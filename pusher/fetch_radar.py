"""拉取并解析 AI News Radar 的公开 24 小时数据。

数据地址（原 learnprompt.github.io 已 301 到自定义域名）：
https://news.learnprompt.pro/data/latest-24h.json
"""
from datetime import datetime, timezone

import requests


def fetch_latest_24h(url, timeout=60, proxies=None):
    resp = requests.get(url, timeout=timeout, proxies=proxies)
    resp.raise_for_status()
    return resp.json()


def freshness(data, max_age_hours=36, now=None):
    """返回 (数据是否可用, 数据年龄小时数)。"""
    generated = datetime.fromisoformat(data["generated_at"].replace("Z", "+00:00"))
    now = now or datetime.now(timezone.utc)
    age = (now - generated).total_seconds() / 3600
    return age <= max_age_hours, age


def normalize(item):
    return {
        "id": item.get("id") or item.get("url"),
        "title": item.get("title_zh") or item.get("title") or "",
        "title_en": item.get("title_en") or "",
        "url": item.get("url", ""),
        "source": item.get("source", ""),
        "label": item.get("ai_label", ""),
        "score": item.get("ai_score", 0.0),
        "tier": item.get("source_tier_rank", 9),
        "tier_label": item.get("source_tier_label", ""),
        "signals": item.get("ai_signals") or [],
        "reason": item.get("recommend_reason_zh") or "",
        # 上游还有文章摘要（summary）与发布时间（published_at），此前两个字段都被丢弃：
        # 2026-10-05 实测 112 条里 summary 非空 23 条、recommend_reason_zh 非空 75 条，
        # 只映射 reason 的结果是 89 条推送时没有任何正文、并且永远不显示时间。
        # 两者取其一后仍覆盖不到的条目，由 pusher/enrich.py 从原文页兜底。
        "summary": item.get("summary") or "",
        "published": item.get("published_at") or item.get("first_seen_at") or "",
        "site_id": item.get("site_id") or "",
    }


def normalize_all(data):
    return [normalize(i) for i in data.get("items_ai", [])]
