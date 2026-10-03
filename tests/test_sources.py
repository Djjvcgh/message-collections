from pusher.sources import SOURCE_TYPES
from pusher.sources.base import Item, as_item, load_sources


class FakeSource:
    type = "fake"

    def __init__(self, source_id, options=None):
        self.id = source_id
        self.options = options or {}
        if self.options.get("explode"):
            raise ValueError("配置坏了")

    def fetch(self, log=print, **ctx):
        return [Item(title="免费额度", url="https://x.com/1", source_id=self.id)]


def cfg(*specs):
    return {"sources": list(specs)}


def test_registry_has_all_documented_types():
    assert set(SOURCE_TYPES) == {"radar", "rss", "telegram"}


def test_load_sources_builds_in_order():
    sources = load_sources(
        cfg(
            {"id": "a", "type": "radar", "options": {"url": "https://x"}},
            {"id": "b", "type": "rss", "options": {"url": "https://y"}},
        ),
        log=lambda *_: None,
    )
    assert [s.id for s in sources] == ["a", "b"]


def test_disabled_and_unknown_types_are_skipped():
    logs = []
    sources = load_sources(
        cfg(
            {"id": "off", "type": "radar", "enabled": False, "options": {"url": "https://x"}},
            {"id": "weird", "type": "nope", "options": {}},
            {"id": "ok", "type": "rss", "options": {"url": "https://y"}},
        ),
        log=logs.append,
    )
    assert [s.id for s in sources] == ["ok"]
    assert any("disabled" in m for m in logs)
    assert any("unknown type" in m for m in logs)


def test_only_filter_selects_requested_ids():
    sources = load_sources(
        cfg(
            {"id": "a", "type": "rss", "options": {"url": "https://x"}},
            {"id": "b", "type": "rss", "options": {"url": "https://y"}},
        ),
        only=["b"],
        log=lambda *_: None,
    )
    assert [s.id for s in sources] == ["b"]


def test_broken_options_do_not_break_other_sources():
    logs = []
    sources = load_sources(
        cfg(
            {"id": "bad", "type": "rss", "options": {}},
            {"id": "good", "type": "rss", "options": {"url": "https://y"}},
        ),
        log=logs.append,
    )
    assert [s.id for s in sources] == ["good"]
    assert any("init failed" in m for m in logs)


def test_load_sources_uses_fake_registry_entry(monkeypatch):
    import pusher.sources as pkg

    monkeypatch.setitem(pkg.SOURCE_TYPES, "fake", FakeSource)
    logs = []
    sources = load_sources(
        cfg(
            {"id": "f1", "type": "fake", "options": {}},
            {"id": "f2", "type": "fake", "options": {"explode": True}},
        ),
        log=logs.append,
    )
    assert [s.id for s in sources] == ["f1"]
    assert any("init failed" in m for m in logs)


def test_as_item_accepts_legacy_dict():
    item = as_item(
        {
            "title": "ZCode 送 token",
            "url": "https://x.com/z",
            "source": "AIbase",
            "label": "ai_product_update",
            "reason": "摘要",
            "tier": 1,
            "score": 0.9,
            "signals": ["zcode"],
        }
    )
    assert item.title == "ZCode 送 token"
    assert item.category == "ai_product_update"
    assert item.reason == "摘要"
    assert item.sort_key() == (1, -0.9)


def test_as_item_is_idempotent():
    original = Item(title="a", url="https://x")
    assert as_item(original) is original
