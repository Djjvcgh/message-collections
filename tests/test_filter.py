import json
from pathlib import Path

import yaml

from pusher.fetch_radar import normalize_all
from pusher.filter import (
    KIND_OPPORTUNITY,
    KIND_WELFARE,
    WelfareFilter,
    dedupe,
    head_fields,
    text_blob,
)
from pusher.sources.base import as_item
from pusher.state import State

FIXTURE = Path(__file__).parent / "fixtures" / "radar-sample.json"
CONFIG = Path(__file__).parent.parent / "config" / "keywords.yml"

WELFARE = ["免费", "白嫖", "赠送", "送token", "学生", "优惠", "discount", "free"]
OPPORTUNITY = ["限量", "名额", "开放注册", "截止"]
EXCLUDE = ["广告", "广告位", "推广", "返利"]


def load_items():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [as_item(raw) for raw in normalize_all(data)]


def make_filter():
    return WelfareFilter(WELFARE, OPPORTUNITY, EXCLUDE)


def make_item(title, title_en="", signals=None, summary=""):
    return as_item(
        {"title": title, "title_en": title_en, "signals": signals or [], "summary": summary}
    )


def test_text_blob_strips_spaces_and_lowercases():
    item = make_item("ZCode 送 token")
    assert "送token" in text_blob(head_fields(item))


def test_welfare_matches_chinese_and_english():
    wf = make_filter()
    urls = {i.url for i in wf.hits(load_items())}
    assert "https://example.com/zcode-token" in urls
    assert "https://example.com/gemini-student" in urls


def test_exclude_words_win():
    wf = make_filter()
    urls = {i.url for i in wf.hits(load_items())}
    # a3 含“广告”，即使带“福利”也不推
    assert "https://example.com/ads" not in urls


def test_non_welfare_not_matched():
    wf = make_filter()
    urls = {i.url for i in wf.hits(load_items())}
    assert "https://example.com/ds-v41" not in urls
    assert "https://example.com/ai-safety" not in urls


def test_dedupe_by_url():
    hits = make_filter().hits(load_items())
    st = State()
    st.mark_pushed("https://example.com/zcode-token")
    urls = {i.url for i in dedupe(hits, st)}
    assert "https://example.com/zcode-token" not in urls
    assert "https://example.com/gemini-student" in urls


def test_ascii_word_boundary():
    wf = WelfareFilter(["trial", "free"])
    assert not wf.match(make_item("Industrial AI 传感器上新"))
    assert not wf.match(make_item("freedom of speech 讨论"))
    assert wf.match(make_item("Free trial 开放申请"))


def test_english_plural_tolerated():
    wf = WelfareFilter(["student", "credit"])
    assert wf.match(make_item("", "Google offers students free credits"))
    assert wf.match(make_item("", "student discount program"))


def test_summary_matched_only_when_short():
    """短摘要可兜底；整篇正文长度（>120 字）不参与匹配。"""
    wf = WelfareFilter(["免费"], ["名额"])
    body = "本文讨论 AI 安全。" * 60 + "文末提到可免费领取"
    assert not wf.match(make_item("AI 安全讨论", summary=body))
    assert wf.match(make_item("某活动上线", summary="前 100 名免费领取"))
    # 标题命中时摘要多长都照推
    assert wf.match(make_item("免费额度上线", summary=body))


def test_match_summary_can_be_disabled_per_item():
    """资讯聚合类信源的摘要是整篇文章，可按条目关闭摘要匹配。"""
    wf = WelfareFilter(["免费"])
    noisy = as_item(
        {
            "title": "OpenAI 发布新 agent 平台",
            "summary": "文中提到该平台免费开放给企业",
            "match_summary": False,
        }
    )
    assert not wf.match(noisy)
    # 同一段摘要在允许匹配时（RSS 社区源默认行为）仍可兜底
    loose = as_item(
        {
            "title": "OpenAI 发布新 agent 平台",
            "summary": "文中提到该平台免费开放给企业",
        }
    )
    assert wf.match(loose)


def test_benign_phrases_do_not_count_as_welfare():
    """「免费公开课」「免费开放代码」是描述别人的行为，不是读者能领的福利。"""
    wf = WelfareFilter(["免费"])
    assert not wf.match(make_item("我开了一门免费公开课"))
    assert not wf.match(make_item("Meta 把 Muse 免费开放"))
    assert not wf.match(make_item("某项目开源免费了"))
    # 真福利不受影响
    assert wf.match(make_item("免费领取 1 年域名"))


def test_long_summary_excluded_from_matching():
    wf = WelfareFilter(["免费"])
    noisy = make_item("AI 安全讨论", summary="免费" * 200)
    assert not wf.match(noisy)


def test_opportunity_keywords_classify_separately():
    wf = WelfareFilter(["免费"], ["开放注册", "限量", "截止"])
    assert wf.classify(make_item("某站点开放注册，无需邀请码")) == KIND_OPPORTUNITY
    assert wf.classify(make_item("限量 100 份，先到先得")) == KIND_OPPORTUNITY
    assert wf.classify(make_item("免费领取一年域名")) == KIND_WELFARE
    # 同时命中两类时按福利处理（信息量更大）
    assert wf.classify(make_item("免费额度限量发放，截止 9/30")) == KIND_WELFARE
    assert wf.classify(make_item("OpenAI 发布新模型")) is None


def test_exclude_beats_opportunity():
    wf = WelfareFilter(["免费"], ["名额"], EXCLUDE)
    assert wf.classify(make_item("名额有限，推广返利活动")) is None


def test_keywords_config_is_usable():
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    welfare = {k.strip().lower() for k in cfg["welfare_keywords"]}
    opportunity = {k.strip().lower() for k in cfg["opportunity_keywords"]}
    # 调优结论：deal 在独立成词时会命中大量收购新闻，禁止进入词表
    assert "deal" not in welfare
    # coupon 同理：2026-10-03 在 Telegram 免费课频道连中 20 条正文
    assert "coupon" not in welfare
    assert len(cfg["opportunity_keywords"]) >= 10
    assert "开放注册" in opportunity
    assert "免费域名" in {k.strip().lower() for k in cfg["welfare_keywords"]}
    assert cfg["exclude_words"]
    # 噪音防护：抽奖/签到类刷屏词必须在排除表里
    exclude = {w.strip().lower() for w in cfg["exclude_words"]}
    assert {"抽奖", "每日签到"} <= exclude


def test_dedupe_collapses_same_title_from_two_sources():
    a = make_item("NodeSeek：某某免费域名活动开始")
    b = make_item("某某免费域名活动开始")
    b.url = "https://other.example.com/x"
    kept = dedupe([a, b], State())
    assert len(kept) == 1
    assert kept[0].url == a.url
