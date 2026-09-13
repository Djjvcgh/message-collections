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
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .digest import GROUP_LABELS, build_groups, render_group_message
from .fetch_radar import fetch_latest_24h, freshness, normalize_all
from .filter import WelfareFilter, drop_pushed
from .notify_base import send_all
from .notify_email import EmailChannel
from .notify_telegram import TelegramChannel, build_instant_message
from .notify_wecom import WeComChannel
from .state import State
from .translate import is_mostly_english, translate_title
from .watch_pages import build_page_message, check_pages

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
    """翻译将要展示的标题与摘要（英文→中文），译文有缓存。"""
    proxies = _outbound_proxies()
    for item in items:
        if is_mostly_english(item["title"]):
            item["title"] = translate_title(item["title"], proxies=proxies)
        if is_mostly_english(item.get("reason") or ""):
            item["reason"] = translate_title(item["reason"], proxies=proxies)
    return items


def _run_page_watch(cfg, channels, dry_run):
    """M2 页面 diff 监控：内容变化即推送，推送成功才更新快照。"""
    pages = cfg.get("page_watch") or []
    if not pages:
        return
    changes = check_pages(pages, STATE_PATH.parent / "pages",
                          proxies=_outbound_proxies(), log=log)
    if not changes:
        return
    log(f"page changes: {len(changes)}")
    for change in changes:
        message = build_page_message(change)
        if dry_run:
            log(message + "\n---")
            continue
        failed = send_all(channels, message, log=log)
        if failed:
            log(f"page change push failed, snapshot kept for retry: {change['name']}")
            continue
        change["snapshot_path"].write_text(change["snapshot_text"], encoding="utf-8")


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
    _run_page_watch(cfg, channels, dry_run)


def run_digest(cfg, state, wf, channels, dry_run, slot, settings):
    data = fetch_latest_24h(cfg["radar"]["latest_24h_url"])
    ok, age = freshness(data, cfg["radar"].get("max_age_hours", 36))
    if not ok:
        log(f"radar data stale ({age:.1f}h > limit), skip this round")
        return
    now_bj = datetime.now(BJT)
    slot = slot or ("morning" if now_bj.hour < 12 else "evening")
    items = normalize_all(data)
    limits = settings.get("digest", {}).get("limits", {})
    groups = build_groups(items, wf, limits)

    # 按组去重：某组发送失败只补发该组，不会全量重发
    pending = []
    for key, emoji, name in GROUP_LABELS:
        gitems = groups.get(key)
        if not gitems:
            continue
        gkey = f"{now_bj:%Y-%m-%d}_{slot}_{key}"
        if state.has_digest(gkey):
            log(f"digest {gkey} already sent, skip")
            continue
        pending.append((gkey, emoji, name, gitems))
    if not pending:
        log("nothing to send")
        return

    date_str = f"{now_bj:%Y-%m-%d}"
    time_str = f"{now_bj:%H:%M}"
    for i, (gkey, emoji, name, gitems) in enumerate(pending):
        _translate_shown(gitems)
        text = render_group_message(date_str, time_str, emoji, name, gitems)
        if dry_run:
            log(text + "\n\n========== 消息结束 ==========")
            continue
        failed = send_all(channels, text, log=log)
        if failed:
            log(f"group {name} send failed, will retry next run")
            continue
        state.mark_digest(gkey)
        _save_state(state)
        if i < len(pending) - 1:
            time.sleep(0.8)  # 多条连发间隔，避免触发 Telegram 限频


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
