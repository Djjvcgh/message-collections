"""统一入口。

用法：
  python -m pusher.run                      # 拉取全部信源，命中即推送
  python -m pusher.run --dry-run            # 只打印不发送
  python -m pusher.run --probe              # 逐源探测可用性（不推送、不写 state）
  python -m pusher.run --report             # 逐源漏斗：抓了多少 / 命中多少 / 为什么没推
  python -m pusher.run --only linuxdo-free  # 只跑指定信源 id（可重复 / 逗号分隔）
  python -m pusher.run --limit 0            # 覆盖本轮推送条数上限

密钥从环境变量读取；本地调试可在仓库根目录放 .env（已被 .gitignore 排除）。
"""
import argparse
import os
import sys
import time
from pathlib import Path

import yaml

from . import enrich
from .filter import (
    KIND_OPPORTUNITY,
    KIND_WELFARE,
    MAX_REASON_CHARS,
    WelfareFilter,
    dedupe,
)
from .facts import is_actionable
from .notify_base import send_all
from .notify_email import EmailChannel
from .notify_telegram import (
    TelegramChannel,
    build_instant_message,
    content_line_count,
)
from .notify_wecom import WeComChannel
from .sources import SOURCE_TYPES  # noqa: F401 — 触发信源注册
from .sources.base import load_sources
from .state import State
from .translate import is_mostly_english, translate_title

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "state.json"
SETTINGS_PATH = ROOT / "config" / "settings.yml"
KEYWORDS_PATH = ROOT / "config" / "keywords.yml"
SOURCES_PATH = ROOT / "config" / "sources.yml"
DEFAULT_MAX_PUSH = 8
DEFAULT_MAX_ENRICH = 10


def ensure_utf8_console():
    """把标准输出切到 UTF-8。

    Windows 控制台默认 GBK，打印 `🎁 [福利]` 这类抬头会直接抛
    UnicodeEncodeError（实测 --dry-run 在本地跑到第一条消息就崩），
    而 --dry-run 正是文档里推荐的验证方式。errors="replace" 兜底，
    保证即使重设编码失败也不会中断推送流程。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, TypeError, ValueError, OSError):
            pass


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


def _outbound_proxies():
    """本地联调时复用 TELEGRAM_PROXY 作为出站代理；Actions 上为 None 直连。"""
    proxy = os.getenv("TELEGRAM_PROXY")
    return {"http": proxy, "https": proxy} if proxy else None


def build_channels(settings):
    channels = []
    conf = settings.get("channels", {})
    tg = conf.get("telegram", {})
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
    wc = conf.get("wecom", {})
    if wc.get("enabled"):
        try:
            channels.append(WeComChannel(os.getenv("WEWORK_WEBHOOK_URL")))
        except RuntimeError as exc:
            log(f"[wecom] disabled: {exc}")
    em = conf.get("email", {})
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


def _translate_shown(items):
    """翻译将要展示的标题与摘要（英文→中文），译文有缓存。"""
    proxies = _outbound_proxies()
    for item in items:
        if is_mostly_english(item.title):
            item.title = translate_title(item.title, proxies=proxies)
        if is_mostly_english(item.summary):
            item.summary = translate_title(item.summary, proxies=proxies)
    return items


def build_filter(kw, settings=None):
    """按 config/keywords.yml 组装过滤器（硬词 + 宽词 + 排除词）。

    max_reason_chars 可被 settings.yml → filter.max_reason_chars 覆盖，
    调参不必改代码。
    """
    limits = (settings or {}).get("filter") or {}
    return WelfareFilter(
        kw.get("welfare_keywords", []),
        kw.get("opportunity_keywords", []),
        kw.get("exclude_words", []),
        weak_welfare_keywords=kw.get("weak_welfare_keywords", []),
        weak_opportunity_keywords=kw.get("weak_opportunity_keywords", []),
        max_reason_chars=int(limits.get("max_reason_chars", MAX_REASON_CHARS)),
    )


def collect(sources, proxies=None, log_fn=log):
    """逐源采集；单源故障只跳过该源，不影响其他源。"""
    items = []
    for source in sources:
        try:
            items.extend(source.fetch(log=log_fn, proxies=proxies))
        except Exception as exc:  # noqa: BLE001 — 单源故障必须被隔离
            log_fn(f"[source:{source.id}] fetch failed: {exc}")
    return items


def probe(sources, proxies=None, log_fn=log):
    """逐源探测可用性与样例条目，不推送也不写 state。"""
    report = []
    for source in sources:
        try:
            items = source.fetch(log=log_fn, proxies=proxies)
        except Exception as exc:  # noqa: BLE001 — 探测本身就是为了看失败
            log_fn(f"[probe:{source.id}] FAILED: {exc}")
            report.append({"id": source.id, "ok": False, "error": str(exc), "count": 0})
            continue
        log_fn(f"[probe:{source.id}] OK {len(items)} items")
        for item in items[:3]:
            log_fn(f"    - {item.title or '(无标题)'} | {item.url[:110]}")
        report.append(
            {
                "id": source.id,
                "ok": True,
                "error": "",
                "count": len(items),
                "sample": [
                    {"title": i.title, "url": i.url, "summary": i.summary[:120]}
                    for i in items[:5]
                ],
            }
        )
    usable = [r for r in report if r["ok"]]
    log_fn(f"\nprobe summary: {len(usable)}/{len(report)} sources usable")
    return report


def select_pushes(items, max_push):
    """排序并按上限截取：先福利后限时，同类按信源权威度。"""
    welfare = sorted(
        (i for i in items if i.extra.get("kind") == KIND_WELFARE), key=lambda i: i.sort_key()
    )
    opportunity = sorted(
        (i for i in items if i.extra.get("kind") == KIND_OPPORTUNITY),
        key=lambda i: i.sort_key(),
    )
    ordered = welfare + opportunity
    return ordered[:max_push] if max_push > 0 else ordered


def save_state(state, path=STATE_PATH):
    state.prune()
    path.parent.mkdir(parents=True, exist_ok=True)
    state.save(path)


def add_stat(stats, key, count=1):
    stats[key] = stats.get(key, 0) + count


def format_stats(stats):
    """把漏斗计数压成一行，Actions 日志里可直接复盘「为什么没推」。"""
    if not stats:
        return "(无)"
    return " ".join(f"{key}={stats[key]}" for key in sorted(stats))


def build_enrich_fn(state_path=None, settings=None, persist=True):
    """构造正文兜底回调：缓存写在 state/bodies.json。

    persist=False（dry-run）时只读缓存、不落盘，保持「dry-run 不写 state」的约定。
    """
    conf = (settings or {}).get("enrich") or {}
    cache_path = Path(state_path).parent / "bodies.json" if state_path else enrich.CACHE_PATH
    cache = enrich.load_cache(cache_path)
    timeout = float(conf.get("timeout", enrich.DEFAULT_TIMEOUT))
    save = (lambda data: enrich.save_cache(data, cache_path)) if persist else None

    def run_enrich(items, proxies=None, max_items=DEFAULT_MAX_ENRICH, log=log):
        return enrich.enrich_items(
            items,
            cache,
            timeout=timeout,
            max_items=max_items,
            proxies=proxies,
            save=save,
            log=log,
        )

    return run_enrich


def classify_items(fresh, wf, stats=None):
    """把去重后的条目分成可推与不可推，并把原因记进 stats。

    stats 里的键就是「为什么没推」，--report 直接拿它出逐源漏斗，
    这样「筛选是不是太严」永远有数据可查，不必靠猜。
    """
    stats = stats if stats is not None else {}
    classified = []
    for item in fresh:
        kind, reason = wf.explain(item)
        if not kind:
            add_stat(stats, f"drop:{reason}")
            continue
        # 交易/灌水比例高的社区源：必须含明确优惠信息才推，否则噪声远多于价值
        if item.require_actionable and not is_actionable(item):
            add_stat(stats, "drop:source-not-actionable")
            continue
        item.extra["kind"] = kind
        item.extra["match"] = reason
        classified.append(item)
    return classified


def render_candidates(items, proxies=None, log=log, enrich_fn=None, max_enrich=DEFAULT_MAX_ENRICH):
    """补正文 → 翻译 → 渲染，返回 {item.key(): 消息}。

    渲染只做一次：正文闸门、dry-run、真实投递都复用同一份消息，
    避免「闸门看到的」和「用户收到的」不是同一条。
    """
    if enrich_fn is not None and items:
        filled = enrich_fn(items, proxies=proxies, max_items=max_enrich, log=log)
        if filled:
            log(f"enriched {filled} items from original pages")
    _translate_shown(items)
    return {item.key(): build_instant_message(item, kind=item.extra["kind"]) for item in items}


def apply_body_gate(items, require_body, stats, messages, log=log):
    """正文闸门：丢掉「渲染后除了标题没有别的内容」的条目。

    用户的第一条反馈就是消息缺内容，所以默认不推空壳消息；
    条数少时宁可少推，也不要推一条只有标题的资讯。
    """
    if not require_body:
        return list(items)
    kept = []
    for item in items:
        if content_line_count(messages.get(item.key(), "")) < 1:
            add_stat(stats, "drop:no-body")
            log(f"[skip:no-body] {item.title[:60]}")
            continue
        kept.append(item)
    return kept


def run_once(
    cfg,
    wf,
    state,
    channels,
    dry_run=False,
    max_push=None,
    proxies=None,
    log=log,
    state_path=None,
    require_body=True,
    max_enrich=DEFAULT_MAX_ENRICH,
    enrich_fn=None,
):
    """采集 → 去重 → 分类 → 补正文 → 翻译/渲染 → 正文闸门 → 排序 → 推送 → 落盘。

    返回本轮推送成功的条数。dry-run 不发送、不记账、不写 state。
    失败投递的条目本轮不记账，下一轮自动重试。
    """
    sources = load_sources(cfg, log=log)
    if not sources:
        log("no usable source configured")
        return 0
    raw = collect(sources, proxies=proxies, log_fn=log)
    fresh = dedupe(raw, state)
    log(f"collected {len(raw)} items, {len(fresh)} after dedupe")

    stats = {}
    classified = classify_items(fresh, wf, stats)
    log(
        f"hits: {len(classified)} "
        f"(welfare {sum(1 for i in classified if i.extra['kind'] == KIND_WELFARE)}, "
        f"opportunity {sum(1 for i in classified if i.extra['kind'] == KIND_OPPORTUNITY)})"
    )
    if not classified:
        log(f"funnel: {format_stats(stats)}")
        return 0

    messages = render_candidates(
        classified,
        proxies=proxies,
        log=log,
        enrich_fn=enrich_fn,
        max_enrich=max_enrich,
    )
    candidates = apply_body_gate(classified, require_body, stats, messages, log=log)
    if not candidates:
        log("all hits lack a readable body, nothing to push")
        log(f"funnel: {format_stats(stats)}")
        return 0

    to_send = select_pushes(candidates, DEFAULT_MAX_PUSH if max_push is None else max_push)
    log(f"funnel: {format_stats(stats)}")
    sent = 0
    for index, item in enumerate(to_send):
        message = messages[item.key()]
        if dry_run:
            log(message + "\n---")
            continue
        failed = send_all(channels, message, log=log)
        if failed:
            log(f"delivery failed, will retry next run: {item.url or item.title}")
            continue
        state.mark_pushed(item.url, item.title)
        sent += 1
        if index < len(to_send) - 1:
            time.sleep(0.8)  # 多条连发间隔，避免触发 Telegram 限频
    if not dry_run:
        save_state(state, state_path or STATE_PATH)
    return sent


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pusher")
    parser.add_argument("--dry-run", action="store_true", help="只打印不发送")
    parser.add_argument("--probe", action="store_true", help="逐源探测可用性")
    parser.add_argument("--report", action="store_true", help="逐源漏斗报告（不推送、不写 state）")
    parser.add_argument("--only", default=None, help="只跑指定信源 id（逗号分隔）")
    parser.add_argument("--limit", type=int, default=None, help="本轮推送条数上限")
    args = parser.parse_args(argv)

    ensure_utf8_console()
    load_env()
    cfg = load_yaml(SOURCES_PATH)
    kw = load_yaml(KEYWORDS_PATH)
    settings = load_yaml(SETTINGS_PATH)

    state = State.load(STATE_PATH)
    wf = build_filter(kw, settings)
    only = [s.strip() for s in (args.only or "").split(",") if s.strip()]
    proxies = _outbound_proxies()

    if args.probe:
        sources = load_sources(cfg, only=only, log=log)
        if not sources:
            log("no source matched")
            return 1
        probe(sources, proxies=proxies, log_fn=log)
        return 0

    if args.report:
        from .report import funnel_report

        return funnel_report(cfg, wf, state, only=only, proxies=proxies, log=log)

    channels = [] if args.dry_run else build_channels(settings)
    if not args.dry_run and not channels:
        log("no usable channel configured, abort")
        return 1

    push_conf = settings.get("push") or {}
    limit = args.limit
    if limit is None:
        limit = int(push_conf.get("max_per_run", DEFAULT_MAX_PUSH))

    sent = run_once(
        cfg,
        wf,
        state,
        channels,
        dry_run=args.dry_run,
        max_push=limit,
        proxies=proxies,
        state_path=STATE_PATH,
        require_body=bool(push_conf.get("require_body", True)),
        max_enrich=int(push_conf.get("max_enrich", DEFAULT_MAX_ENRICH)),
        enrich_fn=build_enrich_fn(STATE_PATH, settings, persist=not args.dry_run),
    )
    log(f"done: {sent} pushed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
