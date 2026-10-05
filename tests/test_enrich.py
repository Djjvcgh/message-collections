"""正文兜底（pusher/enrich.py）的用例：抽取优先级、解码、缓存、上限、降级。"""
from pusher.enrich import (
    body_for,
    cache_key,
    enrich_items,
    extract_body,
    fetch_body,
    load_cache,
    save_cache,
)
from pusher.sources.base import Item


class FakeResponse:
    def __init__(self, text="", status_code=200, content=None):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8") if content is None else content


class Recorder:
    """记录调用次数的假 requests.get。"""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.response


def test_extract_body_prefers_og_description():
    page = (
        '<html><head><meta property="og:description" content="OG 描述的正文">'
        '<meta name="description" content="普通描述"></head>'
        "<body><p>这是一段足够长的正文段落，用来兜底测试。</p></body></html>"
    )
    assert extract_body(page) == "OG 描述的正文"


def test_extract_body_falls_back_to_description_then_paragraph():
    assert extract_body('<meta name="description" content="只有普通描述">') == "只有普通描述"
    paragraph = "<p>这是首个足够长的段落，应该被当成正文兜底使用。</p>"
    assert extract_body(f"<html><body>{paragraph}</body></html>") == (
        "这是首个足够长的段落，应该被当成正文兜底使用。"
    )


def test_extract_body_ignores_tiny_paragraph_and_scripts():
    page = "<p>短</p><script>var x = '很长的一段脚本文字，绝不能被当成正文'</script>"
    assert extract_body(page) == ""


def test_extract_body_handles_broken_html():
    assert extract_body("<meta property='og:description' content='半截页面也要能取出来'><p") == (
        "半截页面也要能取出来"
    )
    assert extract_body("") == ""


def test_fetch_body_decodes_gbk_pages():
    """中文站点常不带 charset，requests 会猜成 ISO-8859-1 变乱码。"""
    html = '<meta name="description" content="中文摘要要能正确解码">'
    response = FakeResponse(content=html.encode("gb18030"))
    assert fetch_body("https://x.com/1", get=Recorder(response)) == "中文摘要要能正确解码"


def test_fetch_body_degrades_silently():
    assert fetch_body("https://x.com/1", get=Recorder(error=RuntimeError("boom"))) == ""
    assert fetch_body("https://x.com/1", get=Recorder(FakeResponse(status_code=404))) == ""


def test_body_for_caches_including_failures(tmp_path):
    getter = Recorder(FakeResponse(status_code=500))
    cache = {}
    assert body_for("https://x.com/1", cache, get=getter) == ""
    assert body_for("https://x.com/1", cache, get=getter) == ""
    assert len(getter.calls) == 1, "抓不到也要记账，否则每轮都重试失败页面"
    assert cache_key("https://x.com/1") in cache


def test_enrich_items_fills_only_missing_bodies_and_respects_budget():
    page = '<meta property="og:description" content="从原文页补来的正文">'
    getter = Recorder(FakeResponse(page))
    items = [
        Item(title="已有正文", url="https://x.com/has", summary="源数据自带摘要"),
        Item(title="缺正文 A", url="https://x.com/a"),
        Item(title="缺正文 B", url="https://x.com/b"),
    ]
    filled = enrich_items(items, {}, get=getter, max_items=1, log=lambda *_: None)
    assert filled == 1
    assert items[1].summary == "从原文页补来的正文"
    assert items[1].extra["body_from"] == "page"
    assert items[2].summary == ""            # 超出预算的不抓
    assert getter.calls == ["https://x.com/a"]


def test_enrich_items_without_url_is_a_miss():
    items = [Item(title="没有链接", url="")]
    assert enrich_items(items, {}, get=Recorder(FakeResponse()), log=lambda *_: None) == 0


def test_cache_roundtrip(tmp_path):
    path = tmp_path / "bodies.json"
    save_cache({"k": "正文"}, path)
    assert load_cache(path) == {"k": "正文"}
    assert load_cache(tmp_path / "missing.json") == {}
