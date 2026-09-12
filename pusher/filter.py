"""福利关键词过滤与去重。

中文关键词在去空白文本上做子串匹配（"送 token" ≡ "送token"）；
英文关键词用词边界匹配（容忍复数 s），避免 industrial 误命中 trial。
匹配范围：标题(中/英) + AI 信号词；不含推荐理由（噪音多）。
"""
import re


def _texts(item):
    return " ".join([
        item.get("title", ""),
        item.get("title_en", ""),
        " ".join(item.get("signals") or []),
    ])


def text_blob(item):
    """去空白小写文本，供中文类关键词子串匹配。"""
    return re.sub(r"\s+", "", _texts(item)).lower()


def text_blob_spaced(item):
    """保留单空格的小写文本，供英文词边界匹配。"""
    return re.sub(r"\s+", " ", _texts(item)).lower().strip()


def _ascii_pattern(word):
    """纯 ASCII 关键词编译为词边界正则；含中文的返回 None 走子串匹配。"""
    if not all(ord(c) < 128 for c in word):
        return None
    return re.compile(r"(?<![a-z0-9])" + re.escape(word) + r"s?(?![a-z0-9])")


class WelfareFilter:
    def __init__(self, keywords, exclude_words=()):
        self.exclude = [w.lower() for w in exclude_words if w]
        self.cjk = []
        self.ascii_patterns = []
        for w in keywords:
            if not w:
                continue
            pat = _ascii_pattern(w)
            if pat:
                self.ascii_patterns.append(pat)
            else:
                self.cjk.append(re.sub(r"\s+", "", w).lower())

    def match(self, item):
        cjk_blob = text_blob(item)
        if not cjk_blob:
            return False
        spaced = text_blob_spaced(item)
        if any(w in cjk_blob or w in spaced for w in self.exclude):
            return False
        return (
            any(w in cjk_blob for w in self.cjk)
            or any(p.search(spaced) for p in self.ascii_patterns)
        )

    def hits(self, items):
        return [i for i in items if self.match(i)]


def drop_pushed(items, state):
    """过滤掉已推送过的条目（按 URL 永久去重）。"""
    return [i for i in items if not state.is_pushed(i["url"])]
