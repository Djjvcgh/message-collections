"""邮件兜底渠道（可选）。

配置 SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / SMTP_TO 后，
在 config/settings.yml 打开 email 开关即可启用。
"""
import re
import smtplib
from email.header import Header
from email.mime.text import MIMEText

from .notify_base import NotifyChannel

TAG_RE = re.compile(r"<[^>]+>")


def subject_from(text, prefix="[AI情报]"):
    first_line = TAG_RE.sub("", text.splitlines()[0]).strip()
    return f"{prefix} {first_line}"[:120]


class EmailChannel(NotifyChannel):
    name = "email"

    def __init__(self, host, port, user, password, to_addr, prefix="[AI情报]"):
        if not all([host, port, user, password, to_addr]):
            raise RuntimeError("SMTP_* 配置不完整（该渠道当前为预留状态）")
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self.to_addr = to_addr
        self.prefix = prefix

    def send(self, text, parse_mode=None):
        msg = MIMEText(text, "plain", "utf-8")
        msg["Subject"] = Header(subject_from(text, self.prefix), "utf-8")
        msg["From"] = self.user
        msg["To"] = self.to_addr
        with smtplib.SMTP_SSL(self.host, self.port, timeout=30) as smtp:
            smtp.login(self.user, self.password)
            smtp.sendmail(self.user, [self.to_addr], msg.as_string())
