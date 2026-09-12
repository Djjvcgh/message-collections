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
    title_link = (
        f'<a href="{html.escape(item["url"] or "", quote=True)}">'
        f"<b>{esc(item['title'])}</b></a>"
    )
    src = item["source"] + (f" · {item['tier_label']}" if item["tier_label"] else "")
    return f"🎁 [福利] {title_link}\n　来源：{esc(src)}"
