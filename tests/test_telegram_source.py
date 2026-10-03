from pusher.sources.telegram import (
    TelegramChannelSource,
    parse_channel_html,
    parse_channel_items,
)

PAGE = """
<div class="tgme_widget_message" data-post="yangmaoshe/123">
  <div class="tgme_widget_message_text">限时免费领取 1 个月 <b>VPS</b>，先到先得<br/>
  详情 https://example.com/free-vps</div>
  <time datetime="2026-10-03T08:00:00+00:00"></time>
</div>
<div class="tgme_widget_message" data-post="yangmaoshe/124">
  <div class="tgme_widget_message_text">某站开放注册，无需邀请码，<a href="https://t.me/yangmaoshe/124">原帖</a></div>
  <time datetime="2026-10-03T09:00:00+00:00"></time>
</div>
"""


def test_parse_channel_html_structure():
    entries = parse_channel_html(PAGE, "yangmaoshe")
    assert len(entries) == 2
    first = entries[0]
    assert "限时免费领取" in first["text"]
    assert first["url"] == "https://example.com/free-vps"
    assert first["permalink"] == "https://t.me/yangmaoshe/123"
    assert first["date"].startswith("2026-10-03T08:00")


def test_parse_channel_items_uses_outer_link_as_url():
    items = parse_channel_items(PAGE, "yangmaoshe", source_id="tg-yangmaoshe", source_name="羊毛社")
    assert len(items) == 2
    assert items[0].url == "https://example.com/free-vps"
    assert items[0].source == "羊毛社"
    assert items[0].source_id == "tg-yangmaoshe"
    # 正文里的 Telegram 内链不算外链，回退到消息 permalink
    assert items[1].url == "https://t.me/yangmaoshe/124"
    assert items[1].extra["permalink"] == "https://t.me/yangmaoshe/124"


def test_title_is_first_line_only():
    items = parse_channel_items(PAGE, "yangmaoshe")
    assert items[0].title == "限时免费领取 1 个月 VPS，先到先得"


def test_empty_page_yields_nothing():
    assert parse_channel_html("", "yangmaoshe") == []
    assert parse_channel_items("", "yangmaoshe") == []


def test_message_without_text_is_skipped():
    page = '<div class="tgme_widget_message" data-post="c/1"></div>'
    assert parse_channel_html(page, "c") == []


def test_source_requires_channel():
    import pytest

    with pytest.raises(ValueError):
        TelegramChannelSource("tg", {})
