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
