"""统一入口。

用法：
  python -m pusher.run instant            # 拉取 radar，福利命中即时推送
  python -m pusher.run digest             # 组装并推送日报（按 slot 去重）
  python -m pusher.run instant --dry-run  # 只打印不发送，用于本地调试

密钥从环境变量读取；本地调试可在仓库根目录放 .env（已被 .gitignore 排除）。
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .digest import build_groups, render_digest
from .fetch_radar import fetch_latest_24h, freshness, normalize_all
from .filter import WelfareFilter, drop_pushed
from .notify_base import send_all
from .notify_email import EmailChannel
from .notify_telegram import TelegramChannel, build_instant_message
from .notify_wecom import WeComChannel
from .state import State
from .translate import is_mostly_english, translate_title

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "state.json"
BJT = timezone(timedelta(hours=8))  # 北京时间固定 UTC+8


def log(msg):
    print(msg, flush=True)


def load_env(path=ROOT / ".env"):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_channels(settings):
    channels = []
    tg = settings.get("channels", {}).get("telegram", {})
    if tg.get("enabled"):
        try:
            proxy = os.getenv("TELEGRAM_PROXY") or tg.get("proxy") or None
            channels.append(
                TelegramChannel(
                    os.getenv("TELEGRAM_BOT_TOKEN"),
                    os.getenv("TELEGRAM_CHAT_ID"),
                    proxy=proxy,
                    link_preview=tg.get("link_preview", False),
                )
            )
        except RuntimeError as exc:
            log(f"[telegram] disabled: {exc}")
    wc = settings.get("channels", {}).get("wecom", {})
    if wc.get("enabled"):
        try:
            channels.append(WeComChannel(os.getenv("WEWORK_WEBHOOK_URL")))
        except RuntimeError as exc:
            log(f"[wecom] disabled: {exc}")
    em = settings.get("channels", {}).get("email", {})
    if em.get("enabled"):
        try:
            channels.append(
                EmailChannel(
                    os.getenv("SMTP_HOST"),
                    os.getenv("SMTP_PORT"),
                    os.getenv("SMTP_USER"),
                    os.getenv("SMTP_PASSWORD"),
                    os.getenv("SMTP_TO"),
                )
            )
        except RuntimeError as exc:
            log(f"[email] disabled: {exc}")
    return channels


def _outbound_proxies():
    """本地联调时复用 TELEGRAM_PROXY 作为出站代理；Actions 上为 None 直连。"""
    proxy = os.getenv("TELEGRAM_PROXY")
    return {"http": proxy, "https": proxy} if proxy else None


def _translate_shown(items):
    """只翻译将要展示的条目标题（英文→中文），译文有缓存。"""
    proxies = _outbound_proxies()
    for item in items:
        if is_mostly_english(item["title"]):
            item["title"] = translate_title(item["title"], proxies=proxies)
    return items


def run_instant(cfg, state, wf, channels, dry_run):
    data = fetch_latest_24h(cfg["radar"]["latest_24h_url"])
    ok, age = freshness(data, cfg["radar"].get("max_age_hours", 36))
    if not ok:
        log(f"radar data stale ({age:.1f}h > limit), skip this round")
        return
    items = normalize_all(data)
    new = drop_pushed(wf.hits(items), state)
    log(f"welfare hits: {len(new)} (deduped)")
    _translate_shown(new)
    for item in new:
        message = build_instant_message(item)
        if dry_run:
            log(message + "\n---")
            continue
        failed = send_all(channels, message, log=log)
        if failed:
            log(f"delivery failed, will retry next run: {item['url']}")
            continue
        state.mark_pushed(item["url"])
    if not dry_run:
        _save_state(state)


def run_digest(cfg, state, wf, channels, dry_run, slot, settings):
    data = fetch_latest_24h(cfg["radar"]["latest_24h_url"])
    ok, age = freshness(data, cfg["radar"].get("max_age_hours", 36))
    if not ok:
        log(f"radar data stale ({age:.1f}h > limit), skip this round")
        return
    now_bj = datetime.now(BJT)
    slot = slot or ("morning" if now_bj.hour < 12 else "evening")
    key = f"{now_bj:%Y-%m-%d}_{slot}"
    if state.has_digest(key):
        log(f"digest {key} already sent, skip")
        return
    items = normalize_all(data)
    limits = settings.get("digest", {}).get("limits", {})
    groups = build_groups(items, wf, limits)
    for shown in groups.values():
        _translate_shown(shown)
    text = render_digest(f"{now_bj:%m-%d}", groups)
    if dry_run:
        log(text)
    else:
        failed = send_all(channels, text, log=log)
        if failed:
            log("digest send failed on all channels, will retry next run")
            return
        state.mark_digest(key)
        _save_state(state)


def _save_state(state):
    state.prune()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state.save(STATE_PATH)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pusher")
    parser.add_argument("mode", choices=["instant", "digest"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--slot", default=None, help="日报槽位 morning/evening")
    args = parser.parse_args(argv)

    load_env()
    cfg = load_yaml(ROOT / "config" / "sources.yml")
    kw = load_yaml(ROOT / "config" / "keywords.yml")
    settings = load_yaml(ROOT / "config" / "settings.yml")

    state = State.load(STATE_PATH)
    wf = WelfareFilter(kw.get("welfare_keywords", []), kw.get("exclude_words", []))
    channels = [] if args.dry_run else build_channels(settings)
    if not args.dry_run and not channels:
        log("no usable channel configured, abort")
        return 1

    if args.mode == "instant":
        run_instant(cfg, state, wf, channels, args.dry_run)
    else:
        run_digest(cfg, state, wf, channels, args.dry_run, args.slot, settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
