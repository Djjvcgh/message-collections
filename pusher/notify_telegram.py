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


def link(url):
    return f'<a href="{html.escape(url or "", quote=True)}">{html.escape(url or "")}</a>'


class TelegramChannel(NotifyChannel):
    name = "telegram"

    def __init__(self, token, chat_id, proxy=None):
        if not token or not chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 未配置")
        self.token = token
        self.chat_id = chat_id
        self.proxies = {"http": proxy, "https": proxy} if proxy else None

    def send(self, text, parse_mode="HTML"):
        resp = requests.post(
            f"{API}/bot{self.token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": False,
            },
            timeout=30,
            proxies=self.proxies,
        )
        resp.raise_for_status()


def build_instant_message(item):
    lines = [
        f"🎁 [福利] <b>{esc(item['title'])}</b>",
        f"来源：{esc(item['source'])} · {esc(item['tier_label'])}",
        link(item["url"]),
    ]
    return "\n".join(lines)
