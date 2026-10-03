"""Telegram Bot 推送（主渠道）+ 消息渲染。

GitHub Actions 上直连 api.telegram.org；本地调试可通过
TELEGRAM_PROXY 环境变量走代理（如 http://127.0.0.1:7897）。

渲染目标（可读性优先，宁缺毋滥）：
- **标题完整**：换行的推文/论坛帖先合并成一句，长度按标点边界收口，绝不切断词语。
- **摘要倒金字塔**：拆句后按信息密度重排 —— 含价格/折扣/名额/截止日的句子优先，
  其次是首句，最后是背景补充；预算不够时整句丢弃，不切半句。
- **关键数字加粗**：价格、折扣、日期在正文里加粗，扫一眼就能看到。
- **速览行**：抽出的价格/截止时间 + 来源 + 发布时间放最后一行。
- **预算**：正文预算约 350 字（不含标签），长内容按句压缩，短内容不硬凑。
"""
import html
import re
from datetime import datetime, timedelta, timezone

import requests

from .notify_base import NotifyChannel

API = "https://api.telegram.org"
BJT = timezone(timedelta(hours=8))

# 两类即时消息的标识：福利可直接领取，限时情报是错过就没的窗口
KIND_HEADERS = {
    "welfare": "🎁 [福利]",
    "opportunity": "⏳ [限时]",
}
DEFAULT_HEADER = "🎁 [福利]"
DEFAULT_BUDGET = 350
MIN_SUMMARY_BUDGET = 60      # 摘要至少留这么多字，否则还不如不写
TITLE_SOFT_LIMIT = 140       # 超过这个长度就算「长标题」，改用摘要重做标题
HEADLINE_LIMIT = 110         # 重做后的标题长度上限
MIN_REASON_FOR_HEADLINE = 20  # 摘要短于这个长度就不足以当标题
FIRST_SENTENCE_BONUS = 6

# 改写：去掉信源自带的转发腔调，让标题直接说事（只动语气词，不动事实）
_TITLE_REWRITES = (
    (re.compile(r"^(?:我试用了|我试了|试用了|实测了?|体验了?)\s*"), ""),
    (re.compile(r"^(?:别错过|不要错过|不容错过)[，,：:]?\s*(?:这个|这款|这波)?\s*"), ""),
    (re.compile(r"^(?:受[^，,]{0,12}影响[？?])\s*"), ""),
    (re.compile(r"^(?:重磅|突发|快讯|速看|注意)[！!，,：:]?\s*"), ""),
)

# 收口时愿意在这些标点处断开，保证语义完整
_BREAK_CHARS = "。！？!?；;，,、）)】」"
# 摘要拆句：中英文句末标点，保留标点
_SENTENCE_END = re.compile(r"(?<=[。！？!?；;])\s*|(?<=[.!?])\s+(?=[A-Z0-9\u4e00-\u9fff])")

_PRICE = re.compile(
    r"(?:[$￥¥€£]\s?\d[\d,]*(?:\.\d+)?"
    r"|\d[\d,]*(?:\.\d+)?\s?(?:美元|元|块|人民币|港币|日元|欧元)"
    r"|\d[\d,]*(?:\.\d+)?\s?(?:USD|usd|RMB|rmb|EUR|JPY)"
    r"|\d+\s?折|\d+(?:\.\d+)?\s?%|免费)"
)
_DEADLINE = re.compile(
    r"(?:截止|截至|deadline|ends?|until|expires?)[^。！？!?\n]{0,24}"
    r"|\d{1,2}\s?月\s?\d{1,2}\s?日"
    r"|\d{1,2}/\d{1,2}(?:/\d{2,4})?"
    r"|(?:今天|今日|明天|本周|本月|最后一天|最后\s?\d+\s?[天小时])"
    r"|\d{4}-\d{2}-\d{2}"
)


# ---------------------------------------------------------------- 推送渠道

class TelegramChannel(NotifyChannel):
    name = "telegram"

    def __init__(self, token, chat_id, proxy=None, link_preview=False):
        if not token or not chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 未配置")
        self.token = token
        self.chat_id = chat_id
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.link_preview = link_preview

    def send(self, text, parse_mode="HTML"):
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": not self.link_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        resp = requests.post(
            f"{API}/bot{self.token}/sendMessage",
            json=payload,
            timeout=30,
            proxies=self.proxies,
        )
        if resp.status_code >= 400:
            # 带上 Telegram 的错误描述，便于定位（如 can't parse entities）
            raise RuntimeError(f"Telegram API {resp.status_code}: {resp.text[:200]}")


# ---------------------------------------------------------------- 工具

def esc(s):
    return html.escape(s or "", quote=False)


def _get(item, field, default=""):
    if isinstance(item, dict):
        value = item.get(field)
    else:
        value = getattr(item, field, None)
    return default if value is None else value


# ---------------------------------------------------------------- 标题处理

def normalize_title(raw):
    """把多行/带标记的标题整理成一句完整的话，并去掉转发腔调。"""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[\s·…\.]+$", "", text)
    for pattern, repl in _TITLE_REWRITES:
        text = pattern.sub(repl, text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    return text.strip()


def _trim_at_boundary(text, limit, prefer_sentence=False):
    """按标点边界收口，避免切断词语。

    prefer_sentence=True 时优先句末标点（句号/问号/叹号），找不到才退到逗号，
    因为「在逗号处断开」经常留下「如果这堂课卖 400 刀，」这种悬空半句。
    搜索窗口覆盖整个待截断区间，否则短句成不了收口点。
    """
    if len(text) <= limit:
        return text
    window = text[: limit + 1]
    tiers = ("。！？!?", _BREAK_CHARS) if prefer_sentence else (_BREAK_CHARS,)
    for chars in tiers:
        for index in range(len(window) - 1, 0, -1):
            if window[index] in chars:
                return window[: index + 1].rstrip()
    return window[:limit].rstrip() + "…"


def build_title(item, limit=TITLE_SOFT_LIMIT):
    """挑选/生成标题。

    实测（376 条真实条目）11% 的标题超过 140 字，几乎全是 X 推文原文
    （"dot is my favorite openai product so far!…"），既缺主语又常被上游截断。
    这类条目用信源自带的一句话摘要重做标题，语义完整、能一眼看懂；
    短标题一律原样保留，绝不改写事实。
    """
    title = normalize_title(_get(item, "title")) or normalize_title(_get(item, "title_en"))
    if not title:
        return "(无标题)"
    if len(title) <= limit:
        return title

    headline = _headline_from_reason(_get(item, "reason") or _get(item, "summary"))
    if headline and len(headline) <= HEADLINE_LIMIT:
        return headline
    return _trim_at_boundary(title, limit, prefer_sentence=True)


def _headline_from_reason(reason, limit=HEADLINE_LIMIT):
    """用摘要拼一句完整标题：优先首句，不够再补信息量最高的句子。"""
    sentences = split_sentences(reason)
    if not sentences or len(reason) < MIN_REASON_FOR_HEADLINE:
        return ""
    first = sentences[0]
    if len(first) > limit:
        return _trim_at_boundary(first, limit, prefer_sentence=True)
    picked = [first]
    used = len(first)
    rest = sorted(
        enumerate(sentences[1:], start=1),
        key=lambda pair: -score_sentence(pair[1], pair[0]),
    )
    for _, sentence in rest:
        if used + len(sentence) + 1 > limit:
            continue
        picked.append(sentence)
        used += len(sentence) + 1
    return " ".join(picked)


# ---------------------------------------------------------------- 摘要处理

def split_sentences(text):
    """按句末标点拆句，保留标点；没有标点的长句再按分号/逗号切。"""
    text = re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENTENCE_END.split(text) if p and p.strip()]
    out = []
    for part in parts:
        if len(part) <= 140:
            out.append(part)
            continue
        chunks, buf = [], ""
        for piece in re.split(r"(?<=[；;，,])", part):
            if len(buf) + len(piece) > 120 and buf:
                chunks.append(buf)
                buf = piece
            else:
                buf += piece
        if buf:
            chunks.append(buf)
        out.extend(chunks)
    return out


def score_sentence(sentence, index):
    """信息密度打分：价格/期限/资格等硬信息优先，其次是靠前的句子。"""
    score = 0
    if index == 0:
        score += FIRST_SENTENCE_BONUS
    if _PRICE.search(sentence):
        score += 10
    if _DEADLINE.search(sentence):
        score += 8
    if re.search(r"(?:免费|赠送|折扣|优惠|领取|注册|申请|名额|限量|资格)", sentence):
        score += 6
    if re.search(r"(?:官方|announced|确认|回应|宣布)", sentence, re.I):
        score += 4
    if re.search(r"\d", sentence):
        score += 2
    if len(sentence) < 12:
        score -= 4          # 太短的碎片信息量低
    if len(sentence) > 200:
        score -= 2
    score -= index * 0.5    # 原文顺序本身就是一种重要性信号
    return score


def build_summary(text, budget, keep_order=False):
    """按信息密度重排句子并压到预算内；整句取舍，不切半句。"""
    sentences = split_sentences(text)
    if not sentences or budget <= 0:
        return ""
    ranked = list(enumerate(sentences))
    if not keep_order:
        ranked.sort(key=lambda pair: -score_sentence(pair[1], pair[0]))
    picked, used = [], 0
    for index, sentence in ranked:
        cost = len(sentence) + (1 if picked else 0)
        if used + cost > budget:
            continue
        picked.append((index, sentence))
        used += cost
    if not picked:
        return _trim_at_boundary(sentences[0], budget)
    if not keep_order:
        picked.sort(key=lambda pair: pair[0])   # 选完恢复原文顺序，读起来才连贯
    return " ".join(sentence for _, sentence in picked)


# ---------------------------------------------------------------- 速览信息

def extract_facts(*texts):
    """从标题/摘要里抽出价格与截止时间；抽不到留空（不硬凑）。"""
    blob = " ".join(t for t in texts if t)
    price = _PRICE.search(blob)
    deadline = _DEADLINE.search(blob)
    return {
        "price": price.group(0).strip() if price else "",
        "deadline": deadline.group(0).strip() if deadline else "",
    }


def highlight(text):
    """给正文里的价格与日期加粗（先转义再加标签，避免破坏 HTML）。"""
    escaped = esc(text)
    escaped = re.sub(
        r"((?:[$￥¥€£]\s?\d[\d,]*(?:\.\d+)?"
        r"|\d[\d,]*(?:\.\d+)?\s?(?:美元|元|块)"
        r"|\d+\s?折|\d+(?:\.\d+)?\s?%))",
        r"<b>\1</b>",
        escaped,
    )
    escaped = re.sub(
        r"(\d{1,2}\s?月\s?\d{1,2}\s?日|\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{2}-\d{2})",
        r"<b>\1</b>",
        escaped,
    )
    return escaped


def format_published(item):
    """发布时间：当天显示时刻，一周内显示月日，更早不显示。"""
    raw = (_get(item, "extra") or {}).get("published") or ""
    if not raw:
        return ""
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    local = when.astimezone(BJT)
    now = datetime.now(BJT)
    if local.date() == now.date():
        return local.strftime("今天 %H:%M")
    if (now - local).days < 7:
        return local.strftime("%m-%d %H:%M")
    return ""


# ---------------------------------------------------------------- 组装消息

def build_instant_message(item, kind="welfare", budget=DEFAULT_BUDGET):
    """组装一条即时消息。

    结构：`抬头 + 标题` → 引用块摘要（关键数字加粗）→ 速览行（价格/截止/来源/时间）。
    摘要按预算压缩、整句取舍；与标题重复的句子会被去掉（标题本身已用摘要重做时，
    摘要常常就是同一句话，重复展示纯属浪费版面）；抽不到价格与截止时间就不显示。
    """
    header = KIND_HEADERS.get(kind, DEFAULT_HEADER)
    title = build_title(item)
    url = html.escape(_get(item, "url") or "", quote=True)
    src = _get(item, "source") or "原文链接"
    tier_label = _get(item, "tier_label")

    # 预算：先扣标题与速览行的开销，其余留给摘要
    footer_cost = len(src) + 14 + (len(tier_label) + 3 if tier_label else 0)
    summary_budget = budget - len(title) - footer_cost
    reason = (_get(item, "reason") or _get(item, "summary") or "").strip()
    summary = ""
    if reason and summary_budget >= MIN_SUMMARY_BUDGET:
        rest = drop_repeated(reason, title)
        if rest:
            summary = build_summary(rest, summary_budget)

    lines = [f"{header} <b>{esc(title)}</b>"]
    if summary:
        lines.append(f"<blockquote>{highlight(summary)}</blockquote>")

    facts = extract_facts(title, reason)
    meta = [v for v in (facts["price"], facts["deadline"]) if v]
    stamp = format_published(item)
    if stamp:
        meta.append(stamp)
    tail = f'via <a href="{url}">{esc(src)}</a>' if url else f"via {esc(src)}"
    if tier_label:
        tail += esc(f" · {tier_label}")
    if meta:
        tail += " · " + esc(" · ".join(meta))
    lines.append(tail)
    return "\n\n".join(lines)


def drop_repeated(reason, headline):
    """去掉与标题重复（或互为子串）的句子，只留补充信息。"""
    if not headline:
        return reason or ""
    key = _squeeze(headline)
    kept = [s for s in split_sentences(reason) if _squeeze(s) not in key]
    return " ".join(kept)


def _squeeze(text):
    """去空白与标点，用于判断两句是不是同一句。"""
    return re.sub(r"[\s，。；、,.;:!?！？…]+", "", text or "")
