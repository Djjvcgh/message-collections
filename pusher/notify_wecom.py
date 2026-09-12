"""企业微信群机器人（预留渠道）。

当前处于预留状态：在 config/settings.yml 打开 wecom 开关并配置
WEWORK_WEBHOOK_URL 环境变量后即可启用，主逻辑无需改动。
企业微信原生支持 markdown，忽略 parse_mode。
"""
import requests

from .notify_base import NotifyChannel


class WeComChannel(NotifyChannel):
    name = "wecom"

    def __init__(self, webhook_url=None):
        self.webhook_url = webhook_url
        if not webhook_url:
            raise RuntimeError("WEWORK_WEBHOOK_URL 未配置（该渠道当前为预留状态）")

    def send(self, text, parse_mode=None):
        resp = requests.post(
            self.webhook_url,
            json={"msgtype": "markdown", "markdown": {"content": text}},
            timeout=30,
        )
        resp.raise_for_status()
