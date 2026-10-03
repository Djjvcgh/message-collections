"""Telegram Bot 推送（主渠道）。

GitHub Actions 上直连 api.telegram.org；本地调试可通过
TELEGRAM_PROXY 环境变量走代理（如 http://127.0.0.1:7897）。
"""
import html

import requests

from .notify_base import NotifyChannel

API = "https://api.telegram.org"

# 两类即时消息的标识：福利可直接领取，限时情报是错过就没的窗口
KIND_HEADERS = {
    "welfare": "🎁 [福利]",
    "opportunity": "⏳ [限时]",
}
DEFAULT_HEADER = "🎁 [福利]"


def esc(s):
    return html.escape(s or "", quote=False)


def _get(item, field, default=""):
    if isinstance(item, dict):
        value = item.get(field)
    else:
        value = getattr(item, field, None)
    return default if value is None else value


class TelegramChannel(NotifyChannel):
    name = "telegram"

    def __init__(self, token, chat_id, proxy=None, link_preview=False):
        if not token or not chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 未配置")
        self.token = token
        self.chat_id = chat_id
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.link_preview = link_preview

    def send(self, text, parse_mode="HTML"):
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": not self.link_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        resp = requests.post(
            f"{API}/bot{self.token}/sendMessage",
            json=payload,
            timeout=30,
            proxies=self.proxies,
        )
        if resp.status_code >= 400:
            # 带上 Telegram 的错误描述，便于定位（如 can't parse entities）
            raise RuntimeError(f"Telegram API {resp.status_code}: {resp.text[:200]}")


def build_instant_message(item, kind="welfare"):
    """即时消息：标题 → 引用块摘要 → via 来源，空行分隔。

    kind 决定抬头标识；摘要缺失时省略引用块，不做留白。
    """
    header = KIND_HEADERS.get(kind, DEFAULT_HEADER)
    lines = [f"{header} <b>{esc(_get(item, 'title'))}</b>"]
    reason = (_get(item, "reason") or _get(item, "summary")).strip()
    if reason:
        lines.append(f"<blockquote>{esc(reason)}</blockquote>")
    url = html.escape(_get(item, "url"), quote=True)
    src = _get(item, "source") or "原文链接"
    tier_label = _get(item, "tier_label")
    tier = f" · {tier_label}" if tier_label else ""
    if url:
        lines.append(f'via <a href="{url}">{esc(src)}</a>{esc(tier)}')
    else:
        lines.append(f"via {esc(src)}{esc(tier)}")
    return "\n\n".join(lines)
