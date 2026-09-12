"""日报组装：按组归类、排序、限额、渲染为 HTML 文本。"""
from .notify_telegram import esc, link

GROUP_LABELS = [
    ("welfare", "🎁 福利速递"),
    ("model_release", "🚀 模型发布"),
    ("product", "🧰 产品与工具"),
    ("notable", "💬 值得注意"),
]

PRODUCT_LABELS = ("ai_product_update", "developer_tool", "agent_workflow")
MAX_CHARS = 3900  # Telegram 单条消息上限 4096，留出余量


def classify(item, welfare_filter):
    if welfare_filter.match(item):
        return "welfare"
    if item["label"] == "model_release":
        return "model_release"
    if item["label"] in PRODUCT_LABELS:
        return "product"
    return "notable"


def build_groups(items, welfare_filter, limits):
    groups = {key: [] for key, _ in GROUP_LABELS}
    for item in items:
        groups[classify(item, welfare_filter)].append(item)
    for key in groups:
        groups[key].sort(key=lambda i: (i["tier"], -i["score"]))
        groups[key] = groups[key][: limits.get(key, 5)]
    return {key: kept for key, kept in groups.items() if kept}


def render_item(item):
    src = item["source"] + (f" · {item['tier_label']}" if item["tier_label"] else "")
    return f"· <b>{esc(item['title'])}</b>\n  {esc(src)}\n  {link(item['url'])}"


def render_digest(date_str, groups):
    """按 福利→模型→产品→值得注意 的优先级渲染。

    超过 Telegram 长度上限时按条目整体丢弃（不按字符硬切，
    避免切断 <a> 标签导致 Telegram 400）。
    """
    parts = [f"🤖 AI 日报 · {date_str}"]
    length = len(parts[0])
    truncated = False
    for key, label in GROUP_LABELS:
        items = groups.get(key)
        if not items:
            continue
        header = f"{label} ({len(items)})"
        if length + 1 + len(header) > MAX_CHARS:
            truncated = True
            break
        parts.append(header)
        length += 1 + len(header)
        for item in items:
            rendered = render_item(item)
            if length + 1 + len(rendered) > MAX_CHARS:
                truncated = True
                break
            parts.append(rendered)
            length += 1 + len(rendered)
        if truncated:
            break
    if truncated:
        parts.append("…（内容过长已截断）")
    return "\n".join(parts)
