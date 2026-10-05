"""消息渲染的用例：标题清洗、摘要重排、关键数字高亮、速览行、长度预算。"""
from pusher.facts import extract_facts, is_actionable
from pusher.notify_telegram import (
    DEFAULT_BUDGET,
    build_instant_message,
    build_summary,
    build_title,
    content_line_count,
    drop_repeated,
    format_published,
    highlight,
    normalize_title,
    score_sentence,
    split_sentences,
)
from pusher.sources.base import as_item


def make(**kw):
    base = {"title": "", "url": "https://x.com/1", "source": "NodeSeek", "summary": ""}
    base.update(kw)
    return as_item(base)


# ---------------------------------------------------------------- 标题

def test_title_joins_wrapped_lines_and_strips_markup():
    item = make(title="某云赠送\n3 个月    免费服务器<br/>先到先得")
    title = build_title(item)
    # 换行与多余空白被合并，HTML 标签被剥掉，中文之间的空格收紧
    assert "\n" not in title
    assert "<br/>" not in title
    assert "某云赠送 3 个月免费服务器先到先得" == title


def test_title_join_keeps_space_for_latin_words():
    # 英文之间必须保留空格，否则单词会粘在一起
    assert build_title(make(title="Cloudflare\nfree tier raised")) == "Cloudflare free tier raised"


def test_title_drops_forwarding_tone():
    assert build_title(make(title="我试用了 Gemini 免费的新工具")) == "Gemini 免费的新工具"
    assert build_title(make(title="别错过这个 75 美元的优惠")) == "75 美元的优惠"
    assert build_title(make(title="重磅！某活动开启")) == "某活动开启"


def test_short_title_is_untouched():
    title = "某活动发放 75 美元优惠，名额 100 个"
    assert build_title(make(title=title)) == title


def test_long_tweet_title_is_rebuilt_from_summary():
    """实测场景：X 推文标题 200+ 字且被上游截断，改用摘要重做标题。"""
    item = make(
        title="Anthropic 的工程师说：我们内部已经不怎么写 prompt 了，写的是循环。 她在台上花了半小时，"
              "展示了 Claude 团队如何创建能够自我提示的循环。 如果这堂课卖 400 刀，今年 Agent 课的榜单"
              "大概就是它。但它是免费的。 真正拉开代际差距的，不是谁的 Prompt 写得…",
        summary="Anthropic 工程师用半小时展示 Claude 团队如何创建自我提示的循环，称内部已不写 prompt；"
                "这堂价值 400 刀的 Agent 课免费，讲记忆从 CLAUDE.md 变成 Agent 自己读写的 memory/。",
    )
    title = build_title(item)
    assert len(title) <= 110
    assert title.startswith("Anthropic 工程师用半小时展示")
    assert "…" not in title          # 收口落在句末标点上，不留悬空省略号


def test_long_title_without_summary_trims_at_sentence_end():
    item = make(title="第一句话讲完了。第二句话非常长" + "继续铺垫" * 40, summary="")
    title = build_title(item)
    assert title.endswith("。")
    assert len(title) <= 140


def test_title_falls_back_to_english_title():
    assert build_title(make(title="", title_en="Free tier raised")) == "Free tier raised"


# ---------------------------------------------------------------- 摘要

def test_split_sentences_keeps_punctuation():
    assert split_sentences("第一句。第二句！第三句？") == ["第一句。", "第二句！", "第三句？"]


def test_split_sentences_breaks_long_run_on_commas():
    text = "没有句号的一段话，" * 20
    parts = split_sentences(text)
    assert len(parts) > 1
    assert all(len(p) <= 140 for p in parts)


def test_summary_puts_price_sentence_first():
    """倒金字塔：含价格的句子优先，但最终仍按原文顺序输出保证连贯。"""
    reason = "某活动开始了。参与方式很简单。价格只要 $75，限前 100 名。"
    out = build_summary(reason, 200)
    assert "$75" in out
    # 原文顺序保留：价格句仍在最后
    assert out.index("某活动开始了") < out.index("$75")


def test_summary_drops_low_value_sentences_when_budget_is_tight():
    reason = "开场白一句。真正的福利只要 $75。补一句没用的。" * 3
    out = build_summary(reason, 40)
    assert "$75" in out
    assert len(out) <= 40


def test_summary_keeps_context_when_budget_allows():
    """预算够时要保留起因、背景与官方说法，让读者不必点原文。"""
    text = (
        "最近云服务市场又有新动作。想知道这家新秀值不值得上手，读这篇就够了。"
        "X 云宣布新用户注册即可免费领取 100 元额度，名额限前 500 名，活动截止 10月15日。"
        "之所以推出这轮补贴，是因为年底前要冲开发者规模。"
        "此前该平台在 2024 年也做过类似活动，当时额度只有 50 元。"
        "官方表示这次会分批放量。"
    )
    out = build_summary(text, 600)
    assert "100 元额度" in out          # 事实
    assert "之所以推出这轮补贴" in out    # 起因
    assert "2024 年" in out              # 历史背景
    assert "官方表示" in out              # 官方说法
    assert "想知道" not in out            # 评论腔剔除


def test_score_rewards_cause_and_background():
    cause = score_sentence("之所以推出这轮补贴，是因为年底要冲规模。", 3)
    flat = score_sentence("参与方式很简单。", 3)
    assert cause > flat


def test_summary_never_cuts_a_sentence_in_half():
    reason = "甲" * 30 + "。" + "乙" * 30 + "。"
    out = build_summary(reason, 40)
    assert out in ("甲" * 30 + "。", "乙" * 30 + "。") or out == "甲" * 30 + "。"


def test_summary_empty_when_budget_too_small():
    assert build_summary("有内容。", 0) == ""


def test_drop_repeated_removes_sentence_already_in_headline():
    headline = "某云赠送 3 个月免费服务器"
    rest = drop_repeated("某云赠送 3 个月免费服务器。另有 5 折优惠码。", headline)
    assert rest == "另有 5 折优惠码。"


def test_score_prefers_hard_information():
    hard = score_sentence("价格只要 $75，限前 100 名。", 2)
    soft = score_sentence("参与方式很简单。", 2)
    assert hard > soft


# ---------------------------------------------------------------- 高亮与速览

def test_highlight_bolds_prices_and_dates():
    out = highlight("只要 $75，10月2日 截止")
    assert "<b>$75</b>" in out
    assert "<b>10月2日</b>" in out


def test_highlight_escapes_before_adding_tags():
    out = highlight("A & B 只要 $5")
    assert "&amp;" in out
    assert "<b>$5</b>" in out


def test_extract_facts_finds_price_and_deadline():
    facts = dict(extract_facts("某活动 Expo+ Pass 只要 $75，原文提醒 10月2日 是最后一天。"))
    assert facts["价格"] == "$75"
    assert facts["截止"]


def test_extract_facts_prefers_final_price():
    facts = dict(extract_facts("原价 ￥74.00，折扣码：LIFETIMEO，最终到手价 ￥59.20"))
    assert "59.20" in facts["价格"]
    assert "到手价" in facts["价格"]
    assert facts["折扣码"] == "LIFETIMEO"


def test_extract_facts_requires_label_for_codes():
    """裸匹配会把型号/日期/单号当成折扣码，所以只认带标签的。"""
    facts = dict(extract_facts("出 H11SSL-NC 主板，单号 1086110586937，日期 20270930"))
    assert "折扣码" not in facts


def test_extract_facts_prefers_labeled_price_without_unit():
    """实测：正文「原价 130，现价 120」的 120 没有单位，旧逻辑取到了「14 元/年保号」。"""
    facts = dict(
        extract_facts("香港 CSL esim, 15GB 中澳台漫游，14 元/年保号，原价 130，现价 120 联系绿泡泡 abc")
    )
    assert facts["价格"] == "120（到手价）"


def test_extract_facts_skips_per_unit_price():
    """「14 元/年」是套餐单价，不是售价。"""
    assert "价格" not in dict(extract_facts("套餐 14 元/年保号，续费另算"))


def test_negated_offer_yields_no_evidence():
    """否定语境先行屏蔽：福利消失的资讯不能产生任何硬证据。"""
    from pusher.facts import has_offer_signal, has_window_signal

    assert not has_offer_signal(make(title="Gemini 将结束 Flash 和 Pro 模型的免费使用"))
    assert not has_offer_signal(make(title="某服务不再免费", summary="下月起开始收费"))
    assert has_offer_signal(make(title="免费领取 100 元额度"))
    assert has_window_signal(make(title="限量 100 份，先到先得"))
    assert has_window_signal(make(title="5 折优惠", summary="活动截止 10月15日"))


def test_extract_facts_returns_empty_when_absent():
    assert extract_facts("普通标题 普通摘要，没有价格也没有期限。") == []


def test_actionable_filter_drops_secondhand_and_requests():
    assert not is_actionable(make(title="【出】出懒猫云LazyCat优惠码及机器", summary="26元 折扣"))
    assert not is_actionable(make(title="求推荐香港 CN2 原生机房 VPS", summary="有没有优惠的"))
    assert not is_actionable(make(title="收 berohost", summary=""))
    assert is_actionable(
        make(title="内网云国庆活动余额兑换券", summary="兑换券码：NWY-CIYUM-4UYZD-40694 先到先得")
    )
    assert is_actionable(make(title="AdGuard 终身订阅", summary="折扣码：LIFETIMEO 最终到手价 ￥59.20"))


def test_format_published_today_and_recent():
    from datetime import datetime, timedelta, timezone

    from pusher.notify_telegram import BJT

    now = datetime.now(BJT)
    assert format_published(make(extra={"published": now.isoformat()})).startswith("今天")
    three_days = now - timedelta(days=3)
    assert format_published(make(extra={"published": three_days.isoformat()}))[:5].count("-") == 1
    old = now - timedelta(days=30)
    assert format_published(make(extra={"published": old.isoformat()})) == ""
    assert format_published(make(extra={"published": "坏数据"})) == ""


# ---------------------------------------------------------------- 组装

def test_message_layout_and_budget():
    item = make(
        title="某云赠送 3 个月免费服务器",
        summary="注册即可领取。原价 30 美元，现在免费。名额限前 100 名，截止 10月2日。",
        source="NodeSeek",
        tier_label="热议参考",
        extra={"published": "2026-09-01T00:00:00+00:00"},
    )
    msg = build_instant_message(item, kind="welfare")
    assert msg.startswith("🎁 [福利] <b>某云赠送 3 个月免费服务器</b>")
    assert "<blockquote>" in msg
    assert 'via <a href="https://x.com/1">NodeSeek</a> · 热议参考' in msg
    visible = msg.replace("<b>", "").replace("</b>", "").replace("<blockquote>", "").replace("</blockquote>", "")
    assert len(visible) <= DEFAULT_BUDGET + 30


def test_message_omits_summary_when_it_repeats_title():
    item = make(title="Minisforum 工作站限时预购价 7399 美元", summary="Minisforum 工作站限时预购价 7399 美元。")
    msg = build_instant_message(item, kind="opportunity")
    assert "⏳ [限时]" in msg
    assert "<blockquote>" not in msg


def test_message_without_summary_still_renders():
    item = make(title="免费域名活动", summary="")
    msg = build_instant_message(item)
    assert "<blockquote>" not in msg
    # 只有标题行与速览行两行，中间不留空段
    assert msg.count("\n") == 1


def test_short_summary_is_not_padded():
    """内容本来就短，不硬凑到 350 字。"""
    item = make(title="折扣券", summary="三折券 40r。")
    msg = build_instant_message(item)
    assert len(msg) < 120


# ---------------------------------------------------------------- 正文保障（2026-10-05）

def test_facts_only_gets_brief_line_when_details_are_thin():
    """实测：V2EX「优惠价出一个香港 CSL esim」只抽到一行价格，整条消息没有内容。"""
    item = make(
        title="优惠价出一个香港 CSL esim",
        source="V2EX 优惠信息",
        layout="facts_only",
        summary=(
            "香港 CSL esim, 15GB 中澳台漫游，香港 25GB，14 元/年保号，"
            "原价 130，现价 120 联系绿泡泡 abc"
        ),
    )
    msg = build_instant_message(item)
    assert "▎简介：" in msg
    assert "香港 CSL esim" in msg
    assert content_line_count(msg) >= 1


def test_facts_only_skips_brief_when_details_are_enough():
    """细节行够多时不再补简介，保持「只给要点」的体裁。"""
    item = make(
        title="AdGuard 终身订阅优惠",
        layout="facts_only",
        summary="折扣码：LIFETIMEO 最终到手价 ￥59.20，截止 10月15日，库存有限",
    )
    msg = build_instant_message(item)
    assert "<blockquote>" not in msg
    assert "▎简介：" not in msg


def test_content_line_count_counts_only_body_lines():
    assert content_line_count("🎁 [福利] <b>标题</b>\nvia 来源") == 0
    assert content_line_count("🎁 [福利] <b>标题</b>\n▎价格：<b>14 元</b>\nvia 来源") == 1
    assert (
        content_line_count(
            "🎁 [福利] <b>标题</b>\n▎价格：1 元\n<blockquote>摘要</blockquote>\nvia 来源"
        )
        == 2
    )


def test_short_label_filters_composite_noise():
    """实测尾部是「via Hacker News · 24h最热 · 热议参考」，两个标签挤在一起是噪声。"""
    from pusher.notify_telegram import short_label

    assert short_label("热议参考") == "热议参考"
    assert short_label("24h最热 · 热议参考") == "24h最热"
    assert short_label("这是一个特别特别长的标签") == ""
    assert short_label("") == ""


def test_long_composite_label_is_dropped_from_footer():
    item = make(title="某云赠送 3 个月免费服务器", summary="注册即可领取。", tier_label="24h最热 · 热议参考")
    msg = build_instant_message(item)
    assert "24h最热" in msg
    assert "热议参考" not in msg
