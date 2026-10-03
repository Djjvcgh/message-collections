import pytest

from pusher.notify_base import NotifyChannel, send_all
from pusher.notify_email import subject_from
from pusher.notify_telegram import build_instant_message, esc
from pusher.notify_wecom import WeComChannel


def test_esc_escapes_html():
    assert esc("<b>标题 & 测试") == "&lt;b&gt;标题 &amp; 测试"


def test_build_instant_message_layout():
    item = {
        "title": "ZCode 送 token 限时活动开启",
        "source": "AIbase",
        "tier_label": "AI垂直源",
        "url": "https://example.com/zcode-token",
        "reason": "ZCode 推出送 token 活动",
    }
    msg = build_instant_message(item)
    # 样式：标题行 → 引用块摘要 → via 来源（URL 内嵌），逐行紧凑排列
    assert "🎁 [福利] <b>ZCode 送 token 限时活动开启</b>\n<blockquote>" in msg
    assert "</blockquote>\nvia <a href=\"https://example.com/zcode-token\">AIbase</a> · AI垂直源" in msg


def test_build_instant_message_renders_detail_lines():
    """抽到硬信息时按行列出；摘要里已有的项不重复占行。"""
    item = {
        "title": "AdGuard Family Plan 终身订阅优惠",
        "source": "V2EX 优惠信息",
        "url": "https://example.com/adguard",
        "summary": "折扣码：LIFETIMEO 最终到手价：￥59.20 CNY",
    }
    msg = build_instant_message(item)
    assert "▎价格：<b>￥59.20</b>（到手价）" in msg
    assert "▎折扣码：LIFETIMEO" in msg
    assert msg.index("▎价格") < msg.index("via <a")


def test_build_instant_message_detail_line_rescues_code_from_long_summary():
    """长摘要会被压缩，券码可能被压掉——这时靠细节行把它拎出来。"""
    long_body = (
        "几年以前我在 V2EX 发过这个工具的第一版，当时是基于 SSH 协议做的内网穿透，"
        "这几年陆续更新了不少东西，节点从早期单节点扩展到多地区，域名的 SSL 证书集成得更完整，"
        "凭兑换券码 NWY-CIYUM-4UYZD-40694 到平台仪表盘兑换，先到先得。"
    )
    item = {
        "title": "内网云 2026 国庆活动余额兑换券",
        "source": "V2EX 优惠信息",
        "url": "https://example.com/nwy",
        "summary": long_body,
    }
    msg = build_instant_message(item)
    assert "▎折扣码：NWY-CIYUM-4UYZD-40694" in msg
    assert "▎价格" not in msg  # 没有价格就不占行


def test_short_summary_dedupes_price_but_keeps_code_line():
    """摘要里已出现的信息不重复占行，但折扣码例外（强制单独成行便于扫到）。"""
    item = {
        "title": "内网云国庆活动余额兑换券",
        "source": "V2EX 优惠信息",
        "url": "https://example.com/nwy",
        "summary": "兑换券码：NWY-CIYUM-4UYZD-40694 先到先得",
    }
    msg = build_instant_message(item)
    assert "▎折扣码：NWY-CIYUM-4UYZD-40694" in msg
    assert "▎价格" not in msg


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
