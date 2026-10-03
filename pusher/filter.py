"""福利 / 限时情报分类与关键词命中。

两类词表（config/keywords.yml）：
- welfare_keywords：可直接领取的福利（免费额度、赠送、优惠码……）
- opportunity_keywords：不是送东西、但错过就没了的限时窗口（开放注册、截止、限量……）

一个条目同时命中两类时按福利处理（信息量更大）。
中文关键词在去空白文本上做子串匹配（"送 token" ≡ "送token"）；
英文关键词用词边界匹配（容忍复数 s），避免 industrial 误命中 trial。
匹配范围：标题(中/英) + 信号词 + 短摘要；过长的摘要是整篇正文，
关键词撞车概率高，只在标题毫无命中时才作为兜底。
"""
import re

from .state import title_key

KIND_WELFARE = "welfare"
KIND_OPPORTUNITY = "opportunity"

# 摘要超过这个长度视为正文，不参与关键词匹配（RSS 常把全文塞进 description）
MAX_REASON_CHARS = 300


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


def text_fields(item):
    """标题 + 短摘要：摘要过长（整篇正文）时丢弃，避免误报。"""
    reason = _get(item, "reason")
    if len(reason) > MAX_REASON_CHARS:
        reason = ""
    return head_fields(item) + [reason]


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

    def __init__(self, welfare_keywords=(), opportunity_keywords=(), exclude_words=()):
        self.welfare = _WordSet(welfare_keywords)
        self.opportunity = _WordSet(opportunity_keywords)
        self.exclude = [w.lower() for w in (exclude_words or []) if w]

    def _excluded(self, parts):
        blob = text_blob(parts)
        spaced = text_blob_spaced(parts)
        return any(w in blob or w in spaced for w in self.exclude)

    def classify(self, item):
        """返回 'welfare' / 'opportunity'，不命中返回 None。"""
        head = head_fields(item)
        parts = text_fields(item)
        if self._excluded(parts):
            return None
        if self.welfare.matches(head) or self.welfare.matches(parts):
            return KIND_WELFARE
        if self.opportunity.matches(head) or self.opportunity.matches(parts):
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
