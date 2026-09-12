import json
from pathlib import Path

from pusher.digest import build_groups, classify, render_digest
from pusher.fetch_radar import normalize, normalize_all
from pusher.filter import WelfareFilter

FIXTURE = Path(__file__).parent / "fixtures" / "radar-sample.json"

KEYWORDS = ["免费", "白嫖", "赠送", "送token", "学生", "优惠", "discount", "free"]


def make_groups(limits=None):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    items = normalize_all(data)
    wf = WelfareFilter(KEYWORDS, ["广告", "广告位", "推广"])
    return build_groups(items, wf, limits or {}), wf, items


def test_classify_routes_by_label():
    _, wf, items = make_groups()
    by_url = {i["url"]: i for i in items}
    assert classify(by_url["https://example.com/zcode-token"], wf) == "welfare"
    assert classify(by_url["https://example.com/gemini-student"], wf) == "welfare"
    assert classify(by_url["https://example.com/ds-v41"], wf) == "model_release"
    assert classify(by_url["https://example.com/cursor-projects"], wf) == "product"
    assert classify(by_url["https://example.com/ai-safety"], wf) == "notable"


def test_build_groups_sorted_and_limited():
    # 造 3 条 model_release 验证限额与排序
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    base = data["items_ai"][3]  # DeepSeek V4.1
    extra = []
    for n in range(2):
        clone = dict(base)
        clone["url"] = f"https://example.com/mr-{n}"
        clone["title_zh"] = f"模型发布 {n}"
        clone["source_tier_rank"] = 1  # tier 更低，排序应在官方之后
        extra.append(clone)
    items = normalize_all(data) + [normalize(e) for e in extra]
    wf = WelfareFilter(KEYWORDS)
    groups = build_groups(items, wf, {"model_release": 2})

    mr = groups["model_release"]
    assert len(mr) == 2
    assert mr[0]["url"] == "https://example.com/ds-v41"  # 官方一手源在前


def test_render_digest_contains_groups_and_links():
    groups, _, _ = make_groups()
    text = render_digest("09-12", groups)
    assert "AI 日报 · 09-12" in text
    assert "🎁 福利速递" in text
    assert "🚀 模型发布" in text
    assert "🧰 产品与工具" in text
    assert "💬 值得注意" in text
    assert "https://example.com/zcode-token" in text
    assert len(text) <= 3900 + 20  # 截断保护 + 省略行余量


def test_render_digest_empty():
    assert render_digest("09-12", {}) == "🤖 AI 日报 · 09-12"


def test_render_digest_truncation_never_cuts_tags():
    from pusher.digest import MAX_CHARS

    def big_item(n):
        return {
            "title": f"很长的模型发布标题第{n}条" + "补充细节" * 30,
            "title_en": "",
            "source": "Source",
            "tier": 1,
            "tier_label": "AI垂直源",
            "score": 0.9,
            "label": "model_release",
            "url": f"https://example.com/very/long/url/segment-{n}/more/segments",
            "signals": [],
            "reason": "",
        }

    groups = {"model_release": [big_item(n) for n in range(30)]}
    text = render_digest("09-12", groups)
    assert len(text) <= MAX_CHARS + 20
    assert text.count("<a ") == text.count("</a>")  # 标签必须成对完整
    assert text.count("<b>") == text.count("</b>")
    assert text.endswith("…（内容过长已截断）")
