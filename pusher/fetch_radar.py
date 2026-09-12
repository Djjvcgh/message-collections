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
    }


def normalize_all(data):
    return [normalize(i) for i in data.get("items_ai", [])]
