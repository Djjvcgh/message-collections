"""RSS / Atom 信源（社区板块、厂商博客、羊毛站点等）。

- 解析走标准库 html.parser（RSS 2.0 / Atom / RDF 都能吃），不引入 feedparser
- options.text_fallback 为 true 时，正文以纯文本返回且不做 HTML 反转义：
  少数站点（如 Discourse 论坛）把整篇文章塞进 description 的文本节点里，
  反转义会把代码块里的 `<div class="x">` 变成真标签，反而破坏正文
- 时间字段尽力解析成 ISO8601，解析失败就留空（过滤不依赖时间，不影响推送）
"""
import html
import re
import time
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests

from .base import Item

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
MAX_TITLE_CHARS = 200
MAX_SUMMARY_CHARS = 400
# 只关心最近的条目，老文章不必进过滤流程
DEFAULT_MAX_AGE_DAYS = 7

_ITEM_TAGS = {"item", "entry"}
_TITLE_TAGS = {"title"}
_LINK_TAGS = {"link"}
_SUMMARY_TAGS = {"description", "summary", "content", "encoded", "content:encoded"}
_DATE_TAGS = {"pubdate", "published", "updated", "date", "dc:date"}
_SKIP_TAGS = {"script", "style"}


def _local(tag):
    name = tag.lower()
    return name.split(":", 1)[-1] if ":" in name else name


def _collapse(text):
    return re.sub(r"\s+", " ", text or "").strip()


class _FeedReader(HTMLParser):
    """注意两个坑：

    1. 不能定义名为 feed() 的方法——那会覆盖 HTMLParser.feed()，
       导致喂进去的文本根本没被解析（异常还会被上层静默吞掉）。
    2. 每个字段标签结束时必须 flush 缓冲区，否则 <title>x</title> 之后
       紧跟 <link> 时，x 会永远留在缓冲区里丢掉。
    """

    def __init__(self, text_fallback=False):
        # convert_charrefs 必须为 False（见 parse_feed 的说明）
        super().__init__(convert_charrefs=False)
        self.text_fallback = text_fallback
        self.items = []
        self.channel_title = ""
        self._in_item = False
        self._depth = 0
        self._field = None
        self._entry = None
        self._link_href = ""
        self._content = []
        self._skip = False

    # --- HTMLParser 回调 ---
    def handle_starttag(self, tag, attrs):
        name = _local(tag)
        if name in _SKIP_TAGS:
            self._skip = True
            return
        if name in _ITEM_TAGS:
            self._in_item = True
            self._depth = 1
            self._field = None
            self._link_href = ""
            self._content = []
            self._entry = {"title": "", "link": "", "summary": "", "date": "", "extra": {}}
            return
        # 注意：这里不能因为「不在 item 里」就早退，
        # 否则 feed 自身的 <title>（channel/feed 级）永远捕获不到，
        # source 名会退化成 feed URL。
        self._depth += 1
        if name in _LINK_TAGS:
            href = dict(attrs).get("href")
            rel = dict(attrs).get("rel", "")
            # Atom 的 rel="alternate" 才是原文链接，rel="self" 是 feed 自身地址。
            # href 立刻写进 entry，避免被同一条目里后续的 <link> 覆盖
            if self._in_item and href and "self" not in rel and not self._entry["link"]:
                self._entry["link"] = href
            self._link_href = href or ""
            self._field = None if "self" in rel else "link"
        elif name in _TITLE_TAGS:
            if self._in_item:
                self._field = "title"
            elif not self.channel_title:
                # feed 自身的标题（RSS 的 channel/title 或 Atom 的 feed/title）
                self._field = "channel_title"
        elif self._in_item and name in _SUMMARY_TAGS:
            self._field = "summary"
        elif self._in_item and name in _DATE_TAGS:
            self._field = "date"

    def handle_endtag(self, tag):
        name = _local(tag)
        if name in _SKIP_TAGS:
            self._skip = False
            return
        if not self._in_item:
            # 条目之外的字段只有 feed 自身标题，同样要 flush，否则 source 名会退化成 URL
            if self._field:
                self._finish_field(None)
            return
        if self._entry is None:
            return
        self._depth -= 1
        if name in _ITEM_TAGS or self._depth <= 0:
            self._finish_item()
            return
        if self._field:
            self._finish_field(self._entry)
        self._field = None

    def handle_data(self, data):
        if self._skip or not self._field:
            return
        self._content.append(data)

    def unknown_decl(self, data):
        """HTMLParser 把 <![CDATA[...]]> 归到 unknown_decl，不处理就等于丢正文。

        Discourse / 多数 RSS 生成器都会用 CDATA 包正文，这里必须接住。
        """
        if self._skip or not self._field:
            return
        text = data or ""
        if text[:6].upper() == "CDATA[":
            text = text[6:]
        if text.endswith("]]"):
            text = text[:-2]
        if text:
            self._content.append(text)

    def _flush(self):
        """取出一段字段文本。

        实体语义要分三种形态，且必须在剥标签之前把「字面量」保护起来：
        - CDATA 里的 `<a href=..>`：真标签，该剥掉；
        - 单重转义 `&lt;p&gt;`：作者想表达标记，解码后当标签剥掉；
        - 双重转义 `&amp;lt;p&amp;gt;`：作者要展示字面量，必须原样留下。
        另外实测（Python 3.13）convert_charrefs=False 时 parser 会按实体边界切块，
        并把 `&lt;`/`&gt;` 的尖括号直接丢掉，所以解码与保护都在这里自己做。
        """
        text = _collapse("".join(self._content))
        self._content = []
        field = self._field
        self._field = None
        if field in ("summary", "title", "channel_title"):
            text = _clean_html(text)
        return field, text

    def _finish_item(self):
        self._finish_field(self._entry)
        entry = self._entry or {}
        self._entry = None
        self._in_item = False
        self._depth = 0
        self._link_href = ""
        if entry.get("title") or entry.get("link"):
            self.items.append(entry)

    def _finish_field(self, entry):
        """把缓冲区里的字段写进 entry（entry 为 None 时只负责清空缓冲区）。"""
        field, text = self._flush()
        if not field:
            return
        if field == "channel_title":
            if text:
                self.channel_title = text
            return
        if entry is None:
            return
        # 兼容 RSS 2.0：<link> 若没有 href 属性，链接写在文本节点里；
        # Atom 的 href 在开始标签上，开始时就该记下来，否则会被后一个链接覆盖
        if field == "link":
            if text:
                entry["link"] = text
            elif self._link_href and not entry["link"]:
                entry["link"] = self._link_href
            return
        if field == "title":
            if entry["title"]:
                entry["extra"].setdefault("title_alt", text)
            else:
                entry["title"] = text
            return
        if field == "summary":
            # 同一 entry 可能有 description + content:encoded，取更长的那段
            if len(text) > len(entry["summary"]):
                entry["summary"] = text
            return
        if field == "date" and text and not entry["date"]:
            entry["date"] = text


def parse_feed(text, text_fallback=False):
    """返回 (feed 标题, 条目 dict 列表)。解析失败返回 ("", [])。

    实体解码与去标签统一在 _clean_html 里做（见其说明），
    这里只负责把文本喂给解析器。
    """
    parser = _FeedReader(text_fallback=text_fallback)
    try:
        parser.feed(_protect_entities(text))
        parser.close()
    except Exception:  # noqa: BLE001 — 半截 HTML 也尽量给出已解析部分
        pass
    return parser.channel_title, parser.items


_LT, _GT = "\x00", "\x01"
_M_LT, _M_GT = "\x02", "\x03"
_TAG = re.compile(r"(?s)<[^>]+>")
_SCRIPTS = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")


def _protect_entities(text):
    """喂给 HTMLParser 之前保护转义实体。

    实测（Python 3.13）parser 在入口就把 `&lt;`/`&gt;` 的尖括号丢掉
    （'&lt;p&gt;x' 变成 'p' 'x'），回调层拿不回原样，只能在入口先换占位符。
    顺序要紧：先处理双重转义（`&amp;lt;`），否则它的尾巴会被单重规则吃掉。
    - `&amp;lt;` → \\x00：作者要展示的字面量，解码后保留
    - `&lt;`    → \\x02：作者想表达的标记，解码后当标签剥掉
    """
    text = (text or "").replace("&amp;lt;", _LT).replace("&amp;gt;", _GT)
    return text.replace("&lt;", _M_LT).replace("&gt;", _M_GT)


def _clean_html(text):
    """把一段 RSS 字段文本清洗成可读纯文本（语义见 _FeedReader._flush）。"""
    if not text:
        return ""
    # 若调用方没走 _protect_entities（直接手测/单独调用），这里补一次
    text = text.replace("&lt;", _M_LT).replace("&gt;", _M_GT)
    text = text.replace(_M_LT, "<").replace(_M_GT, ">")
    text = html.unescape(text)
    text = _SCRIPTS.sub(" ", text)
    text = _TAG.sub(" ", text)
    # 真标签剥完后再还原双重转义的字面量，避免被误剥
    text = text.replace(_LT, "<").replace(_GT, ">")
    # 标签留下的空格挤在标点前会很难看（"领取 ，详情"），顺手收一下
    text = re.sub(r"\s+([，。、；：！？,.;:!?])", r"\1", text)
    return _collapse(re.sub(r"[ \t]{2,}", " ", text))


# 兼容旧名字（页面监控已下线，仅测试引用）
_strip_tags = _clean_html


def parse_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    cleaned = raw.replace("Z", "+00:00")
    try:
        from datetime import datetime

        return datetime.fromisoformat(cleaned).isoformat()
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
            try:
                return time.strftime("%Y-%m-%dT%H:%M:%S", time.strptime(cleaned, fmt))
            except ValueError:
                continue
    return ""


def parse_feed_items(text, feed_url, feed_title="", text_fallback=False, source_id=""):
    """把 feed 文本转成 Item 列表（纯函数，便于测试）。

    feed_title 留空时用 feed 自带的标题（channel/feed 级 <title>），
    再退回 feed_url；这样没配 name 的源也不会把 URL 当来源名展示。
    """
    parsed_title, entries = parse_feed(text, text_fallback=text_fallback)
    source_name = feed_title or parsed_title or feed_url
    items = []
    for entry in entries:
        title = _collapse(entry["title"])[:MAX_TITLE_CHARS]
        link = urljoin(feed_url, (entry["link"] or "").strip())
        if not title and not link:
            continue
        summary = (entry["summary"] or "")[:MAX_SUMMARY_CHARS]
        items.append(
            Item(
                title=title,
                url=link,
                source=source_name,
                source_id=source_id,
                summary=summary,
                extra={"published": parse_date(entry["date"])},
            )
        )
    return items


def fetch_feed_text(url, timeout=20, proxies=None):
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": UA}, proxies=proxies)
    resp.raise_for_status()
    return resp.text


class RssSource:
    type = "rss"

    def __init__(self, source_id, options=None):
        options = options or {}
        self.id = source_id
        self.url = options.get("url")
        if not self.url:
            raise ValueError("rss 信源需要 options.url")
        self.name = options.get("name") or ""
        self.text_fallback = bool(options.get("text_fallback", False))
        self.timeout = int(options.get("timeout", 20))
        self.limit = int(options.get("limit", 0))
        self.max_age_days = float(options.get("max_age_days", DEFAULT_MAX_AGE_DAYS))

    def fetch(self, log=print, proxies=None, **ctx):
        text = fetch_feed_text(self.url, timeout=self.timeout, proxies=proxies)
        feed_title, _ = parse_feed(text, text_fallback=self.text_fallback)
        items = parse_feed_items(
            text,
            self.url,
            feed_title=self.name or feed_title,
            text_fallback=self.text_fallback,
            source_id=self.id,
        )
        items = self._drop_old(items)
        if self.limit:
            items = items[: self.limit]
        log(f"[source:{self.id}] {len(items)} items <- {self.name or self.url}")
        return items

    def _drop_old(self, items):
        """丢弃明显过期的条目；解析不出时间的保留（宁滥勿缺）。"""
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.max_age_days)
        kept = []
        for item in items:
            published = (item.extra or {}).get("published")
            if not published:
                kept.append(item)
                continue
            try:
                when = datetime.fromisoformat(published)
            except ValueError:
                kept.append(item)
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when >= cutoff:
                kept.append(item)
        return kept
