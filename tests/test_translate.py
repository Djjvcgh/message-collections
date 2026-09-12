import json

import pytest

from pusher.translate import is_mostly_english, translate_title


def test_is_mostly_english():
    assert is_mostly_english("GSA says OpenAI is replacing its pilot")
    assert not is_mostly_english("DeepSeek 发布 V4.1 模型")
    assert not is_mostly_english("")
    assert not is_mostly_english("short")


def test_chinese_title_skips_translation(tmp_path):
    cache = tmp_path / "translations.json"
    assert translate_title("DeepSeek 发布新模型", cache_path=cache) == "DeepSeek 发布新模型"
    assert not cache.exists()  # 没发过任何请求


def test_translate_uses_cache(monkeypatch, tmp_path):
    cache = tmp_path / "translations.json"
    calls = []

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return [[["人工智能发布新模型", "dummy"]]]

    def fake_get(url, params=None, timeout=None, proxies=None):
        calls.append(params["q"])
        return FakeResp()

    monkeypatch.setattr("pusher.translate.requests.get", fake_get)

    first = translate_title("AI releases new model", cache_path=cache)
    second = translate_title("AI releases new model", cache_path=cache)
    assert first == "人工智能发布新模型"
    assert second == first
    assert len(calls) == 1  # 第二次命中缓存，无 HTTP 请求
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert len(saved) == 1


def test_translate_failure_falls_back(monkeypatch, tmp_path):
    def fake_get(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr("pusher.translate.requests.get", fake_get)
    out = translate_title("Some English title here", cache_path=tmp_path / "c.json")
    assert out == "Some English title here"
