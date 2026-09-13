"""Telegram Bot 推送（主渠道）。

GitHub Actions 上直连 api.telegram.org；本地调试可通过
TELEGRAM_PROXY 环境变量走代理（如 http://127.0.0.1:7897）。
"""
import html

import requests

from .notify_base import NotifyChannel

API = "https://api.telegram.org"


def esc(s):
    return html.escape(s or "", quote=False)


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


def build_instant_message(item):
    """即时福利消息，与日报条目同款样式：标题 → 引用块摘要 → via 来源，空行分隔。"""
    lines = [f"🎁 [福利] <b>{esc(item['title'])}</b>"]
    reason = (item.get("reason") or "").strip()
    if reason:
        lines.append(f"<blockquote>{esc(reason)}</blockquote>")
    url = html.escape(item.get("url") or "", quote=True)
    src = item.get("source") or "原文链接"
    tier = f" · {item['tier_label']}" if item.get("tier_label") else ""
    if url:
        lines.append(f'via <a href="{url}">{esc(src)}</a>{esc(tier)}')
    else:
        lines.append(f"via {esc(src)}{esc(tier)}")
    return "\n\n".join(lines)
