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
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": False,
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
    lines = [
        f"🎁 [福利] <b>{esc(item['title'])}</b>",
        f"来源：{esc(item['source'])} · {esc(item['tier_label'])}",
        link(item["url"]),
    ]
    return "\n".join(lines)
