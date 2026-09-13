"""页面 diff 监控（M2）。

抓取固定清单页面（jina 渲染优先，输出稳定的 markdown；失败回退直连），
与 state/pages/<name>.txt 快照对比，内容变化即推送。
快照只存「SHA 指纹 + 字符数」而非全文，git 不膨胀；
快照在推送成功后才更新，推送失败不会丢失本次变化。
"""
import hashlib
import html
import re

import requests

from .notify_telegram import esc

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}


def _fetch_jina(url, timeout, proxies):
    # r.jina.ai 渲染 JS 并输出稳定 markdown，作为主抓取通道
    resp = requests.get(
        f"https://r.jina.ai/{url}", timeout=timeout, headers=UA, proxies=proxies
    )
    resp.raise_for_status()
    return resp.text


def _fetch_direct(url, timeout, proxies):
    resp = requests.get(url, timeout=timeout, headers=UA, proxies=proxies)
    resp.raise_for_status()
    return resp.text


def fetch_page(url, timeout=45, proxies=None):
    """jina 优先、直连兜底；两者都失败抛 RuntimeError。"""
    try:
        return _fetch_jina(url, timeout, proxies)
    except Exception as jina_err:
        try:
            return _fetch_direct(url, timeout, proxies)
        except Exception:
            raise RuntimeError(f"jina & direct fetch both failed: {jina_err}") from None


def normalize_content(text):
    """压平所有空白，避免与正文无关的排版抖动触发误报。"""
    return re.sub(r"\s+", " ", text or "").strip()


def extract_title(content):
    """jina 输出的首行通常是 'Title: ...'。"""
    for line in content.splitlines():
        if line.startswith("Title:"):
            return line[len("Title:"):].strip()
    return None


def fingerprint(norm):
    """快照指纹：SHA 前 16 位 + 字符数，一行文本。"""
    return f"{hashlib.sha256(norm.encode('utf-8')).hexdigest()[:16]} {len(norm)}"


def check_pages(pages, pages_dir, proxies=None, timeout=45, log=print):
    """逐页抓取对比。

    返回变化列表；首访页面静默落基线；无变化不写盘（避免 state 空转）。
    变化页面的新指纹由调用方在推送成功后写入 snapshot_path。
    """
    changes = []
    pages_dir.mkdir(parents=True, exist_ok=True)
    for page in pages:
        name, url = page["name"], page["url"]
        try:
            content = fetch_page(url, timeout=timeout, proxies=proxies)
        except Exception as exc:  # noqa: BLE001 — 单页故障不阻断其他页
            log(f"[page:{name}] fetch failed: {exc}")
            continue
        norm = normalize_content(content)
        fp = fingerprint(norm)
        snap_path = pages_dir / f"{name}.txt"
        if not snap_path.exists():
            snap_path.write_text(fp, encoding="utf-8")
            log(f"[page:{name}] baseline saved ({len(norm)} chars)")
            continue
        old_line = snap_path.read_text(encoding="utf-8").strip()
        if old_line == fp:
            continue
        try:
            old_len = int(old_line.split()[1])
        except (IndexError, ValueError):
            old_len = 0  # 兼容历史全文快照：无法解析长度时按 0 处理
        changes.append({
            "name": name,
            "url": url,
            "title": extract_title(content) or name,
            "old_len": old_len,
            "new_len": len(norm),
            "snapshot_path": snap_path,
            "snapshot_text": fp,
        })
        log(f"[page:{name}] CHANGED ({old_len} -> {len(norm)} chars)")
    return changes


def build_page_message(change):
    return (
        f"📄 [页面更新] <b>{esc(change['title'])}</b>\n\n"
        f"<blockquote>监控页面内容发生变化（{change['old_len']} → {change['new_len']} 字符），"
        f"可能与定价或优惠调整有关，点下方来源查看最新内容。</blockquote>\n\n"
        f'via <a href="{html.escape(change["url"], quote=True)}">{esc(change["name"])}</a>'
    )
