"""信源候选体检：对候选 Telegram 频道 / RSS 站点跑一遍「抓取 + 命中判定」。

沿用仓库铁律：**先 probe 通过，再进正式清单**（频道名靠记忆猜会撞到空壳，
见 DESIGN.md 的 yangmaoshe 教训）。这里只打印结果，不改 config/sources.yml。

准入闸门（两个都要满足）：
1. 抓取 ≥ MIN_ITEMS 条（空壳/被墙的信源直接淘汰）
2. 用真实词表判定后 ≥ MIN_HITS 条命中（能产出福利，而不是只有资讯）

用法：
    python probe/probe_candidates.py
    python probe/probe_candidates.py --only tg-freebie,feed-sspai
    python probe/probe_candidates.py --feed https://example.com/feed
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pusher.filter import WelfareFilter, dedupe  # noqa: E402
from pusher.facts import is_actionable  # noqa: E402
from pusher.run import KEYWORDS_PATH, SETTINGS_PATH, build_filter, load_yaml  # noqa: E402
from pusher.sources.feed import fetch_feed_text, parse_feed_items  # noqa: E402
from pusher.sources.telegram import fetch_channel_html, parse_channel_items  # noqa: E402
from pusher.state import State  # noqa: E402

MIN_ITEMS = 5
MIN_HITS = 1
SAMPLE = 4

# 候选 Telegram 频道（公开预览页，无需鉴权）
#
# 2026-10-05 已实测结论（不必重复探测）：
#   空壳（0 条消息块）：giveaway / freenet / letsdeel / dealsfreenet / aliExpressDeals /
#     giveawayoftheday / DealsFreebie / vpscangku / vpsbaipiao / baipiaovps / freevps /
#     hostloccom / cnfreebies / yangmaoshe（DESIGN 里的老教训）
#   有内容但不启用：freebie（20 条里 12 条是加密空投刷屏）、
#     vpsdeals（20 条 0 命中，印度 VPS 促销）、freebies（20 条 1 命中：Telegram Premium 抽奖）
CANDIDATE_CHANNELS = [
    "maoyangmao",
    "haoyangmao",
    "yangmao",
    "fulizhijia",
    "youhuiquan",
    "baipiao8",
    "freebiecn",
    "hostshare",
    "idcshare",
    "vpsyouhui",
]

# 候选 RSS（羊毛站 / 线报站 / 促销聚合 / 社区福利版块）
#
# 2026-10-05 已实测结论：sspai / 小众软件 / free.com.tw（繁体词表未覆盖）/
#   giveawayoftheday / nodeseek-free（f=7 全是二手交易）/ 52pojie 16·41·42 各 20 条 0 命中；
#   smzdm 主站 feed 返回 0 条（非 RSS）；9to5toys 50 条、ghacks 40 条命中 0（消费电子促销）；
#   bensbargains 连接被中断。
CANDIDATE_FEEDS = [
    ("r-freebies", "https://www.reddit.com/r/freebies/.rss"),
    ("r-freebies-wide", "https://www.reddit.com/r/freebies+eFreebies+AppHookup+freegames+GameDealsFree+FreeGameFindings+FreeEBOOKS/.rss"),
    ("r-freebies-wider", "https://www.reddit.com/r/freebies+eFreebies+AppHookup+freegames+GameDealsFree+FreeGameFindings+FreeEBOOKS+ebookdeals+androidfreebies+iosgaming/.rss"),
]


def probe_channel(channel, wf, proxies=None):
    try:
        page = fetch_channel_html(channel, timeout=20, proxies=proxies)
    except Exception as exc:  # noqa: BLE001 — 探测就是为了看失败
        return {"id": f"tg-{channel}", "ok": False, "error": str(exc)[:120], "items": [], "hits": []}
    items = parse_channel_items(page, channel, source_id=f"tg-{channel}", source_name=f"t.me/{channel}")
    return {"id": f"tg-{channel}", "ok": True, "error": "", "items": items, "hits": classify(items, wf)}


def probe_feed(name, url, wf, proxies=None):
    try:
        text = fetch_feed_text(url, timeout=20, proxies=proxies)
    except Exception as exc:  # noqa: BLE001
        return {"id": f"feed-{name}", "ok": False, "error": str(exc)[:120], "items": [], "hits": []}
    items = parse_feed_items(text, url, source_id=f"feed-{name}")
    return {
        "id": f"feed-{name}",
        "ok": True,
        "error": "",
        "url": url,
        "items": items,
        "hits": classify(items, wf),
    }


def classify(items, wf):
    """与 run.py 同款判定：分类命中 + 源级可推门槛（这里对所有源都按严格口径统计）。"""
    hits = []
    for item in dedupe(items, State()):
        kind = wf.classify(item)
        if not kind:
            continue
        if not is_actionable(item):
            continue
        item.extra["kind"] = kind
        hits.append(item)
    return hits


def report_row(row, log=print):
    flag = "OK " if row["ok"] else "ERR"
    log(f"[{flag}] {row['id']:<26} 抓取 {len(row['items']):>3} 条 / 命中 {len(row['hits']):>2} 条  {row['error']}")
    for item in row["hits"][:SAMPLE]:
        log(f"        ✓ [{item.extra['kind']}] {item.title[:70]}")
    if not row["hits"] and row["items"]:
        for item in row["items"][:2]:
            log(f"        · {item.title[:70]}")
    verdict = row["ok"] and len(row["items"]) >= MIN_ITEMS and len(row["hits"]) >= MIN_HITS
    return verdict


def main(argv=None):
    parser = argparse.ArgumentParser(prog="probe-candidates")
    parser.add_argument("--only", default="", help="只测指定候选 id（逗号分隔）")
    parser.add_argument("--feed", action="append", default=[], help="临时追加 RSS 候选 URL")
    parser.add_argument("--channel", action="append", default=[], help="临时追加 Telegram 频道")
    args = parser.parse_args(argv)

    settings = load_yaml(SETTINGS_PATH)
    wf = build_filter(load_yaml(KEYWORDS_PATH), settings)
    wanted = {w.strip() for w in args.only.split(",") if w.strip()}

    channels = CANDIDATE_CHANNELS + args.channel
    feeds = CANDIDATE_FEEDS + [(f"custom-{i}", url) for i, url in enumerate(args.feed)]

    rows = []
    print("== Telegram 候选 ==")
    for channel in channels:
        if wanted and f"tg-{channel}" not in wanted:
            continue
        row = probe_channel(channel, wf)
        rows.append(row)
        report_row(row)
    print("\n== RSS 候选 ==")
    for name, url in feeds:
        if wanted and f"feed-{name}" not in wanted:
            continue
        row = probe_feed(name, url, wf)
        rows.append(row)
        report_row(row)

    passed = [r["id"] for r in rows if r["ok"] and len(r["items"]) >= MIN_ITEMS and len(r["hits"]) >= MIN_HITS]
    print("\n== 结论 ==")
    print(f"准入闸门：抓取 ≥{MIN_ITEMS} 条 且 命中 ≥{MIN_HITS} 条")
    print(f"建议启用（{len(passed)}）: {', '.join(passed) if passed else '（无）'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
