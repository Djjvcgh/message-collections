"""真实语料回归：噪音必须全部不推，真福利必须全部推。

两个语料文件是「口味」的单一事实来源，都来自真实信源与线上推送记录：
- ``tests/fixtures/noise_corpus.json``：必须判为不推（含线上 3 条真实噪音推送）
- ``tests/fixtures/offer_corpus.json``：必须命中，且 kind 与 expect 一致

以后调词表、改正则，只要往 JSON 里加一条就能把这次的教训固定下来，
不需要每次重新翻日志。
"""
import json
from pathlib import Path

import pytest
import yaml

from pusher.filter import KIND_OPPORTUNITY, KIND_WELFARE, WelfareFilter
from pusher.facts import is_actionable
from pusher.sources.base import as_item

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent
CONFIG = ROOT / "config" / "keywords.yml"
SOURCES = ROOT / "config" / "sources.yml"


def build_filter():
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return WelfareFilter(
        cfg["welfare_keywords"],
        cfg["opportunity_keywords"],
        cfg["exclude_words"],
        weak_welfare_keywords=cfg["weak_welfare_keywords"],
        weak_opportunity_keywords=cfg["weak_opportunity_keywords"],
    )


def gated_sources():
    """config/sources.yml 里打开 require_actionable 的信源 id。"""
    cfg = yaml.safe_load(SOURCES.read_text(encoding="utf-8"))
    return {
        spec["id"]
        for spec in cfg["sources"]
        if (spec.get("options") or {}).get("require_actionable")
    }


def pipeline_verdict(raw, wf, gated):
    """复刻 run.classify_items 的判定：分类命中 且（未开源级门槛 或 确实可推）。

    返回 (kind, reason)——kind 为 None 表示这一条最终不会被推送。
    """
    item = as_item(raw)
    kind, reason = wf.explain(item)
    if not kind:
        return None, reason
    if raw.get("source_id") in gated and not is_actionable(item):
        return None, "source-not-actionable"
    return kind, reason


def load_corpus(name):
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return [(item.pop("why", ""), item) for item in data["items"]]


NOISE = load_corpus("noise_corpus.json")
OFFERS = load_corpus("offer_corpus.json")


@pytest.mark.parametrize("why,raw", NOISE, ids=[w[:28] for w, _ in NOISE])
def test_noise_corpus_is_never_pushed(why, raw):
    """噪音必须走完整条流水线后仍然不推（分类命中 + 源级门槛）。"""
    wf = build_filter()
    kind, reason = pipeline_verdict(raw, wf, gated_sources())
    assert kind is None, f"噪音被推送：{why}（命中 {reason}）"


@pytest.mark.parametrize(
    "why,raw", OFFERS, ids=[(r.get("expect", "") + "-" + w)[:28] for w, r in OFFERS]
)
def test_offer_corpus_is_always_pushed(why, raw):
    wf = build_filter()
    item = as_item(raw)
    expect = raw.get("expect", KIND_WELFARE)
    kind = wf.classify(item)
    assert kind == expect, f"真福利没被正确命中：{why}"
    assert is_actionable(item), f"真福利被判为不可推：{why}"
    assert kind in (KIND_WELFARE, KIND_OPPORTUNITY)


def test_corpora_are_not_empty_and_grow_together():
    """语料是对抗性资产：两类都必须在，且每条都要写清为什么。"""
    assert len(NOISE) >= 10
    assert len(OFFERS) >= 8
    assert all(why for why, _ in NOISE)
    assert all(why for why, _ in OFFERS)
