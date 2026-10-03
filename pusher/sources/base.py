"""条目模型与信源注册表。

所有信源产出统一的 Item，后续过滤 / 去重 / 推送只认这一种结构。
新增一种信源只需在 pusher/sources/ 下实现 fetch(options)，
再在 SOURCE_TYPES 注册，配置里即可用 type: 引用。
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Item:
    title: str = ""
    url: str = ""
    source: str = ""          # 展示用来源名（如 linux.do）
    source_id: str = ""       # 配置里的信源 id（如 linuxdo-free）
    category: str = ""        # radar 的 ai_label，或信源自带分类
    title_en: str = ""
    summary: str = ""         # 原始摘要（可为 HTML 残留）
    tier: int = 9             # 越小越权威；radar 有分层，其余默认 9
    tier_label: str = ""
    score: float = 0.0
    signals: List[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def reason(self) -> str:
        """推送时展示的摘要文案。"""
        return self.summary

    def key(self) -> str:
        """本轮内部去重键（与 state 的哈希键区分开）。"""
        return (self.url or "") + "|" + self.title

    def sort_key(self):
        return (self.tier, -self.score)


def text_or(item, field, default=""):
    """兼容 dict 与 Item 的字段读取，便于老调用点平滑迁移。"""
    if isinstance(item, dict):
        value = item.get(field)
    else:
        value = getattr(item, field, None)
    return default if value is None else value


def as_item(raw):
    """把 dict（含旧 radar normalize 结果）转成 Item。"""
    if isinstance(raw, Item):
        return raw
    return Item(
        title=raw.get("title") or "",
        url=raw.get("url") or "",
        source=raw.get("source") or "",
        source_id=raw.get("source_id") or "",
        category=raw.get("category") or raw.get("label") or "",
        title_en=raw.get("title_en") or "",
        summary=raw.get("summary") or raw.get("reason") or "",
        tier=int(raw.get("tier") or 9),
        tier_label=raw.get("tier_label") or "",
        score=float(raw.get("score") or 0.0),
        signals=list(raw.get("signals") or []),
        extra=dict(raw.get("extra") or {}),
    )


def load_sources(config, only=None, log=print):
    """按配置构建信源实例；单个信源构造失败只跳过它。

    only: 可选的 id 列表，只构建这些信源（用于本地联调单个源）。
    """
    from . import SOURCE_TYPES

    specs = (config or {}).get("sources") or []
    wanted = set(only or [])
    sources = []
    for spec in specs:
        if not spec or not spec.get("type"):
            continue
        sid = spec.get("id") or spec.get("type")
        if spec.get("enabled", True) is False:
            log(f"[source:{sid}] disabled by config")
            continue
        if wanted and sid not in wanted:
            continue
        cls = SOURCE_TYPES.get(spec["type"])
        if cls is None:
            log(f"[source:{sid}] unknown type: {spec['type']}")
            continue
        try:
            sources.append(cls(sid, spec.get("options") or {}))
        except Exception as exc:  # noqa: BLE001 — 配置错误不应拖垮整轮
            log(f"[source:{sid}] init failed: {exc}")
    return sources
