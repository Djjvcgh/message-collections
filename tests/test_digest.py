import json
from pathlib import Path

from pusher.digest import (
    MAX_CHARS,
    build_groups,
    classify,
    render_group_message,
)
from pusher.fetch_radar import normalize, normalize_all
from pusher.filter import WelfareFilter

FIXTURE = Path(__file__).parent / "fixtures" / "radar-sample.json"

KEYWORDS = ["免费", "白嫖", "赠送", "送token", "学生", "优惠", "discount", "free"]
EXCLUDE = ["广告", "广告位", "推广"]


def make_groups(limits=None):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    items = normalize_all(data)
    wf = WelfareFilter(KEYWORDS, EXCLUDE)
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


def test_render_reason_full_no_truncation():
    from pusher.digest import render_item

    item = {"reason": "很" * 300, "title": "t", "url": "https://a.com",
            "source": "S", "tier_label": "", "signals": []}
    text = render_item(item, 1)
    assert "很" * 300 in text  # 摘要完整展示，无省略号
    assert "…" not in text.replace("很", "")


def test_render_group_message_layout():
    groups, _, _ = make_groups()
    text = render_group_message("2026-09-12", "21:00", "🎁", "福利速递", groups["welfare"])
    # 头部：组名 + 完整日期时间 + 条数
    assert "🎁 <b>福利速递</b> · 2026-09-12 21:00（2条）" in text
    # 标题加粗（非链接），编号在最前
    assert "1. <b>" in text
    # 引用块摘要（fixture 条目 reason 为中文）
    assert "<blockquote>" in text and "</blockquote>" in text
    # via 来源行：URL 内嵌在来源名里
    assert 'via <a href="https://example.com/gemini-student">Google Blog</a>' in text
    # 分段：条目间空行
    assert "\n\n2. " in text
    # 页脚标签
    assert "#AI日报 #福利速递" in text


def test_footer_tags_include_signals():
    groups, _, _ = make_groups()
    text = render_group_message("2026-09-12", "21:00", "🎁", "福利速递", groups["welfare"])
    # a1 信号词 zcode、a2 信号词 gemini，聚合进页脚
    assert "#zcode" in text
    assert "#gemini" in text


def test_render_group_message_no_reason_skips_blockquote():
    item = {
        "title": "纯标题条目",
        "url": "https://example.com/x",
        "source": "S",
        "tier_label": "",
        "reason": "",
        "signals": [],
    }
    text = render_group_message("2026-09-12", "21:00", "💬", "值得注意", [item])
    assert "<blockquote>" not in text
    assert "1. " in text


def test_render_group_message_truncation_never_cuts_tags():
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
            "signals": [f"tag{n}"],
            "reason": "一句话摘要" * 10,
        }

    items = [big_item(n) for n in range(30)]
    text = render_group_message("2026-09-12", "21:00", "🚀", "模型发布", items)
    assert len(text) <= MAX_CHARS + 40
    assert text.count("<a ") == text.count("</a>")
    assert text.count("<blockquote>") == text.count("</blockquote>")
    assert text.count("<b>") == text.count("</b>")
    assert "#AI日报 #模型发布" in text  # 截断后页脚标签仍在
