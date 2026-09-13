"""日报组装：每组一条消息（引用块摘要式 + 标签页脚）。"""
import html
import re
from collections import Counter

from .notify_telegram import esc

GROUP_LABELS = [
    ("welfare", "🎁", "福利速递"),
    ("model_release", "🚀", "模型发布"),
    ("product", "🧰", "产品与工具"),
    ("notable", "💬", "值得注意"),
]

PRODUCT_LABELS = ("ai_product_update", "developer_tool", "agent_workflow")
MAX_CHARS = 3900   # Telegram 单条消息上限 4096，留出余量（超长按条目整体丢弃）


def classify(item, welfare_filter):
    if welfare_filter.match(item):
        return "welfare"
    if item["label"] == "model_release":
        return "model_release"
    if item["label"] in PRODUCT_LABELS:
        return "product"
    return "notable"


def build_groups(items, welfare_filter, limits):
    groups = {key: [] for key, _, _ in GROUP_LABELS}
    for item in items:
        groups[classify(item, welfare_filter)].append(item)
    for key in groups:
        groups[key].sort(key=lambda i: (i["tier"], -i["score"]))
        groups[key] = groups[key][: limits.get(key, 5)]
    return {key: kept for key, kept in groups.items() if kept}


def _clean_tag(s):
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", s)


def _footer_tags(items, group_name):
    """页脚标签：#AI日报 + 组名 + 消息内条目信号词 top3，可在 Telegram 内点击筛选。"""
    tags = ["AI日报", group_name]
    counter = Counter()
    for item in items:
        for s in item.get("signals") or []:
            t = _clean_tag(s)
            if t:
                counter[t] += 1
    seen = {t.lower() for t in tags}
    for t, _ in counter.most_common(3):
        if t.lower() not in seen:
            tags.append(t)
            seen.add(t.lower())
    return " ".join("#" + t for t in tags)


def render_via(item):
    """来源行：URL 内嵌在来源名里，点击直达原文（样式参考用户提供的频道截图）。"""
    url = html.escape(item.get("url") or "", quote=True)
    src = item.get("source") or "原文链接"
    tier = f" · {item['tier_label']}" if item.get("tier_label") else ""
    if url:
        return f'via <a href="{url}">{esc(src)}</a>{esc(tier)}'
    return f"via {esc(src)}{esc(tier)}"


def render_item(item, index):
    lines = [f"{index}. <b>{esc(item['title'])}</b>"]
    reason = (item.get("reason") or "").strip()
    if reason:
        lines.append(f"<blockquote>{esc(reason)}</blockquote>")
    lines.append(render_via(item))
    return "\n".join(lines)


def render_group_message(date_str, time_str, emoji, name, items):
    """渲染一个分组消息；超长时按条目整体丢弃，绝不切断标签。"""
    header = f"{emoji} <b>{esc(name)}</b> · {date_str} {time_str}（{len(items)}条）"
    text = header
    length = len(text)
    truncated = False
    shown = 0
    for item in items:
        shown += 1
        block = f"\n\n{render_item(item, shown)}"
        if length + len(block) > MAX_CHARS:
            truncated = True
            break
        text += block
        length += len(block)
    text += "\n\n" + _footer_tags(items, name)
    if truncated:
        text += "\n…（部分内容未展示）"
    return text
