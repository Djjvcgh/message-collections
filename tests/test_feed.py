from pusher.sources.feed import (
    MAX_SUMMARY_CHARS,
    RssSource,
    parse_date,
    parse_feed_items,
)

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Linux.do 免费资源</title>
  <item>
    <title>某云厂商赠送 3 个月免费服务器</title>
    <link>https://linux.do/t/topic/1</link>
    <description>&lt;p&gt;注册即可领取，&lt;a href="https://x.com"&gt;详情&lt;/a&gt;&lt;/p&gt;</description>
    <pubDate>Sat, 03 Oct 2026 08:00:00 GMT</pubDate>
  </item>
  <item>
    <title>【福利】免费域名 + 免费 SSL 证书</title>
    <link>https://linux.do/t/topic/2</link>
    <description>先到先得</description>
    <pubDate>Fri, 02 Oct 2026 08:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Cloudflare Blog</title>
  <entry>
    <title>Free tier limits increased</title>
    <link rel="alternate" href="https://blog.cloudflare.com/free-tier/"/>
    <link rel="self" href="https://blog.cloudflare.com/feed.xml"/>
    <summary>We raised free tier limits for everyone.</summary>
    <updated>2026-10-02T13:28:10Z</updated>
  </entry>
</feed>
"""

# Discourse 风格一：双重转义 = 作者要展示字面量，必须原样保留
DISCOURSE_ESCAPED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Linux.do</title>
  <item>
    <title>分享一个免费 DNS 服务</title>
    <link>https://linux.do/t/topic/3</link>
    <description>正文里出现 &amp;lt;div class="x"&amp;gt; 这类代码是标签</description>
  </item>
</channel></rss>
"""

# Discourse 风格二：description 是 CDATA，正文里是真标签
DISCOURSE_CDATA = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Linux.do</title>
  <item>
    <title>某云免费服务器活动</title>
    <link>https://linux.do/t/topic/4</link>
    <description><![CDATA[<p>注册即可领取，<a href="https://x.com">详情</a></p>]]></description>
  </item>
</channel></rss>
"""


def test_parse_rss_items_title_link_summary():
    items = parse_feed_items(RSS, "https://linux.do/c/free-stuff/13.rss", "Linux.do 免费资源")
    assert len(items) == 2
    first = items[0]
    assert first.title == "某云厂商赠送 3 个月免费服务器"
    assert first.url == "https://linux.do/t/topic/1"
    assert first.source == "Linux.do 免费资源"
    # 单重转义的 HTML 是「作者想要的标记」，解码后剥掉只留文字
    assert first.summary == "注册即可领取， 详情"
    assert first.extra["published"].startswith("2026-10-03T08:00")


def test_atom_alternate_link_preferred_over_self():
    items = parse_feed_items(ATOM, "https://blog.cloudflare.com/rss/")
    assert len(items) == 1
    assert items[0].url == "https://blog.cloudflare.com/free-tier/"
    # 没配 name 时应回落到 feed 自带标题，而不是把 URL 当来源名
    assert items[0].source == "Cloudflare Blog"
    assert items[0].title == "Free tier limits increased"


def test_explicit_name_overrides_feed_title():
    items = parse_feed_items(ATOM, "https://blog.cloudflare.com/rss/", "CF 官方博客")
    assert items[0].source == "CF 官方博客"


def test_double_escaped_markup_is_preserved():
    """双重转义表示「这是文本」：解码后不能被当标签剥掉。"""
    items = parse_feed_items(
        DISCOURSE_ESCAPED,
        "https://linux.do/latest.rss",
        text_fallback=True,
        source_id="linuxdo-latest",
    )
    assert items
    assert '<div class="x">' in items[0].summary
    assert items[0].source_id == "linuxdo-latest"
    assert items[0].source == "Linux.do"  # 没配 name 时回落到 feed 标题


def test_cdata_body_strips_real_tags():
    """CDATA 里是真标签，应剥掉只留文字。"""
    items = parse_feed_items(DISCOURSE_CDATA, "https://linux.do/latest.rss")
    assert items
    assert items[0].summary == "注册即可领取， 详情"


def test_entity_decoding_keeps_code_snippet_escaped():
    items = parse_feed_items(RSS, "https://linux.do/c/free-stuff/13.rss")
    assert "详情" in items[0].summary


def test_relative_link_is_resolved():
    feed = (
        '<rss version="2.0"><channel><title>T</title><item>'
        "<title>免费额度</title><link>/posts/1</link>"
        "</item></channel></rss>"
    )
    items = parse_feed_items(feed, "https://example.com/feed.xml")
    assert items[0].url == "https://example.com/posts/1"


def test_summary_is_truncated():
    body = "免费" * 500
    feed = (
        '<rss version="2.0"><channel><title>T</title><item>'
        f"<title>免费额度</title><link>https://x.com/1</link><description>{body}</description>"
        "</item></channel></rss>"
    )
    items = parse_feed_items(feed, "https://x.com/feed")
    assert len(items[0].summary) <= MAX_SUMMARY_CHARS


def test_parse_date_formats():
    assert parse_date("Sat, 03 Oct 2026 08:00:00 GMT").startswith("2026-10-03T08:00")
    assert parse_date("2026-10-02T13:28:10Z").startswith("2026-10-02T13:28")
    assert parse_date("2026-10-02 13:28:10").startswith("2026-10-02T13:28")
    assert parse_date("") == ""
    assert parse_date("不是日期") == ""


def test_source_drops_stale_entries():
    src = RssSource("x", {"url": "https://x.com/feed", "max_age_days": 3})
    old = parse_feed_items(
        '<rss version="2.0"><channel><title>T</title><item><title>旧福利</title>'
        "<link>https://x.com/old</link>"
        "<pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>"
        "</item></channel></rss>",
        "https://x.com/feed",
    )
    assert src._drop_old(old) == []


def test_source_requires_url():
    import pytest

    with pytest.raises(ValueError):
        RssSource("x", {})


def test_source_uses_parser_without_network():
    # 解析层是纯函数：喂文本即可出条目，抓取单独留给 fetch()
    src = RssSource("x", {"url": "https://example.com/feed"})
    assert src.id == "x"
    assert len(parse_feed_items(RSS, src.url)) == 2
