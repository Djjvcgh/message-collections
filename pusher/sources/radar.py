"""radar 信源：AI News Radar 公开 24 小时 JSON。

对外仍是普通信源之一：不再把「AI 相关」当作推送前置门槛，
过滤交给统一的关键词层；radar 自带的分层 / 评分只用于排序。
"""
from ..fetch_radar import fetch_latest_24h, freshness, normalize_all
from .base import as_item


class RadarSource:
    type = "radar"

    def __init__(self, source_id, options=None):
        options = options or {}
        self.id = source_id
        self.url = options.get("url") or options.get("latest_24h_url")
        if not self.url:
            raise ValueError("radar 信源需要 options.url")
        self.max_age_hours = float(options.get("max_age_hours", 36))
        self.limit = int(options.get("limit", 0))
        # radar 的推荐理由是整段资讯摘要，撞词极多（TechCrunch 门票、Meta 开源
        # 都会命中「免费/优惠」），默认只按标题判定；需要摘要兜底时配 true
        self.match_summary = bool(options.get("match_summary", False))

    def fetch(self, log=print, **ctx):
        data = fetch_latest_24h(self.url)
        ok, age = freshness(data, self.max_age_hours)
        if not ok:
            log(f"[source:{self.id}] stale ({age:.1f}h > {self.max_age_hours}h), skipped")
            return []
        items = []
        for raw in normalize_all(data):
            # normalize() 把 radar 的中文推荐理由放在 reason 里，
            # Item 的统一摘要字段是 summary，这里显式搬运，避免推送时摘要空白
            raw["summary"] = raw.get("reason") or ""
            raw["match_summary"] = self.match_summary
            item = as_item(raw)
            item.source_id = self.id
            items.append(item)
        if self.limit:
            items = items[: self.limit]
        log(f"[source:{self.id}] {len(items)} items (age {age:.1f}h)")
        return items


__all__ = ["RadarSource"]
