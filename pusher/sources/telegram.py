"""Telegram 公开频道信源（t.me/s/<channel> 网页预览）。

公开频道无需鉴权即可读取网页预览；不加代理时 Actions 上可能超时，
options.proxy 可单独指定出口代理（本地联调常用）。

消息正文里如果带外链，就以该外链作为条目 URL（点进去是活动页），
并保留一条指向原消息的 permalink 在 extra 里，便于溯源。
"""
import html
import re
from urllib.parse import urljoin

import requests

from .base import Item

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
MAX_TITLE_CHARS = 200
MAX_SUMMARY_CHARS = 400
MAX_ITEMS = 30

_WRAP = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
_DATE = re.compile(r'<time[^>]*datetime="([^"]+)"', re.S)
_MESSAGE_ID = re.compile(r'data-post="([^"]+)"')
_TAGS = re.compile(r"<[^>]+>")
_HREF = re.compile(r'<a[^>]+href="([^"]+)"', re.I)
# 裸链接：频道里大量活动是直接贴 URL 的，不提取就会退化成 permalink
_BARE_URL = re.compile(r"https?://[^\s<>\"'）】]+", re.I)
_BR = re.compile(r"<br\s*/?>", re.I)
_TME = "https://t.me/"


def _text_of(raw):
    text = _BR.sub("\n", raw)
    text = _TAGS.sub("", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _links_of(raw):
    """外链优先，裸链接兜底；t.me 内链不算（那是消息自身地址）。"""
    out = []
    for href in _HREF.findall(raw):
        href = html.unescape(href)
        if href.startswith("http") and not href.startswith(_TME):
            out.append(href)
    if not out:
        text_only = _TAGS.sub(" ", raw)
        out = [u for u in _BARE_URL.findall(text_only) if not u.startswith(_TME)]
    return out


def parse_channel_html(page, channel):
    """返回条目 dict 列表。t.me 的 message 块与文本块一一对应。"""
    posts = _MESSAGE_ID.findall(page or "")
    raw_texts = _WRAP.findall(page or "")
    dates = _DATE.findall(page or "")
    entries = []
    for index, raw in enumerate(raw_texts):
        text = _text_of(raw)
        if not text:
            continue
        permalink = ""
        if index < len(posts):
            permalink = urljoin(_TME, posts[index].strip())
        links = _links_of(raw)
        entries.append(
            {
                "text": text,
                "url": links[0] if links else permalink,
                "permalink": permalink,
                "date": dates[index] if index < len(dates) else "",
                "links": links,
                "channel": channel,
            }
        )
    return entries[:MAX_ITEMS]


def _title_of(text):
    first = text.splitlines()[0].strip() or text.strip()
    return first[:MAX_TITLE_CHARS]


def parse_channel_items(page, channel, source_id="", source_name=""):
    """把频道预览页转成 Item 列表（纯函数，便于测试）。"""
    items = []
    for entry in parse_channel_html(page, channel):
        items.append(
            Item(
                title=_title_of(entry["text"]),
                url=entry["url"],
                source=source_name or f"t.me/{channel}",
                source_id=source_id,
                summary=entry["text"][:MAX_SUMMARY_CHARS],
                extra={
                    "published": entry["date"],
                    "permalink": entry["permalink"],
                    "channel": channel,
                    "links": entry["links"],
                },
            )
        )
    return items


def fetch_channel_html(channel, timeout=20, proxies=None):
    url = f"{_TME}s/{channel}"
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": UA}, proxies=proxies)
    resp.raise_for_status()
    return resp.text


class TelegramChannelSource:
    type = "telegram"

    def __init__(self, source_id, options=None):
        options = options or {}
        self.id = source_id
        self.channel = options.get("channel")
        if not self.channel:
            raise ValueError("telegram 信源需要 options.channel")
        self.name = options.get("name") or ""
        self.timeout = int(options.get("timeout", 20))
        self.limit = int(options.get("limit", MAX_ITEMS))
        self.proxy = options.get("proxy") or None

    def fetch(self, log=print, proxies=None, **ctx):
        use_proxy = proxies
        if self.proxy:
            use_proxy = {"http": self.proxy, "https": self.proxy}
        page = fetch_channel_html(self.channel, timeout=self.timeout, proxies=use_proxy)
        items = parse_channel_items(
            page, self.channel, source_id=self.id, source_name=self.name
        )
        if self.limit:
            items = items[-self.limit :]
        log(f"[source:{self.id}] {len(items)} items <- t.me/s/{self.channel}")
        return items
