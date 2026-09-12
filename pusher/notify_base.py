"""推送渠道抽象。

新渠道（如企业微信群机器人）只需：
1. 实现本类的 send()；
2. 在 config/settings.yml 的 channels 下增加开关；
3. 需要的密钥加为 GitHub Secret。
主逻辑（run.py）无需改动。
"""


class NotifyChannel:
    name = "base"

    def send(self, text, parse_mode=None):
        raise NotImplementedError


def send_all(channels, text, parse_mode=None, log=print):
    """向所有渠道发送；单渠道失败不影响其他渠道。返回失败的渠道名列表。

    parse_mode=None 表示"交给各渠道用自身默认值"（如 Telegram 用 HTML）。
    """
    failed = []
    for ch in channels:
        try:
            if parse_mode is None:
                ch.send(text)
            else:
                ch.send(text, parse_mode=parse_mode)
            log(f"[{ch.name}] sent ({len(text)} chars)")
        except Exception as exc:  # noqa: BLE001 — 单渠道故障必须被隔离
            log(f"[{ch.name}] send failed: {exc}")
            failed.append(ch.name)
    return failed
