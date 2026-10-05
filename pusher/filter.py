"""福利 / 限时情报分类与关键词命中。

四张词表（config/keywords.yml）：
- ``welfare_keywords``：**硬福利词**，命中即成立（免费额度、赠送、折扣码……）
- ``weak_welfare_keywords``：**宽词**（优惠、折扣、福利、学生、免费使用……）。
  单独出现不算福利——2026-10-05 实测线上推的 3 条全是这类词捞进来的
  行业资讯（「Gemini 将结束 Flash 和 Pro 模型的免费使用」命中 `免费使用`、
  「我让AI教学生写前端」命中 `学生`），所以必须与「硬福利证据」共现才成立。
- ``opportunity_keywords`` / ``weak_opportunity_keywords``：限时窗口，
  弱词同样要求有可领取动作或窗口证据。
- ``exclude_words``：命中即整条丢弃。

命中顺序：排除词 → 硬福利 → 弱福利（需硬信号）→ 硬限时 →
弱限时（需可领取动作）→ **派生限时**（有硬信号 + 有窗口 = 错过就没）。
同时命中福利与限时时按福利处理（信息量更大）。

匹配范围：标题(中/英) + 信号词 + 短摘要；摘要超过 ``max_reason_chars``
视为整篇正文，不参与匹配。**弱词不匹配 title_en**：实测英文标题里的
students / discount 会把 AI 行业新闻整条捞进来。
"""
import re

from .facts import (
    has_claim_signal,
    has_offer_signal,
    has_window_signal,
    mask_negated,
)
from .state import title_key

KIND_WELFARE = "welfare"
KIND_OPPORTUNITY = "opportunity"

# 摘要超过这个长度视为整篇文章正文，不参与关键词匹配。
# 实测（2026-10-03 真实信源）：门槛从 300 收到 120，命中从 51 条降到 24 条，
# 丢掉的全是「TechCrunch 门票 / 二手耳机 / Udemy 课程」这类正文撞词，
# 真福利（折扣券、免费额度、开放注册）都在标题或短摘要里命中。
MAX_REASON_CHARS = 120

# 中文短语替换：正文说「某人免费开放了代码」≠ 读者能领福利。
# 标题/摘要里出现这些说法时先抹掉，避免 免费/赠送 被蹭。
_BENIGN_PHRASES = (
    "免费课",
    "免费公开课",
    "免费讲座",
    "免费直播",
    "开源免费",
    "免费开放代码",
    "免费开放",
    "免费试用报告",
)
_PHRASE_MASK = "〇"


def _get(item, field, default=""):
    if hasattr(item, field):
        value = getattr(item, field)
    else:
        value = item.get(field)
    return value if value is not None else default


def head_fields(item):
    """标题类字段：命中即判定（信噪比高）。"""
    return [
        _get(item, "title"),
        _get(item, "title_en"),
        " ".join(_get(item, "signals", []) or []),
    ]


def mask_benign(parts):
    """抹掉已知的「看着像福利其实不是」说法，避免宽词被蹭。

    两层：字面短语（免费公开课）+ 否定语境（将结束…免费使用、
    不再免费、开始收费），后者与 ``facts.mask_negated`` 同一套规则，
    所以分类层与事实层对「福利正在消失」的判断永远一致。
    """
    out = []
    for part in parts:
        text = mask_negated(part or "")
        for phrase in _BENIGN_PHRASES:
            text = text.replace(phrase, _PHRASE_MASK)
        out.append(text)
    return out


def _short_reason(item, max_reason_chars):
    """短摘要才参与匹配；过长（整篇正文）或按信源要求关闭时返回空串。"""
    reason = _get(item, "reason")
    if not _get(item, "match_summary", True):
        return ""
    if len(reason) > max_reason_chars:
        return ""
    return reason


def text_fields(item, max_reason_chars=MAX_REASON_CHARS):
    """标题 + 短摘要：摘要过长（整篇正文）时丢弃，避免误报。"""
    return mask_benign(head_fields(item)) + [_short_reason(item, max_reason_chars)]


def weak_fields(item, max_reason_chars=MAX_REASON_CHARS):
    """弱词匹配范围：中文标题 + 信号词 + 短摘要，**不含英文标题**。

    实测：radar 的 title_en「AI is eroding … the trust between faculty and
    students」让一条 AI 行业新闻命中了英文弱词 student。
    """
    parts = [_get(item, "title"), " ".join(_get(item, "signals", []) or [])]
    return mask_benign(parts) + [_short_reason(item, max_reason_chars)]


def text_blob(parts):
    """去空白小写文本，供中文类关键词子串匹配。"""
    return re.sub(r"\s+", "", " ".join(parts)).lower()


def text_blob_spaced(parts):
    """保留单空格的小写文本，供英文词边界匹配。"""
    return re.sub(r"\s+", " ", " ".join(parts)).lower().strip()


def _ascii_pattern(word):
    """纯 ASCII 关键词编译为词边界正则；含中文的返回 None 走子串匹配。"""
    if not all(ord(c) < 128 for c in word):
        return None
    return re.compile(r"(?<![a-z0-9])" + re.escape(word) + r"s?(?![a-z0-9])")


class _WordSet:
    """一组关键词：中文走子串、英文走词边界。"""

    def __init__(self, keywords=()):
        self.cjk = []
        self.patterns = []
        for w in keywords or []:
            if not w:
                continue
            pat = _ascii_pattern(w)
            if pat:
                self.patterns.append(pat)
            else:
                self.cjk.append(re.sub(r"\s+", "", w).lower())

    def __bool__(self):
        return bool(self.cjk or self.patterns)

    def matches(self, parts):
        blob = text_blob(parts)
        if not blob:
            return False
        if any(w in blob for w in self.cjk):
            return True
        spaced = text_blob_spaced(parts)
        return any(p.search(spaced) for p in self.patterns)


class WelfareFilter:
    """命中判定 + 分类。exclude_words 优先级最高，命中即整条丢弃。"""

    def __init__(
        self,
        welfare_keywords=(),
        opportunity_keywords=(),
        exclude_words=(),
        weak_welfare_keywords=(),
        weak_opportunity_keywords=(),
        max_reason_chars=MAX_REASON_CHARS,
    ):
        self.welfare = _WordSet(welfare_keywords)
        self.opportunity = _WordSet(opportunity_keywords)
        self.weak_welfare = _WordSet(weak_welfare_keywords)
        self.weak_opportunity = _WordSet(weak_opportunity_keywords)
        self.exclude = [w.lower() for w in (exclude_words or []) if w]
        self.max_reason_chars = max_reason_chars

    def _excluded(self, parts):
        blob = text_blob(parts)
        spaced = text_blob_spaced(parts)
        return any(w in blob or w in spaced for w in self.exclude)

    def classify(self, item):
        """返回 'welfare' / 'opportunity'，不命中返回 None。"""
        parts = text_fields(item, self.max_reason_chars)
        if self._excluded(parts):
            return None
        if self.welfare.matches(parts):
            return KIND_WELFARE
        weak_parts = weak_fields(item, self.max_reason_chars)
        if self.weak_welfare and self.weak_welfare.matches(weak_parts):
            # 宽词必须与硬福利证据共现，否则就是行业资讯
            if has_offer_signal(item):
                return KIND_WELFARE
        if self.opportunity.matches(parts):
            return KIND_OPPORTUNITY
        if self.weak_opportunity and self.weak_opportunity.matches(weak_parts):
            if has_offer_signal(item) or has_claim_signal(item):
                return KIND_OPPORTUNITY
        # 派生限时：既拿得到东西、又有窗口（截止/名额/先到先得），就是「错过没了」
        if has_offer_signal(item) and has_window_signal(item):
            return KIND_OPPORTUNITY
        return None

    def match(self, item):
        return self.classify(item) is not None

    # 兼容旧调用：命中任意一类即算命中
    def hits(self, items):
        return [i for i in items if self.match(i)]


def dedupe(items, state):
    """过滤已推送过的条目，并折叠本轮内部重复。

    两把尺子：
    - state：URL 哈希或标题归一化哈希命中即已推送过（跨轮去重）
    - 本轮：URL 相同、或标题归一化指纹相同（多源转载同一活动）只留第一条
    """
    kept = []
    seen_urls = set()
    seen_titles = set()
    for item in items:
        url = _get(item, "url")
        title = _get(item, "title")
        if state is not None and state.is_pushed(url, title):
            continue
        tkey = title_key(title)
        if (url and url in seen_urls) or (tkey and tkey in seen_titles):
            continue
        if url:
            seen_urls.add(url)
        if tkey:
            seen_titles.add(tkey)
        kept.append(item)
    return kept
