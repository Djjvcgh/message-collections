import json
from datetime import datetime, timezone
from pathlib import Path

from pusher.fetch_radar import freshness, normalize, normalize_all

FIXTURE = Path(__file__).parent / "fixtures" / "radar-sample.json"


def load_data():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_freshness_fresh():
    data = load_data()
    ok, age = freshness(
        data,
        now=datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc),  # 生成后约 2.85 小时
    )
    assert ok
    assert 2.8 < age < 2.9


def test_freshness_stale():
    data = load_data()
    ok, age = freshness(
        data,
        now=datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc),  # 生成后 ~41 小时
    )
    assert not ok
    assert age > 36


def test_normalize_prefers_chinese_title():
    items = normalize_all(load_data())
    zcode = next(i for i in items if i["url"] == "https://example.com/zcode-token")
    assert zcode["title"] == "ZCode 送 token 限时活动开启"
    assert zcode["source"] == "AIbase"
    assert zcode["tier"] == 1
    assert zcode["label"] == "ai_product_update"
    assert "zcode" in zcode["signals"]


def test_normalize_missing_optional_fields():
    items = normalize_all(load_data())
    ads = next(i for i in items if i["url"] == "https://example.com/ads")
    assert ads["title_en"] == ""
    assert ads["signals"] == []
    assert ads["reason"] == ""


def test_normalize_keeps_summary_and_published():
    """上游的 summary 与 published_at 此前被丢弃，正文与时间都展示不出来。"""
    items = normalize_all(load_data())
    zcode = next(i for i in items if i["url"] == "https://example.com/zcode-token")
    assert zcode["published"] == "2026-09-12T01:00:00Z"
    assert zcode["summary"] == ""            # 该 fixture 没有 summary 字段
    assert zcode["reason"] == "ZCode 推出送 token 活动"

    mapped = normalize(
        {
            "id": "x",
            "url": "https://example.com/x",
            "title_zh": "标题",
            "summary": "文章摘要",
            "recommend_reason_zh": "推荐理由",
            "published_at": "2026-10-05T00:00:00Z",
            "site_id": "hackernews",
        }
    )
    assert mapped["summary"] == "文章摘要"
    assert mapped["reason"] == "推荐理由"
    assert mapped["published"] == "2026-10-05T00:00:00Z"
    assert mapped["site_id"] == "hackernews"


def test_apply_body_prefers_article_summary_then_reason():
    """radar 正文优先级：文章摘要 → 推荐理由 → 留空（交给 enrich 抓原文页）。"""
    from pusher.sources.radar import apply_body

    both = apply_body({"summary": " 文章摘要 ", "reason": "推荐理由", "published": "T"})
    assert both["summary"] == "文章摘要"
    assert both["extra"]["body_from"] == "summary"
    assert both["extra"]["published"] == "T"

    only_reason = apply_body({"summary": "", "reason": "推荐理由"})
    assert only_reason["summary"] == "推荐理由"
    assert only_reason["extra"]["body_from"] == "reason"

    empty = apply_body({"summary": "  ", "reason": ""})
    assert empty["summary"] == ""
    assert empty["extra"]["body_from"] == ""
