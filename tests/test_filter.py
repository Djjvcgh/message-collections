import json
from pathlib import Path

from pusher.filter import WelfareFilter, drop_pushed, text_blob
from pusher.fetch_radar import normalize_all
from pusher.state import State

FIXTURE = Path(__file__).parent / "fixtures" / "radar-sample.json"

KEYWORDS = ["免费", "白嫖", "赠送", "送token", "学生", "优惠", "discount", "free"]
EXCLUDE = ["广告", "广告位", "推广"]


def load_items():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return normalize_all(data)


def make_filter():
    return WelfareFilter(KEYWORDS, EXCLUDE)


def test_text_blob_strips_spaces_and_lowercases():
    item = {"title": "ZCode 送 token", "title_en": "", "signals": [], "reason": ""}
    assert "送token" in text_blob(item)


def test_welfare_matches_chinese_and_english():
    wf = make_filter()
    items = load_items()
    hits = wf.hits(items)
    urls = {i["url"] for i in hits}
    # a1: 送token；a2: student/discount
    assert "https://example.com/zcode-token" in urls
    assert "https://example.com/gemini-student" in urls


def test_exclude_words_win():
    wf = make_filter()
    items = load_items()
    urls = {i["url"] for i in wf.hits(items)}
    # a3 含"广告"，即使带"福利"也不推
    assert "https://example.com/ads" not in urls


def test_non_welfare_not_matched():
    wf = make_filter()
    items = load_items()
    urls = {i["url"] for i in wf.hits(items)}
    assert "https://example.com/ds-v41" not in urls
    assert "https://example.com/ai-safety" not in urls


def test_drop_pushed_dedups_by_url():
    wf = make_filter()
    hits = wf.hits(load_items())
    st = State()
    st.mark_pushed("https://example.com/zcode-token")
    remaining = drop_pushed(hits, st)
    urls = {i["url"] for i in remaining}
    assert "https://example.com/zcode-token" not in urls
    assert "https://example.com/gemini-student" in urls


def make_item(title, title_en=""):
    return {"title": title, "title_en": title_en, "signals": []}


def test_ascii_word_boundary():
    wf = WelfareFilter(["trial", "free"])
    assert not wf.match(make_item("Industrial AI 传感器上新"))
    assert not wf.match(make_item("freedom of speech 讨论"))
    assert wf.match(make_item("Free trial 开放申请"))


def test_english_plural_tolerated():
    wf = WelfareFilter(["student", "credit"])
    assert wf.match(make_item("", "Google offers students free credits"))
    assert wf.match(make_item("", "student discount program"))


def test_keywords_config_avoids_generic_deal():
    # M1 调优结论：deal 在独立成词时会命中大量收购新闻，禁止进入词表
    import pathlib

    import yaml

    cfg_path = pathlib.Path(__file__).parent.parent / "config" / "keywords.yml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    kws = {k.strip().lower() for k in cfg["welfare_keywords"]}
    assert "deal" not in kws


def test_reason_field_not_matched():
    # 推荐理由不参与匹配，避免长文本噪音
    wf = WelfareFilter(["免费"])
    item = {
        "title": "教师们担心 AI 接管课堂",
        "title_en": "",
        "signals": [],
        "reason": "文中提到教师可免费领取资源",
    }
    assert not wf.match(item)
