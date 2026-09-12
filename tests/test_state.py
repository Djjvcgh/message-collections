import json
from pathlib import Path

from pusher.state import State, url_hash

FIXTURE = Path(__file__).parent / "fixtures" / "radar-sample.json"


def test_url_hash_stable():
    assert url_hash("https://a.com") == url_hash("https://a.com")
    assert url_hash("https://a.com") != url_hash("https://b.com")


def test_push_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    st = State.load(path)  # 文件不存在也能用
    assert not st.is_pushed("https://a.com")
    st.mark_pushed("https://a.com", when="2026-09-12T00:00:00+00:00")
    st.save(path)

    st2 = State.load(path)
    assert st2.is_pushed("https://a.com")


def test_digest_key(tmp_path):
    st = State()
    assert not st.has_digest("2026-09-12_morning")
    st.mark_digest("2026-09-12_morning", when="2026-09-12T01:00:00+00:00")
    assert st.has_digest("2026-09-12_morning")


def test_prune_keeps_recent_only():
    st = State()
    st.mark_pushed("https://old.com", when="2026-09-01T00:00:00+00:00")
    st.mark_pushed("https://new.com", when="2026-09-12T00:00:00+00:00")
    st.mark_digest("2026-09-01_morning", when="2026-09-01T01:00:00+00:00")
    st.prune(days=7, now="2026-09-12T00:00:00+00:00")
    assert not st.is_pushed("https://old.com")
    assert st.is_pushed("https://new.com")
    assert not st.has_digest("2026-09-01_morning")
