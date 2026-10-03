"""Telegram Bot 推送（主渠道）+ 消息渲染。

GitHub Actions 上直连 api.telegram.org；本地调试可通过
TELEGRAM_PROXY 环境变量走代理（如 http://127.0.0.1:7897）。

消息体裁（2026-10-03 按实际观感重做）：
- **标题**：一句完整的事件陈述；换行的推文/论坛帖先合并，去掉源站冗余标记
  与转发腔调；过长或残缺时用信源自带摘要重做标题。
- **细节行**：把折扣码、价格、截止、资格、形式逐行列出（`▎价格：…`），
  抽不到的行不占位。这一层是「一眼能用」的信息，优先于叙述。
- **摘要**：只放细节行没覆盖到的句子，长段落压缩，短内容不硬凑。
- **速览行**：`via 来源 · 截止 · 时间`，抽不到就不显示。
- **预算**：正文约 350 字，超长先砍摘要、再砍细节行。
"""
import html
import re
from datetime import datetime, timedelta, timezone

import requests

from .facts import extract_facts, is_actionable  # noqa: F401 — is_actionable 供 run 调用
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
MAX_DETAIL_LINES = 5         # 细节行上限，避免比正文还长

# 标题开头的方括号标记（【福利】【免费赠送】【慢讯】…）：信息量低，去掉
_BRACKET_TAG = re.compile(r"^\s*[\[【（(][^\]】)）]{1,12}[\]】)）]\s*")
# 方括号里属于「活动性质说明」的词，无脑去掉
_TAG_NOISE = re.compile(
    r"^(?:福利|免费赠送|免费送|限时|优惠|活动|分享|推荐|慢讯|快讯|重磅|首发|搬运|转发|"
    r"抽奖|开源|教程|求助|讨论|提问|已结束)$"
)

# 叙事/评论腔调的开头：资讯类摘要常写成「想知道…读这篇」「我试用了…」，
# 这类句子对「一眼看懂发生了什么」没有帮助，只在摘要里剔除（标题另有规则）。
_NARRATIVE = re.compile(
    r"^(?:想知道|感兴趣的话|感兴趣的可以|值得一读|值得读|这篇内容值得|"
    r"如果你|我试用了|我试了|作者称|作者表示|原文还|读这篇)"
)

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
    """把多行/带标记的标题整理成一句完整的话。

    实测场景：V2EX 的 `[免费赠送] 内网云 2026 国庆活动余额兑换券` 会和我们自己的
    `🎁 [福利]` 叠成两套标记，所以源标题开头的方括号活动标记要逐个去掉；
    但若整条标题都由标记构成，则保留原样，不能清空。
    """
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[\s·…\.]+$", "", text)

    stripped = text
    while True:
        match = _BRACKET_TAG.match(stripped)
        if not match:
            break
        stripped = stripped[match.end():]
    if stripped.strip():
        text = stripped.strip()

    # 逐轮剥离：剥掉「受裁员影响？」后，「别错过这个」才会露出来成为新的开头
    for _ in range(4):
        changed = False
        for pattern, repl in _TITLE_REWRITES:
            new_text = pattern.sub(repl, text)
            if new_text != text:
                text = new_text
                changed = True
        if not changed:
            break
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    # 半角逗号/冒号夹在汉字之间是源站排版习惯（「优惠,9 设备永久授权」），
    # 转发到 Telegram 观感差，换成中文标点
    text = re.sub(r"(?<=[\u4e00-\u9fff])[,;:](?=[\u4e00-\u9fff0-9])", "，", text)
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


# ---------------------------------------------------------------- 高亮

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

    结构：`抬头 + 标题` → 细节行（▎价格 / ▎折扣码 / ▎截止 …）→ 尾部摘要 → 速览行。
    细节行是「一眼能用」的硬信息，优先保证；摘要只放细节行没覆盖到的句子；
    预算不够时先砍摘要、再砍细节行，标题永不砍。
    """
    header = KIND_HEADERS.get(kind, DEFAULT_HEADER)
    title = build_title(item)
    url = html.escape(_get(item, "url") or "", quote=True)
    src = _get(item, "source") or "原文链接"
    tier_label = _get(item, "tier_label")
    facts_only = _get(item, "layout") == "facts_only"

    # 预留速览行开销，其余给细节行与摘要
    footer_cost = len(src) + 16 + (len(tier_label) + 3 if tier_label else 0)
    body_budget = max(budget - len(title) - footer_cost, 0)

    reason = (_get(item, "reason") or _get(item, "summary") or "").strip()
    facts = extract_facts(f"{title} {reason}")
    squeezed_title = _squeeze(title)
    fresh = [(label, value) for label, value in facts if _squeeze(value) not in squeezed_title]

    # 1) 尾部摘要。两种情况不再展示：
    #    facts_only 不倒正文；标题本身就是这段摘要改写的（长标题场景）也不重复——
    #    否则用户看到的会是同一句话说两遍。
    summary = ""
    reason_is_headline = bool(reason) and _squeeze(reason) == _squeeze(title.replace("…", ""))
    if not facts_only and not reason_is_headline and reason and body_budget >= MIN_SUMMARY_BUDGET:
        covered = " ".join([title] + [value for _, value in fresh])
        rest = drop_repeated(reason, covered)
        rest = drop_narrative(rest)
        if rest:
            summary = build_summary(rest, body_budget)

    # 2) 细节行：把摘要里没交代的硬信息单独列出。
    #    折扣码是「这条消息最该被看到的东西」，即使摘要里也出现过，也强制单独占一行
    #    （摘要会被压缩，码混在长段落里很难扫到）；其余项重复则不占行。
    squeezed_summary = _squeeze(summary)
    detail_lines = []
    for label, value in fresh:
        squeezed_value = _squeeze(value)
        if label != "折扣码" and squeezed_value and squeezed_value in squeezed_summary:
            continue
        if len(detail_lines) >= MAX_DETAIL_LINES:
            break
        line = f"▎{label}：{highlight(value)}"
        if sum(len(x) for x in detail_lines) + len(line) > body_budget:
            break
        detail_lines.append(line)

    lines = [f"{header} <b>{esc(title)}</b>"]
    lines.extend(detail_lines)
    if summary:
        lines.append(f"<blockquote>{highlight(summary)}</blockquote>")

    # 3) 速览行：只放还没出现过的信息
    #    比对范围包含标题——例：标题写「60 元」、细节行给「¥59.20（到手价）」时，
    #    速览行不该再把金额列第三遍
    shown = _squeeze(
        " ".join(detail_lines).replace("<b>", "").replace("</b>", "") + " " + title
    )
    meta = []
    deadline = next((v for label, v in facts if label == "截止"), "")
    if deadline and _squeeze(deadline) not in shown and _squeeze(deadline) not in _squeeze(summary):
        meta.append(deadline)
    price = next((v for label, v in facts if label == "价格"), "")
    price_key = _squeeze(price.split("（")[0])   # 「¥59.20（到手价）」→「¥59.20」
    if price_key and price_key not in shown and price_key not in _squeeze(summary):
        meta.append(price)
    # 速览行自身也要去重：截止与价格抽到同一串时不重复列出
    deduped, seen_meta = [], set()
    for value in meta:
        key = _squeeze(value)
        if key and key not in seen_meta:
            seen_meta.add(key)
            deduped.append(value)
    meta = deduped
    stamp = format_published(item)
    if stamp:
        meta.append(stamp)
    tail = f'via <a href="{url}">{esc(src)}</a>' if url else f"via {esc(src)}"
    if tier_label:
        tail += esc(f" · {tier_label}")
    if meta:
        tail += " · " + esc(" · ".join(meta))
    lines.append(tail)
    return "\n".join(lines)


def drop_repeated(reason, headline):
    """去掉与标题重复（或互为子串）的句子，只留补充信息。"""
    if not headline:
        return reason or ""
    key = _squeeze(headline)
    kept = [s for s in split_sentences(reason) if _squeeze(s) not in key]
    return " ".join(kept)


def drop_narrative(reason):
    """剔除纯评论腔调的句子（「想知道…读这篇」），它们不传达事件本身。"""
    kept = [s for s in split_sentences(reason) if not _NARRATIVE.match(s.strip())]
    return " ".join(kept) if kept else (reason or "")


def _squeeze(text):
    """去空白与标点，用于判断两句是不是同一句。"""
    return re.sub(r"[\s，。；、,.;:!?！？…]+", "", text or "")
