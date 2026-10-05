"""逐源漏斗报告：回答「为什么今天只推了一条 / 是不是筛得太严」。

对每个启用的信源单独采集，复用**同一套**分类与闸门逻辑
（``run.classify_items`` 与渲染器），所以报告里的数字就是线上会发生的数字，
不会出现「报告说该推、实际没推」这种两套逻辑打架的情况。

只读：不推送、不写 state、不抓原文页（正文覆盖用「源数据里有没有正文」衡量，
需要抓页面的部分交给正常轮的 enrich）。

用法：
    python -m pusher.run --report
    python -m pusher.run --report --only radar
"""
from .notify_telegram import build_instant_message, content_line_count
from .filter import KIND_OPPORTUNITY, KIND_WELFARE, dedupe
from .run import (
    add_stat,
    classify_items,
    collect,
    format_stats,
    load_sources,
)

SAMPLE_TITLES = 3


def enabled_specs(cfg, only=None):
    """配置里启用的信源条目（保持清单顺序）。"""
    wanted = set(only or [])
    specs = []
    for spec in (cfg or {}).get("sources") or []:
        if not spec or not spec.get("type"):
            continue
        if spec.get("enabled", True) is False:
            continue
        sid = spec.get("id") or spec.get("type")
        if wanted and sid not in wanted:
            continue
        specs.append(spec)
    return specs


def renderable(item):
    """这条命中在「不补正文」的情况下能渲染出内容吗。"""
    message = build_instant_message(item, kind=item.extra.get("kind", KIND_WELFARE))
    return content_line_count(message) >= 1


def inspect_source(cfg, wf, state, spec, proxies=None, log=print):
    """单源漏斗：抓取 → 去重 → 分类 → 可渲染。"""
    sid = spec.get("id") or spec.get("type")
    sources = load_sources(cfg, only=[sid], log=lambda *_: None)
    if not sources:
        log(f"[report:{sid}] 无法构建信源（配置问题）")
        return None

    lines = []
    raw = collect(sources, proxies=proxies, log_fn=lines.append)
    for line in lines:
        if "failed" in line:
            log(f"[report:{sid}] {line}")

    fresh = dedupe(raw, state)
    stats = {}
    classified = classify_items(fresh, wf, stats)
    welfare = [i for i in classified if i.extra["kind"] == KIND_WELFARE]
    opportunity = [i for i in classified if i.extra["kind"] == KIND_OPPORTUNITY]
    ready = [i for i in classified if renderable(i)]
    stale = len(raw) - len(fresh)

    log(
        f"[report:{sid}] 抓取 {len(raw)} / 已推过 {stale} / 去重后 {len(fresh)} / "
        f"命中 {len(classified)}（福利 {len(welfare)} · 限时 {len(opportunity)}）/ "
        f"可渲染 {len(ready)}"
    )
    if stats:
        log(f"[report:{sid}]   未命中原因: {format_stats(stats)}")
    for item in classified[:SAMPLE_TITLES]:
        kind = "福利" if item.extra["kind"] == KIND_WELFARE else "限时"
        log(
            f"[report:{sid}]   命中[{kind}/{item.extra.get('match', '')}] "
            f"{item.title[:60]} | 正文 {len(item.summary or '')} 字"
        )
    return {
        "id": sid,
        "fetched": len(raw),
        "stale": stale,
        "fresh": len(fresh),
        "hits": len(classified),
        "welfare": len(welfare),
        "opportunity": len(opportunity),
        "renderable": len(ready),
        "stats": stats,
        "items": classified,
    }


def funnel_report(cfg, wf, state, only=None, proxies=None, log=print):
    """打印逐源漏斗与全局汇总。返回进程退出码。"""
    specs = enabled_specs(cfg, only=only)
    if not specs:
        log("没有匹配的启用信源")
        return 1

    rows = []
    for spec in specs:
        row = inspect_source(cfg, wf, state, spec, proxies=proxies, log=log)
        if row:
            rows.append(row)

    total = {key: 0 for key in ("fetched", "stale", "fresh", "hits", "welfare", "opportunity", "renderable")}
    reasons = {}
    for row in rows:
        for key in total:
            total[key] += row[key]
        for key, value in row["stats"].items():
            add_stat(reasons, key, value)

    log("")
    log("== 漏斗汇总 ==")
    log(
        f"抓取 {total['fetched']} → 已推过 {total['stale']} → 去重后 {total['fresh']} → "
        f"命中 {total['hits']}（福利 {total['welfare']} · 限时 {total['opportunity']}）→ "
        f"可渲染 {total['renderable']}"
    )
    if reasons:
        log(f"未命中原因合计: {format_stats(reasons)}")
    log("命中明细（若已全部推过则为空，说明内容没丢，是信源没有新增）：")
    for row in rows:
        for item in row["items"]:
            kind = "福利" if item.extra["kind"] == KIND_WELFARE else "限时"
            log(
                f"  [{kind}/{item.extra.get('match', '')}] {item.title[:70]} "
                f"| {item.source or row['id']} | {item.url[:80]}"
            )
    if not any(row["items"] for row in rows):
        log("  （无）")
    return 0
