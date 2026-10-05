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
    weak_welfare = {k.strip().lower() for k in cfg["weak_welfare_keywords"]}
    weak_opportunity = {k.strip().lower() for k in cfg["weak_opportunity_keywords"]}
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
    # 2026-10-05：宽词必须落在 weak_*（硬词里出现「学生/优惠」会被行业资讯蹭中）
    assert {"学生", "优惠", "折扣", "福利", "免费使用"} <= weak_welfare
    assert not ({"学生", "优惠", "折扣", "福利", "免费使用"} & welfare)
    # 同一个词不能既当硬词又当宽词，否则共现门槛形同虚设
    assert not (weak_welfare & welfare)
    assert not (weak_opportunity & opportunity)
    assert {"限量", "名额", "截止", "限时"} <= weak_opportunity


def build_config_filter():
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return WelfareFilter(
        cfg["welfare_keywords"],
        cfg["opportunity_keywords"],
        cfg["exclude_words"],
        weak_welfare_keywords=cfg["weak_welfare_keywords"],
        weak_opportunity_keywords=cfg["weak_opportunity_keywords"],
    )


def test_weak_keywords_need_hard_offer_evidence():
    """宽词单独出现不是福利：实测「我让AI教学生写前端」就是被 学生 捞进来的。"""
    wf = build_config_filter()
    assert wf.classify(make_item("我让AI教学生写前端，三天后课堂变了——Web教育者的集体反思")) is None
    assert (
        wf.classify(
            make_item(
                "MIT报告发现，AI正在侵蚀答疑时间、学习小组以及师生之间的信任",
                summary="MIT专家委员会报告警告，教授甚至考虑用AI agents替代学生做research assistants。",
            )
        )
        is None
    )
    # 真福利：宽词 + 硬证据 → 推
    assert wf.classify(make_item("学生认证可免费领取 1 年会员")) == KIND_WELFARE
    assert wf.classify(make_item("教育优惠 5 折，折扣码：EDU50")) == KIND_WELFARE


def test_weak_keywords_do_not_match_english_title():
    """实测：radar 的 title_en「…the trust between faculty and students」命中了 student。"""
    wf = WelfareFilter([], [], [], weak_welfare_keywords=["学生", "student", "discount"])
    noisy = make_item(
        "AI 正在侵蚀师生信任",
        title_en="AI is eroding office hours and the trust between faculty and students",
        summary="报告称师生关系受影响。",
    )
    assert wf.classify(noisy) is None
    # 中文标题里同样宽词 + 硬证据时仍然命中
    assert wf.classify(make_item("学生优惠", summary="折扣码：EDU50，半价")) == KIND_WELFARE


def test_negated_offer_is_not_welfare():
    """实测噪音：福利被取消的资讯（Gemini 将结束免费使用）不算福利。"""
    wf = build_config_filter()
    assert wf.classify(make_item("Gemini 将结束 Flash 和 Pro 模型的免费使用")) is None
    assert wf.classify(make_item("XX 云不再免费，下月起开始收费")) is None
    assert wf.classify(make_item("某服务免费额度即将结束")) is None
    assert wf.classify(make_item("套餐恢复原价，优惠码作废")) is None
    # 反例保护：这是截止预告，不是福利消失
    assert wf.classify(make_item("限时免费领取 100 元额度，月底结束")) == KIND_WELFARE


def test_secondhand_sale_is_not_actionable():
    """实测噪音：V2EX「优惠价出一个香港 CSL esim」是二手转卖，不是福利。"""
    from pusher.facts import is_actionable

    assert not is_actionable(
        make_item(
            "优惠价出一个香港 CSL esim",
            summary="香港 CSL esim, 15GB 中澳台漫游，14 元/年保号，原价 130，现价 120 联系绿泡泡 abc",
        )
    )
    # 真福利里的「送出」不能被误判成转卖
    assert is_actionable(make_item("官方免费送出 100 个兑换码", summary="先到先得"))
    assert is_actionable(make_item("内网云国庆活动余额兑换券", summary="兑换券码：NWY-CIYUM-4UYZD-40694"))


def test_derived_opportunity_needs_offer_and_window():
    """有硬信号 + 有窗口 = 错过就没，即使没命中任何限时词。"""
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    wf = WelfareFilter(cfg["welfare_keywords"], cfg["opportunity_keywords"])
    item = make_item("某活动 5 折优惠", summary="折扣码：HALF，限量 100 份，先到先得")
    assert wf.classify(item) is not None
    # 只有窗口词、拿不到任何东西 → 不推
    assert wf.classify(make_item("某大会报名截止 10月15日", summary="议程已公布")) is None
    # 硬限时词本身就是「可报名/可注册」的动作，不需要窗口兜底
    assert wf.classify(make_item("某站开放注册")) == KIND_OPPORTUNITY


def test_bare_generic_words_are_not_in_keywords():
    """裸词「免费 / 试用 / credits」是误报主因，词表里不允许出现。"""
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    welfare = {k.strip().lower() for k in cfg["welfare_keywords"]}
    assert "免费" not in welfare          # 实测 8 条含「免费」的里 7 条是误报
    assert "试用" not in welfare          # 「我试用了 Gemini…」是评测文
    assert "credits" not in welfare       # 「每月含 400 万 credits」是订阅介绍
    assert {"免费领取", "免费试用", "免费发放", "免费域名"} <= welfare

    wf = WelfareFilter(cfg["welfare_keywords"], [])
    assert not wf.match(make_item("我试用了 Gemini 免费的新电影制作工具；好莱坞没什么好担心的"))
    assert not wf.match(make_item("Meta 开源代码，让你打造 Muse AI 小硬件"))
    assert not wf.match(make_item("别再猜哪个 AI 最强了：不到 100 美元就能用上 GPT 和 Claude"))
    # 真能领的仍要命中
    assert wf.match(make_item("新用户注册即可免费领取 100 元额度"))
    assert wf.match(make_item("某站发放免费额度，先到先得"))


def test_dedupe_collapses_same_title_from_two_sources():
    a = make_item("NodeSeek：某某免费域名活动开始")
    b = make_item("某某免费域名活动开始")
    b.url = "https://other.example.com/x"
    kept = dedupe([a, b], State())
    assert len(kept) == 1
    assert kept[0].url == a.url
