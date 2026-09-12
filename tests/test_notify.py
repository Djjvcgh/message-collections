import pytest

from pusher.notify_base import NotifyChannel, send_all
from pusher.notify_email import subject_from
from pusher.notify_telegram import build_instant_message, esc, link
from pusher.notify_wecom import WeComChannel


def test_esc_escapes_html():
    assert esc("<b>标题 & 测试") == "&lt;b&gt;标题 &amp; 测试"


def test_link_escapes_url():
    out = link("https://a.com/?x=1&y=2")
    assert 'href="https://a.com/?x=1&amp;y=2"' in out


def test_build_instant_message_layout():
    item = {
        "title": "ZCode 送 token 限时活动开启",
        "source": "AIbase",
        "tier_label": "AI垂直源",
        "url": "https://example.com/zcode-token",
    }
    msg = build_instant_message(item)
    assert "🎁 [福利]" in msg
    assert "<b>ZCode 送 token 限时活动开启</b>" in msg
    assert "来源：AIbase · AI垂直源" in msg
    assert 'href="https://example.com/zcode-token"' in msg


class Boom(NotifyChannel):
    name = "boom"

    def send(self, text, parse_mode=None):
        raise RuntimeError("boom")


class OK(NotifyChannel):
    name = "ok"
    sent = []

    def send(self, text, parse_mode=None):
        OK.sent.append(text)


def test_send_all_isolates_failures():
    OK.sent = []
    failed = send_all([Boom(), OK()], "hello", log=lambda *_: None)
    assert failed == ["boom"]
    assert OK.sent == ["hello"]


class Record(NotifyChannel):
    """记录调用参数的渠道，用于验证 parse_mode 传递规则。"""

    name = "record"

    def __init__(self):
        self.calls = []

    def send(self, text, parse_mode=None):
        self.calls.append((text, parse_mode))


def test_send_all_none_parse_mode_uses_channel_default():
    ch = Record()
    send_all([ch], "hello", log=lambda *_: None)
    # parse_mode=None 时不应显式传参，渠道用自身默认值
    assert ch.calls == [("hello", None)]


def test_telegram_omits_parse_mode_key_when_none(monkeypatch):
    import pusher.notify_telegram as nt

    captured = {}

    class FakeResp:
        status_code = 200

    def fake_post(url, json=None, timeout=None, proxies=None):
        captured["payload"] = json
        return FakeResp()

    monkeypatch.setattr(nt.requests, "post", fake_post)
    ch = nt.TelegramChannel("t", "c")
    ch.send("hi", parse_mode=None)
    assert "parse_mode" not in captured["payload"]

    ch.send("hi")
    assert captured["payload"]["parse_mode"] == "HTML"


def test_wecom_requires_webhook():
    with pytest.raises(RuntimeError):
        WeComChannel(None)


def test_wecom_sends_markdown_payload(monkeypatch):
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr("pusher.notify_wecom.requests.post", fake_post)
    ch = WeComChannel("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x")
    ch.send("测试")
    assert captured["json"]["msgtype"] == "markdown"
    assert captured["json"]["markdown"]["content"] == "测试"


def test_subject_from_strips_tags():
    assert subject_from("🎁 [福利] <b>标题</b>\n第二行") == "[AI情报] 🎁 [福利] 标题"
