"""运行状态：已推送条目去重记录。

state.json 随仓库 commit 持久化，既是去重数据库，
也让仓库持续有提交、避免 GitHub 停用 60 天无活动的定时任务。

去重键有两个：URL 哈希与标题归一化哈希。多源聚合后同一条福利
常被多个渠道转载、标题仅标点或措辞不同，只靠 URL 会重复推送。
"""
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

# 归一化时剔除的噪声：空白、标点、常见前后缀标记
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
# 【福利】/ [NodeSeek] 这类方括号前缀
_BRACKET_PREFIX = re.compile(r"^[\[【(（][^\]】)）]{1,16}[\]】)）]\s*")
# 「NodeSeek：」「Linux.do: 」这类来源前缀，剥掉后才能和多源转载的同一活动对齐。
# 只在剩余部分仍然够长时才剥，避免把「V2EX：出鸡」这类短标题整体误伤。
_SOURCE_PREFIX = re.compile(r"^[^：:]{1,12}[：:]\s*")
MIN_TITLE_AFTER_PREFIX = 8


def url_hash(url: str) -> str:
    return hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:16]


def title_key(title: str) -> str:
    """标题归一化指纹：大小写、空白、标点、emoji、来源前缀差异不影响比对。"""
    text = unicodedata.normalize("NFKC", title or "").lower().strip()
    text = _BRACKET_PREFIX.sub("", text)
    stripped = _SOURCE_PREFIX.sub("", text)
    if len(stripped) >= MIN_TITLE_AFTER_PREFIX:
        text = stripped
    text = _PUNCT.sub("", _WS.sub("", text))
    if not text:
        return ""
    return "t" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:15]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


class State:
    def __init__(self, data=None):
        data = data or {}
        # 老 state.json 里的 digests 字段属于已下线的日报功能，读入即丢弃
        self.data = {"pushed": dict(data.get("pushed") or {})}

    @classmethod
    def load(cls, path):
        try:
            with open(path, encoding="utf-8") as f:
                return cls(json.load(f))
        except FileNotFoundError:
            return cls()

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)

    def is_pushed(self, url: str = "", title: str = "") -> bool:
        """URL 或标题任一已推送过即视为重复。"""
        if url and url_hash(url) in self.data["pushed"]:
            return True
        key = title_key(title)
        return bool(key) and key in self.data["pushed"]

    def mark_pushed(self, url: str = "", title: str = "", when=None):
        stamp = when or _now()
        if url:
            self.data["pushed"][url_hash(url)] = stamp
        key = title_key(title)
        if key:
            self.data["pushed"][key] = stamp

    def prune(self, days=7, now=None):
        """清理超过 days 天的记录，防止 state 无限膨胀。"""
        cutoff = _parse(now or _now()) - timedelta(days=days)
        self.data["pushed"] = {
            h: t for h, t in self.data["pushed"].items() if _parse(t) >= cutoff
        }
