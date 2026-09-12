"""英文标题翻译（零成本方案）。

用 Google 翻译公共 gtx 接口（无需 Key），仅当标题以英文为主时调用；
译文缓存到 state/translations.json，同一标题只翻译一次。
任何失败都回退原文，绝不阻塞推送。
"""
import hashlib
import json
import re
from pathlib import Path

import requests

GTX_URL = "https://translate.googleapis.com/translate_a/single"
MM_URL = "https://api.mymemory.translated.net/get"
CACHE_PATH = Path(__file__).resolve().parent.parent / "state" / "translations.json"


def is_mostly_english(text):
    if not text:
        return False
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    letters = len(re.findall(r"[A-Za-z]", text))
    return letters > 8 and cjk / (cjk + letters) < 0.25


def _load_cache(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _via_gtx(text, proxies, timeout):
    resp = requests.get(
        GTX_URL,
        params={"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": text},
        timeout=timeout,
        proxies=proxies,
    )
    resp.raise_for_status()
    segments = resp.json()[0]
    return "".join(seg[0] for seg in segments if seg and seg[0]).strip()


def _via_mymemory(text, proxies, timeout):
    if len(text.encode("utf-8")) > 480:  # MyMemory 单次查询上限 500 字节
        return ""
    resp = requests.get(
        MM_URL,
        params={"q": text, "langpair": "en|zh-CN"},
        timeout=timeout,
        proxies=proxies,
    )
    resp.raise_for_status()
    out = (resp.json().get("responseData") or {}).get("translatedText") or ""
    return out.strip()


def translate_title(text, cache_path=CACHE_PATH, proxies=None, timeout=10):
    """英文标题译为中文；中文标题原样返回；全部失败回退原文。"""
    if not is_mostly_english(text):
        return text
    cache = _load_cache(cache_path)
    key = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    if key in cache:
        return cache[key]
    for provider in (_via_gtx, _via_mymemory):
        try:
            translated = provider(text, proxies, timeout)
        except Exception:  # noqa: BLE001 — 翻译是锦上添花，失败必须静默降级
            continue
        if translated:
            cache[key] = translated
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            return translated
    return text
