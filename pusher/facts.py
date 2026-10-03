"""从帖子正文里抽出「能直接用的硬信息」：折扣码、价格、截止时间、资格条件。

实测依据（2026-10-03，376 条真实条目）：
- **带标签的码**（`兑换券码：NWY-CIYUM-4UYZD-40694`、`折扣码：LIFETIMEO`）零误报；
- **裸匹配码**会把 `-8259U`（CPU 型号）、`H11SSL-NC`（主板）、`1086110586937`（快递单号）
  当成折扣码，所以只认带标签的；
- V2EX 的正文里常有 `My blog https://…` 这类签名与闲聊，长段落不当摘要展示。
"""
import re

# 折扣码 / 邀请码：必须带标签，避免把型号、日期、单号当码
_CODE_LABELED = re.compile(
    r"(?:折扣码|优惠码|兑换码|兑换券码|券码|邀请码|激活码|promo\s*code|coupon\s*code|discount\s*code)"
    r"\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9_-]{3,23})",
    re.I,
)
# 金额：带符号或带单位，单个汉字/单字母不做单位
_PRICE = re.compile(
    r"(?:[$￥¥€£]\s?\d[\d,]*(?:\.\d+)?"
    r"|\d[\d,]*(?:\.\d+)?\s?(?:美元|元|块|人民币|港币|日元|欧元|USD|usd|RMB|rmb|EUR|JPY))"
)
# 折扣力度：只认真实让利表述，「终身/年付」这类是形式不是折扣（见 _FORM）
_DISCOUNT = re.compile(
    r"(?:\d+(?:\.\d+)?\s?折|\d+\s?%\s?(?:off|折扣|优惠)?|半价|买一送一|免费领取|0\s?元)"
)
# 截止 / 有效期
_DEADLINE = re.compile(
    r"(?:截止|截至|申领截止|报名截止|有效期至|deadline|ends?|until|expires?)"
    r"[^。！？!?\n]{0,24}"
    r"|\d{1,2}\s?月\s?\d{1,2}\s?日"
    r"|\d{1,2}/\d{1,2}(?:/\d{2,4})?"
    r"|\d{4}-\d{2}-\d{2}"
    r"|(?:今天|今日|明天|本周|本月|最后\s?\d+\s?[天小时])"
)
# 资格 / 领取条件
_ELIGIBILITY = re.compile(
    r"(?:学生|教师|教育|校园|实名|新用户|首单|老用户|会员|限\s?\d+\s?(?:名|位|个|份)|"
    r"前\s?\d+\s?(?:名|位|个|份)|限量|名额|先到先得|需|即可|认证)[^。！？!?\n]{0,30}"
)
# 形式 / 类型
_FORM = re.compile(
    r"(?:终身|永久|年付|月付|订阅|家庭版|个人版|团队版|兑换券|代金券|礼品卡|"
    r"免费额度|free\s?tier|免费试用|试用|内测资格)"
)

# 「明确优惠信号」：判断一条帖子是不是真的在发福利
_OFFER_SIGNAL = re.compile(
    r"(?:免费|0\s?元|零元|白嫖|赠送|领取|免费额度|免费域名|免费服务器|免费机|"
    r"折扣码|优惠码|兑换码?券?|优惠券|代金券|折扣|优惠|特价|促销|返现|"
    r"free\s?tier|free\s?credits|free\s?trial|giveaway|promo\s?code|coupon)",
    re.I,
)
# 二手交易 / 求推荐：这些不是在发福利
_TXN_SIGNAL = re.compile(
    r"(?:^|[【\[（(])(?:收|出|售|求购|求推荐|拼车|车找人|剩余价值|溢价|折价)[】\]）)]?"
    r"|(?:收|出)\s?[A-Za-z0-9\u4e00-\u9fff]{1,10}\s?(?:机|号|券|码)"
)

FACT_LABELS = ("价格", "折扣", "折扣码", "截止", "形式")


def _get(item, field, default=""):
    if isinstance(item, dict):
        value = item.get(field)
    else:
        value = getattr(item, field, None)
    return default if value is None else value


def item_text(item):
    """抽取用的完整文本：标题 + 摘要（首尾去空白）。"""
    return f"{_get(item, 'title')} {_get(item, 'summary')}".strip()


def extract_facts(text):
    """按固定顺序抽事实，每个标签最多一条。"""
    facts = []
    seen = set()

    def add(label, value):
        value = re.sub(r"\s+", " ", (value or "").strip(" :：,，。;；"))
        if not value or label in seen:
            return
        seen.add(label)
        facts.append((label, value))

    # 优先找「到手价 / 最终到手价」，它比原价更有用
    final_price = re.search(
        r"(?:到手价|实付|现价|券后|最终价)\s*[:：]?\s*(?:"
        + _PRICE.pattern
        + r")",
        text,
    )
    if final_price:
        # _PRICE 内部是 (?:…) 非捕获组，只能取整体匹配，不能用 group(1)
        price_value = _PRICE.search(final_price.group(0))
        if price_value:
            add("价格", f"{price_value.group(0)}（到手价）")
    else:
        price = _PRICE.search(text)
        if price:
            add("价格", price.group(0))

    discount = _DISCOUNT.search(text)
    if discount:
        add("折扣", discount.group(0))

    code = _CODE_LABELED.search(text)
    if code:
        add("折扣码", code.group(1).upper() if code.group(1).isascii() else code.group(1))

    deadline = _DEADLINE.search(text)
    if deadline:
        add("截止", deadline.group(0))

    form = _FORM.search(text)
    if form:
        add("形式", form.group(0))
    # 说明：「资格/条件」这类字段实测不可靠——正则很容易从论坛正文里吞下一大段
    # 无关文字（曾把「实名认证说明 几年前我在 V2EX 发过…」整段当成资格）。
    # 条件信息通常已在标题或摘要里出现，故不再单独抽取。
    return facts


def has_offer_signal(item):
    """是否含「明确优惠信息」。用于只推真福利的源（如 V2EX 优惠信息）。"""
    return bool(_OFFER_SIGNAL.search(item_text(item)))


def is_secondhand_or_request(item):
    """二手交易 / 求推荐帖：不是福利，不管命中什么词都不该推。"""
    title = _get(item, "title")
    return bool(_TXN_SIGNAL.search(title))


def is_actionable(item):
    """判定是否值得推送：有明确优惠，且不是二手交易/求推荐。"""
    if is_secondhand_or_request(item):
        return False
    return has_offer_signal(item)
