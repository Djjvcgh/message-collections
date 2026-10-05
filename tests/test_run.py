import json

from pusher.filter import WelfareFilter
from pusher.notify_base import NotifyChannel
from pusher.run import main, run_once, select_pushes
from pusher.sources import SOURCE_TYPES
from pusher.sources.base import Item
from pusher.state import State

WELFARE_KW = ["免费", "赠送", "白嫖"]
OPPORTUNITY_KW = ["开放注册", "限量", "截止"]

ITEMS = [
    Item(
        title="某云赠送免费服务器 3 个月",
        url="https://a.example.com/1",
        source="Linux.do 免费资源",
        summary="注册即可领取",
    ),
    Item(
        title="某站开放注册，无需邀请码",
        url="https://b.example.com/2",
        source="NodeSeek",
        summary="限时窗口",
    ),
    Item(title="OpenAI 发布新模型", url="https://c.example.com/3"),
    Item(title="本站广告位招租，福利多多", url="https://d.example.com/4"),
]


class FakeSource:
    """假信源：直接吐出内存条目，测试不触网。"""

    type = "fake"

    def __init__(self, source_id, options=None):
        self.id = source_id
        self.options = options or {}

    def fetch(self, log=print, **ctx):
        return [Item(**vars(item)) for item in ITEMS]


class RecordingChannel(NotifyChannel):
    name = "record"

    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send(self, text, parse_mode=None):
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append(text)


def make_filter():
    return WelfareFilter(WELFARE_KW, OPPORTUNITY_KW, ["广告"])


def run(tmp_path, monkeypatch, channels, max_push=8, dry_run=False):
    monkeypatch.setitem(SOURCE_TYPES, "fake", FakeSource)
    return run_once(
        {"sources": [{"id": "fake", "type": "fake"}]},
        make_filter(),
        State.load(tmp_path / "state.json"),
        channels,
        dry_run=dry_run,
        max_push=max_push,
        proxies=None,
        log=lambda *_: None,
        state_path=tmp_path / "state.json",
    )


def test_run_once_pushes_welfare_and_opportunity(tmp_path, monkeypatch):
    channel = RecordingChannel()
    sent = run(tmp_path, monkeypatch, [channel])
    assert sent == 2
    welfare = [m for m in channel.sent if "🎁 [福利]" in m]
    opportunity = [m for m in channel.sent if "⏳ [限时]" in m]
    assert len(welfare) == 1 and len(opportunity) == 1
    assert "某云赠送免费服务器 3 个月" in welfare[0]
    assert "<blockquote>注册即可领取</blockquote>" in welfare[0]
    assert 'via <a href="https://a.example.com/1">Linux.do 免费资源</a>' in welfare[0]
    assert "某站开放注册" in opportunity[0]
    # 未命中的资讯与命中排除词的条目都不推
    assert all("OpenAI 发布新模型" not in m for m in channel.sent)
    assert all("广告位" not in m for m in channel.sent)


def test_state_prevents_repeat_and_records_both_keys(tmp_path, monkeypatch):
    run(tmp_path, monkeypatch, [RecordingChannel()])
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert len(saved["pushed"]) == 4  # 2 条 × (URL + 标题)
    assert "digests" not in saved     # 日报字段已随功能下线

    again = RecordingChannel()
    assert run(tmp_path, monkeypatch, [again]) == 0
    assert again.sent == []


def test_dry_run_sends_nothing_and_writes_no_state(tmp_path, monkeypatch):
    channel = RecordingChannel()
    assert run(tmp_path, monkeypatch, [channel], dry_run=True) == 0
    assert channel.sent == []
    assert not (tmp_path / "state.json").exists()


def test_failed_delivery_is_not_recorded(tmp_path, monkeypatch):
    assert run(tmp_path, monkeypatch, [RecordingChannel(fail=True)]) == 0
    state = State.load(tmp_path / "state.json")
    assert not state.is_pushed("https://a.example.com/1")
    assert not state.is_pushed("", "某云赠送免费服务器 3 个月")


def test_max_push_caps_output(tmp_path, monkeypatch):
    channel = RecordingChannel()
    assert run(tmp_path, monkeypatch, [channel], max_push=1) == 1
    assert len(channel.sent) == 1
    # 被截断的条目没有记账，下一轮还能补发
    assert run(tmp_path, monkeypatch, [RecordingChannel()], max_push=1) == 1


def test_select_pushes_prefers_welfare_then_authority():
    welfare_low = Item(title="w2", tier=5, extra={"kind": "welfare"})
    welfare_high = Item(title="w1", tier=1, extra={"kind": "welfare"})
    opportunity = Item(title="o1", tier=0, extra={"kind": "opportunity"})
    picked = select_pushes([welfare_low, opportunity, welfare_high], max_push=2)
    assert [i.title for i in picked] == ["w1", "w2"]


def test_select_pushes_unlimited_when_zero():
    items = [Item(title=str(i), extra={"kind": "welfare"}) for i in range(5)]
    assert len(select_pushes(items, max_push=0)) == 5


def fake_sources(cfg, only=None, log=print):
    return [FakeSource("fake", {})]


def test_main_dry_run_uses_real_config(monkeypatch, capsys):
    import pusher.run as run_mod

    monkeypatch.setattr(run_mod, "load_env", lambda *a, **k: None)
    monkeypatch.setattr(run_mod, "load_sources", fake_sources)
    assert main(["--dry-run", "--limit", "1"]) == 0
    out = capsys.readouterr().out
    assert "collected 4 items" in out
    assert "hits: 2" in out
    assert "🎁 [福利]" in out
    assert "done: 0 pushed" in out


def test_main_probe_reports_each_source(monkeypatch, capsys):
    import pusher.run as run_mod

    monkeypatch.setattr(run_mod, "load_env", lambda *a, **k: None)
    monkeypatch.setattr(run_mod, "load_sources", fake_sources)
    assert main(["--probe"]) == 0
    out = capsys.readouterr().out
    assert "probe summary: 1/1 sources usable" in out


def test_main_without_channel_aborts(monkeypatch, capsys):
    import pusher.run as run_mod

    monkeypatch.setattr(run_mod, "load_env", lambda *a, **k: None)
    monkeypatch.setattr(run_mod, "build_channels", lambda settings: [])
    assert main([]) == 1
    assert "no usable channel configured" in capsys.readouterr().out


def test_ensure_utf8_console_survives_gbk_stdout(monkeypatch):
    """实测缺陷：Windows GBK 控制台下 --dry-run 打印 🎁 直接抛 UnicodeEncodeError。"""
    import io
    import sys

    import pusher.run as run_mod

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="gbk", errors="strict", newline="")
    monkeypatch.setattr(sys, "stdout", stream)
    run_mod.ensure_utf8_console()
    run_mod.log("🎁 [福利] 测试")
    stream.flush()
    assert "🎁 [福利]" in raw.getvalue().decode("utf-8")


def test_ensure_utf8_console_tolerates_streams_without_reconfigure(monkeypatch):
    """pytest 捕获流等对象没有 reconfigure，必须静默跳过而不是抛错。"""
    import sys

    import pusher.run as run_mod

    class DumbStream:
        def write(self, text):
            return len(text)

    monkeypatch.setattr(sys, "stdout", DumbStream())
    monkeypatch.setattr(sys, "stderr", DumbStream())
    run_mod.ensure_utf8_console()   # 不抛异常即通过


def test_config_yaml_matches_code():
    """配置与代码同步：sources.yml 里的 type 必须都在注册表里。"""
    from pathlib import Path

    import yaml

    root = Path(__file__).parent.parent
    cfg = yaml.safe_load((root / "config" / "sources.yml").read_text(encoding="utf-8"))
    assert cfg["sources"], "信源清单不能为空"
    for spec in cfg["sources"]:
        assert spec["type"] in SOURCE_TYPES, spec
        assert spec.get("id"), spec

    settings = yaml.safe_load((root / "config" / "settings.yml").read_text(encoding="utf-8"))
    assert settings["channels"]["telegram"]["enabled"] is True
    assert settings["push"]["max_per_run"] >= 1
