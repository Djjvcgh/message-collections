from pathlib import Path

import pytest

import pusher.watch_pages as wp
from pusher.watch_pages import (
    build_page_message,
    check_pages,
    extract_title,
    fetch_page,
    normalize_content,
)

PAGES = [
    {"name": "a", "url": "https://example.com/a"},
    {"name": "b", "url": "https://example.com/b"},
]

CONTENT = {
    "https://example.com/a": "Title: Page A\n\nMarkdown Content:\n\n价格 8 元",
    "https://example.com/b": "Title: Page B\n\nMarkdown Content:\n\n  内容B  ",
}


def patch_fetch(monkeypatch, mapping):
    calls = []

    def _fetch(url, timeout=45, proxies=None):
        calls.append(url)
        return mapping[url]

    monkeypatch.setattr(wp, "fetch_page", _fetch)
    return calls


def test_normalize_content_collapses_whitespace():
    assert normalize_content(" a\n\n b\t c ") == "a b c"
    assert normalize_content(None) == ""


def test_extract_title_from_jina_output():
    assert extract_title("Title: Hello World\n\nMarkdown Content:\nx") == "Hello World"
    assert extract_title("no title here") is None


def test_check_pages_baseline_first_run(tmp_path, monkeypatch):
    patch_fetch(monkeypatch, CONTENT)
    changes = check_pages(PAGES, tmp_path / "pages", log=lambda *_: None)
    assert changes == []
    # 基线为「指纹 + 字符数」一行文本
    assert (
        (tmp_path / "pages" / "a.txt").read_text(encoding="utf-8").split()[1] == "38"
    )
    assert (tmp_path / "pages" / "b.txt").exists()


def test_check_pages_unchanged_writes_nothing(tmp_path, monkeypatch):
    patch_fetch(monkeypatch, CONTENT)
    check_pages(PAGES, tmp_path / "pages", log=lambda *_: None)
    snap = tmp_path / "pages" / "a.txt"
    before = snap.read_text(encoding="utf-8")
    mtime = snap.stat().st_mtime_ns
    changes = check_pages(PAGES, tmp_path / "pages", log=lambda *_: None)
    assert changes == []
    assert snap.read_text(encoding="utf-8") == before
    assert snap.stat().st_mtime_ns == mtime  # 无变化不写盘，避免 state 空转


def test_check_pages_detects_change_keeps_snapshot_for_caller(tmp_path, monkeypatch):
    patch_fetch(monkeypatch, CONTENT)
    check_pages(PAGES, tmp_path / "pages", log=lambda *_: None)
    changed = dict(CONTENT)
    changed["https://example.com/a"] = (
        "Title: Page A\n\nMarkdown Content:\n\n价格 4 元 降价了"
    )
    patch_fetch(monkeypatch, changed)

    changes = check_pages(PAGES, tmp_path / "pages", log=lambda *_: None)
    assert len(changes) == 1
    ch = changes[0]
    assert ch["name"] == "a"
    assert ch["title"] == "Page A"
    assert ch["old_len"] == 38
    assert ch["new_len"] == 42
    # 快照仍是旧指纹，由调用方在推送成功后写入
    assert "价格" not in ch["snapshot_path"].read_text(encoding="utf-8")
    assert ch["snapshot_path"] == tmp_path / "pages" / "a.txt"

    # 模拟调用方落盘
    ch["snapshot_path"].write_text(ch["snapshot_text"], encoding="utf-8")
    assert ch["snapshot_path"].read_text(encoding="utf-8") == ch["snapshot_text"]


def test_check_pages_fetch_failure_skips_page(tmp_path, monkeypatch):
    check_pages(PAGES, tmp_path / "pages", log=lambda *_: None)

    def _fail(url, timeout=45, proxies=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(wp, "fetch_page", _fail)
    changes = check_pages(PAGES, tmp_path / "pages", log=lambda *_: None)
    assert changes == []  # 单页故障被跳过，不抛出


def test_fetch_page_falls_back_to_direct(monkeypatch):
    def jina_fail(url, timeout, proxies):
        raise RuntimeError("429")

    def direct_ok(url, timeout, proxies):
        return "direct content"

    monkeypatch.setattr(wp, "_fetch_jina", jina_fail)
    monkeypatch.setattr(wp, "_fetch_direct", direct_ok)
    assert fetch_page("https://x.com") == "direct content"


def test_fetch_page_raises_when_both_fail(monkeypatch):
    def fail(url, timeout, proxies):
        raise RuntimeError("down")

    monkeypatch.setattr(wp, "_fetch_jina", fail)
    monkeypatch.setattr(wp, "_fetch_direct", fail)
    with pytest.raises(RuntimeError):
        fetch_page("https://x.com")


def test_build_page_message_layout():
    change = {
        "name": "deepseek-pricing",
        "url": "https://api-docs.deepseek.com/quick_start/pricing",
        "title": "Models & Pricing | DeepSeek API Docs",
        "old_len": 100,
        "new_len": 120,
    }
    msg = build_page_message(change)
    assert "📄 [页面更新] <b>Models &amp; Pricing | DeepSeek API Docs</b>" in msg
    assert "100 → 120 字符" in msg
    assert 'via <a href="https://api-docs.deepseek.com/quick_start/pricing">deepseek-pricing</a>' in msg
