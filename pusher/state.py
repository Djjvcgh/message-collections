"""运行状态：已推送条目与日报记录。

state.json 随仓库 commit 持久化，既是去重数据库，
也让仓库持续有提交、避免 GitHub 停用 60 天无活动的定时任务。
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone


def url_hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


class State:
    def __init__(self, data=None):
        self.data = data or {"pushed": {}, "digests": {}}

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

    def is_pushed(self, url: str) -> bool:
        return url_hash(url) in self.data["pushed"]

    def mark_pushed(self, url: str, when=None):
        self.data["pushed"][url_hash(url)] = when or _now()

    def has_digest(self, key: str) -> bool:
        return key in self.data["digests"]

    def mark_digest(self, key: str, when=None):
        self.data["digests"][key] = when or _now()

    def prune(self, days=7, now=None):
        """清理超过 days 天的记录，防止 state 无限膨胀。"""
        cutoff = _parse(now or _now()) - timedelta(days=days)
        self.data["pushed"] = {
            h: t for h, t in self.data["pushed"].items() if _parse(t) >= cutoff
        }
        self.data["digests"] = {
            k: t for k, t in self.data["digests"].items() if _parse(t) >= cutoff
        }
