"""正文兜底：源数据里没有摘要时，从原文页抓一段能读的正文。

实测依据（2026-10-05，radar 线上数据 112 条）：
- 上游 `summary` 非空 23 条、`recommend_reason_zh` 非空 75 条，
  **89 条（79%）两个字段都空**；用随附的 `data/latest-24h-all.json`
  按 id/url 回捞，能补回 **0 条**。这些条目推出去就是「标题 + via 行」，
  正是用户反馈的「缺乏摘要、信息量太少」。
所以这里补一层：只对**将要推送**的条目（每轮 ≤ max_enrich 条）抓一次原文页，
按 og:description → meta[name=description] → 首个 <p> 的顺序取正文，
失败就静默降级（抓不到就当没有正文，交给 run.py 的正文闸门处理）。

缓存写在 state/bodies.json（键=URL 哈希），同一条链接只抓一次，
抓不到也记空串，避免每轮重试把运行时间耗在失败页面上。
"""
import hashlib
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import requests

CACHE_PATH = Path(__file__).resolve().parent.parent / "state" / "bodies.json"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
MAX_BODY_CHARS = 600
DEFAULT_TIMEOUT = 8
DEFAULT_MAX_ITEMS = 10
MIN_PARAGRAPH_CHARS = 20        # 太短的 <p> 多是导航/版权，不当正文


def _collapse(text):
    return re.sub(r"\s+", " ", text or "").strip()


class _BodyReader(HTMLParser):
    """取页面里最适合当摘要的三处文本：og:description / description / 首个像样的 <p>。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.og = ""
        self.description = ""
        self.first_paragraph = ""
        self._in_paragraph = False
        self._in_skip = False
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style", "noscript"):
            self._in_skip = True
            return
        if tag == "meta":
            data = {str(k).lower(): (v or "") for k, v in attrs}
            name = (data.get("property") or data.get("name") or "").strip().lower()
            content = (data.get("content") or "").strip()
            if not content:
                return
            if name in ("og:description", "twitter:description") and not self.og:
                self.og = content
            elif name == "description" and not self.description:
                self.description = content
            return
        if tag == "p" and not self.first_paragraph and not self._in_skip:
            self._in_paragraph = True
            self._buffer = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style", "noscript"):
            self._in_skip = False
            return
        if tag == "p" and self._in_paragraph:
            self._in_paragraph = False
            text = _collapse("".join(self._buffer))
            if len(text) >= MIN_PARAGRAPH_CHARS and not self.first_paragraph:
                self.first_paragraph = text

    def handle_data(self, data):
        if self._in_paragraph and not self._in_skip:
            self._buffer.append(data)


def extract_body(page):
    """从 HTML 里取一段正文；取不到返回空串。"""
    reader = _BodyReader()
    try:
        reader.feed(page or "")
        reader.close()
    except Exception:  # noqa: BLE001 — 半截 HTML 也尽量给出已解析部分
        pass
    for candidate in (reader.og, reader.description, reader.first_paragraph):
        text = _collapse(html.unescape(candidate or ""))
        if text:
            return text[:MAX_BODY_CHARS]
    return ""


def _decode(resp):
    """中文站点常不带 charset，requests 会猜成 ISO-8859-1 变乱码。"""
    raw = getattr(resp, "content", None)
    if raw:
        for encoding in ("utf-8", "gb18030"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
    return resp.text or ""


def fetch_body(url, timeout=DEFAULT_TIMEOUT, proxies=None, get=None):
    """抓一页并抽出正文；任何失败都返回空串（调用方不感知异常）。"""
    getter = get or requests.get
    try:
        resp = getter(url, timeout=timeout, headers={"User-Agent": UA}, proxies=proxies)
    except Exception:  # noqa: BLE001 — 抓取失败是常态，必须静默降级
        return ""
    if getattr(resp, "status_code", 200) >= 400:
        return ""
    return extract_body(_decode(resp))


def load_cache(path=CACHE_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(cache, path=CACHE_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def cache_key(url):
    return hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:16]


def body_for(url, cache, timeout=DEFAULT_TIMEOUT, proxies=None, get=None, save=None):
    """取正文：命中缓存直接返回；否则抓一次并写缓存（抓不到记空串）。"""
    key = cache_key(url)
    if key in cache:
        return cache[key] or ""
    body = fetch_body(url, timeout=timeout, proxies=proxies, get=get) if url else ""
    cache[key] = body
    if save:
        save(cache)
    return body


def enrich_items(
    items,
    cache,
    timeout=DEFAULT_TIMEOUT,
    max_items=DEFAULT_MAX_ITEMS,
    proxies=None,
    get=None,
    save=None,
    log=print,
):
    """给「没有正文」的条目补正文，返回补上的条数。

    只处理将要推送的条目（调用方先分类、后 enrichment），
    所以每轮的网络开销是常数级（≤ max_items 次抓取）。
    """
    filled = 0
    budget = max_items
    for item in items:
        if (getattr(item, "summary", "") or "").strip():
            continue
        if budget <= 0:
            break
        budget -= 1
        url = getattr(item, "url", "")
        body = body_for(
            url, cache, timeout=timeout, proxies=proxies, get=get, save=save
        )
        if body:
            item.summary = body
            item.extra["body_from"] = "page"
            filled += 1
        else:
            log(f"[enrich:miss] {url[:100] or '(无链接)'}")
    return filled
