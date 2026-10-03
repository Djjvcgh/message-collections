from pusher.state import State, title_key, url_hash


def test_url_hash_stable():
    assert url_hash("https://a.com") == url_hash("https://a.com")
    assert url_hash("https://a.com") != url_hash("https://b.com")


def test_push_roundtrip_by_url(tmp_path):
    path = tmp_path / "state.json"
    st = State.load(path)  # 文件不存在也能用
    assert not st.is_pushed("https://a.com")
    st.mark_pushed("https://a.com", when="2026-09-12T00:00:00+00:00")
    st.save(path)

    st2 = State.load(path)
    assert st2.is_pushed("https://a.com")


def test_push_roundtrip_by_title(tmp_path):
    path = tmp_path / "state.json"
    st = State()
    st.mark_pushed("", "某站开放注册，无需邀请码", when="2026-09-12T00:00:00+00:00")
    st.save(path)

    st2 = State.load(path)
    # 换个 URL 但标题相同，仍视为重复（多源转载场景）
    assert st2.is_pushed("https://mirror.example.com/post", "某站开放注册，无需邀请码")
    assert not st2.is_pushed("https://mirror.example.com/post", "另一个活动")


def test_title_key_ignores_noise():
    a = title_key("【福利】某站 免费 领取 1 年域名！！！")
    b = title_key("某站免费领取1年域名")
    assert a == b
    assert a != title_key("某站免费领取2年域名")


def test_title_key_empty_title_is_falsy():
    assert not title_key("")
    assert not title_key("！！！  ###")


def test_legacy_digests_field_is_dropped_on_load(tmp_path):
    import json

    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "pushed": {"abc": "2026-09-12T00:00:00+00:00"},
                "digests": {"2026-09-12_morning": "2026-09-12T01:00:00+00:00"},
            }
        ),
        encoding="utf-8",
    )
    st = State.load(path)
    st.save(path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "digests" not in saved  # 日报功能已下线，旧字段读入即丢弃
    assert saved["pushed"]


def test_prune_keeps_recent_only():
    st = State()
    st.mark_pushed("https://old.com", when="2026-09-01T00:00:00+00:00")
    st.mark_pushed("https://new.com", when="2026-09-12T00:00:00+00:00")
    st.prune(days=7, now="2026-09-12T00:00:00+00:00")
    assert not st.is_pushed("https://old.com")
    assert st.is_pushed("https://new.com")
