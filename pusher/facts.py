"""从帖子正文里抽出「能直接用的硬信息」：折扣码、价格、截止时间、形式。

实测依据（2026-10-03，376 条真实条目）：
- **带标签的码**（`兑换券码：NWY-CIYUM-4UYZD-40694`、`折扣码：LIFETIMEO`）零误报；
- **裸匹配码**会把 `-8259U`（CPU 型号）、`H11SSL-NC`（主板）、`1086110586937`（快递单号）
  当成折扣码，所以只认带标签的；
- V2EX 的正文里常有 `My blog https://…` 这类签名与闲聊，长段落不当摘要展示。

这里同时提供三个判定谓词，供分类层组合使用（2026-10-05 新增）：
- `has_offer_signal`：有「硬福利证据」（免费额度、折扣码、价格折扣、free credits……）
- `has_claim_signal`：有「可领取动作」（领取/申请/注册/报名/兑换/邀请码……）
- `has_window_signal`：有「窗口」（截止/限量/名额/先到先得/最后 N 天……）
这三者的证据文本都先经过 `mask_negated`，所以「Gemini 将结束…免费使用」
这种「福利正在消失」的资讯不会产生任何证据。
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
# 带标注的价格允许不带单位：实测「原价 130，现价 120」因为 120 没有单位被跳过，
# 于是取到了正文里第一个带单位的数字（「14 元/年保号」），价格整条是错的。
# 先找「到手价/实付/现价/券后」这类读者真正要付的钱，再退到原价/标价。
_FINAL_PRICE = re.compile(
    r"(?:到手价|实付|现价|券后|最终价)\s*[:：]?\s*[$￥¥€£]?\s?\d[\d,]*(?:\.\d+)?"
)
_PLAIN_PRICE = re.compile(
    r"(?:原价|价格|标价)\s*[:：]?\s*[$￥¥€£]?\s?\d[\d,]*(?:\.\d+)?"
)
# 单价陷阱：「14 元/年保号」是套餐单价，不是售价
_PRICE_PER_UNIT = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s?(?:美元|元|块)?\s?/\s?(?:年|月|天|小时|GB|G|M|T|人|个)"
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

# ---------------------------------------------------------------- 否定语境
# 「福利正在消失」的说法必须先抹掉，否则 免费/赠送 会被蹭成福利。
# 方向敏感：动词在福利词之前（将结束…免费），或显式失效短语（不再免费/开始收费）。
# 反例保护：「限时免费领取，月底结束」是截止预告，不是福利消失，
# 所以「免费」后面只接 使用/服务/额度/政策/计划/期 这类*政策名词*时才判为被取消。
_NEGATED = re.compile(
    r"(?:(?:将|即将|已|正式|宣布|确认|决定|计划)[^。！？!?\n]{0,6}"
    r"(?:结束|停止|取消|终止|下线|关停)[^。！？!?\n]{0,12}(?:免费|赠送|优惠|福利)"
    r"|不再(?:免费|赠送|优惠|提供免费|支持免费)"
    r"|(?:取消|停止|终止|关闭)(?:免费|赠送|优惠|福利)"
    r"|(?:免费|优惠|福利)(?:使用|服务|额度|政策|计划|期|时长)[^。！？!?\n]{0,6}"
    r"(?:将|即将|已)?(?:结束|终止|取消|下线|关停)"
    r"|(?:优惠码|折扣码|兑换码|兑换券|优惠券|邀请码)(?:已)?(?:作废|失效|过期|无效)"
    # 英文「已经结束了」：实测 Telegram 免费频道里「Giveaway for $500 USDT Ended!」
    # 会被 giveaway 命中，实际是活动已结束的通告
    r"|(?:giveaway|promo|offer|sale|deal|coupon|discount|trial)[^。.!?！？\n]{0,30}"
    r"(?:ended|over|expired|closed|finished)"
    r"|no\s+longer\s+(?:free|available|offered)"
    r"|恢复原价|开始收费|将?转(?:为|成)收费|改(?:为|成)收费)",
    re.I,
)
_NEGATION_MASK = "〇" * 6

# 「这条公告已经作废」：赠送帖寿命很短，被领完/送完后推出去只会让人白跑一趟。
# 与否定语境不同，这不是「抹掉某个词」而是**整条丢弃**——
# 「[已送出] 内网云兑换券」里 兑换券 是真词，但消息本身已经没有价值。
# 逐条列出完整说法：写成「送(完|出)」会把「免费送出 100 个兑换码」误伤。
_FINISHED = re.compile(
    r"(?:已送出|已送完|都送完|送完了|已领完|都领完|领完了|已抢完|抢光了"
    r"|已发完|发完了|已兑完|兑完了|已没了|已过期|已结束|已作废|已失效"
    r"|out\s+of\s+stock|sold\s+out)",
    re.I,
)


def is_finished(item):
    """公告已作废（领完/送完/过期/已结束）：整条不该推。"""
    return bool(_FINISHED.search(evidence_text(item)))


def mask_negated(text):
    """把「福利即将消失 / 已经取消」的表述抹成占位符。

    这样关键词匹配与证据判定都读不到那些 免费/优惠，
    不必在每个正则里各写一遍前后文检查。
    """
    return _NEGATED.sub(_NEGATION_MASK, text or "")


# ---------------------------------------------------------------- 硬信号 / 动作 / 窗口
# 「硬福利证据」：真的能拿到手的东西。**故意不收 优惠/折扣/学生 这类宽词**——
# 弱词表里也有它们，共现门槛必须由另一个词来满足，否则等于没门槛。
_OFFER_SIGNAL = re.compile(
    r"(?:免费(?:额度|领取|获得|申请|申领|发放|试用|体验|域名|服务器|主机|小鸡|vps|证书|ssl|邮箱|api|送|拿|得|赠送)"
    r"|0\s?元|零元|白嫖|白送|赠送"
    # 「送 1000 额度」「邀请送 1000 额度」这类中间带数字的写法很常见，
    # 死抠「送额度」会漏掉（实测 V2EX 免费赠送节点的 Codex 额度帖就是这样）。
    # (?<!验证) 挡住「发送验证码」这种把 送+码 当成福利的误判。
    r"|送[^。！？!?\n]{0,6}?(?:token|额度|会员|一年|三个月|积分|点数|美元|元|券|(?<!验证)码)"
    r"|领取|申领|兑换码|兑换券|折扣码|优惠码|券码|优惠券|代金券|邀请码"
    r"|返现|半价|\d+(?:\.\d+)?\s?折|\d+\s?%\s?(?:off|折扣|优惠)"
    r"|(?:到手价|实付|现价|券后|最终价)\s*[:：]?\s*[$￥¥€£]?\s?\d"
    r"|free\s?(?:tier|credits?|trial)|giveaway|promo\s?code|discount\s?code|student\s?discount)",
    re.I,
)
# 「可领取动作」：读者能做的动作，比价格与折扣更宽松
_CLAIM_SIGNAL = re.compile(
    r"(?:免费|领取|申请|注册|报名|兑换|申领|试用|体验|白嫖|赠送|预约|抢购|候补|内测|公测|邀请码"
    r"|sign\s?up|early\s?access|waitlist|apply)",
    re.I,
)
# 「窗口」：错过就没了的证据
_WINDOW_SIGNAL = re.compile(
    r"(?:截止|截至|限量|限额|名额|先到先得|售完|抢完|最后\s?\d+\s?(?:天|小时|个|份|名)"
    r"|今天|今日|明天|本周|本月|报名|抽签|开放注册|开放申请|即将结束|限时|仅限"
    r"|deadline|limited|first\s?come|ends?\s)",
    re.I,
)


def _get(item, field, default=""):
    if isinstance(item, dict):
        value = item.get(field)
    else:
        value = getattr(item, field, None)
    return default if value is None else value


def item_text(item):
    """抽取用的完整文本：标题 + 摘要（首尾去空白）。"""
    return f"{_get(item, 'title')} {_get(item, 'summary')}".strip()


def evidence_text(item):
    """判定用的文本：先抹掉否定语境，再交给各证据正则。"""
    return mask_negated(item_text(item))


def extract_facts(text):
    """按固定顺序抽事实，每个标签最多一条。"""
    text = text or ""
    facts = []
    seen = set()

    def add(label, value):
        value = re.sub(r"\s+", " ", (value or "").strip(" :：,，。;；"))
        if not value or label in seen:
            return
        seen.add(label)
        facts.append((label, value))

    # 优先取带标注的价格（到手价/现价/券后），它比原价有用；标注价允许不带单位
    final = _FINAL_PRICE.search(text)
    labeled = final or _PLAIN_PRICE.search(text)
    if labeled:
        money = _PRICE.search(labeled.group(0))
        raw = money.group(0) if money else re.sub(r"^[^\d]+", "", labeled.group(0)).strip()
        add("价格", f"{raw}（到手价）" if final else raw)
    else:
        # 跳过「14 元/年」这类套餐单价，它不是售价
        for match in _PRICE.finditer(text):
            if _PRICE_PER_UNIT.match(text[match.start(): match.start() + 12]):
                continue
            add("价格", match.group(0))
            break

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
    """是否有「硬福利证据」。弱词共现门槛与限时判定都靠它。"""
    return bool(_OFFER_SIGNAL.search(evidence_text(item)))


def has_claim_signal(item):
    """是否有可领取 / 可报名的动作。"""
    return bool(_CLAIM_SIGNAL.search(evidence_text(item)))


def has_window_signal(item):
    """是否有期限 / 名额这类窗口。"""
    return bool(_WINDOW_SIGNAL.search(evidence_text(item)))


# 二手交易 / 求推荐 / 私聊成交：这些不是在发福利
_TXN_SIGNAL = re.compile(
    # 1) 串首或方括号里的交易动词（「收 berohost」「【出】出懒猫云优惠码」「求推荐 VPS」）
    r"(?:^|[\s【\[（(])(?:收|出|售|求购|求推荐|拼车|车找人|剩余价值|溢价|折价|二手|闲置|转手|转卖)"
    r"[\s】\]）)]?"
    # 2) 交易动词 + 实物/账号名词：「优惠价出一个香港 CSL esim」「出 H11SSL-NC 主板」。
    #    (?<![送赠领发派推]) 保证「免费送出 100 个兑换码」「推出 100 个兑换码」不被误判
    r"|(?<![送赠领发派推])(?:出|收)(?:一|1|两|个|台|部|把|张|块|只|条|\s){0,4}"
    r"[A-Za-z0-9\u4e00-\u9fff][^。！？!?\n，,]{0,12}?"
    r"(?:机|号|券|码|卡|esim|vps|鸡|账号|订阅|会员|显卡|内存|硬盘|主板|耳机|显示器|设备)"
    # 3) 私聊成交（「联系绿泡泡 xxx」「联系 tg @xxx」），正经活动不会只留私聊
    r"|(?:联系\s*(?:我|私聊|tg|telegram|微信|vx|绿泡泡|企鹅|qq)|加\s?(?:我|tg|微信|vx)\s?[:：@])"
    # 4) 索要帖（「有大哥能送个金会员吗」，V2EX 免费赠送节点实测存在）。
    #    注意不能写成「能送/请送」这种裸词——「邀请送」里就含「请送」，
    #    会把真福利误判成索要帖；这里要求出现请求语气（…吗 / 哪位…给）。
    r"|(?:求送|求个|有偿|跪求|(?:能|可以)送[^。！？!?\n]{0,4}吗|哪位[^。！？!?\n]{0,8}(?:送|给)|谁能[^。！？!?\n]{0,8}(?:送|给))",
    re.I,
)

FACT_LABELS = ("价格", "折扣", "折扣码", "截止", "形式")


def is_secondhand_or_request(item):
    """二手交易 / 求推荐帖：不是福利，不管命中什么词都不该推。"""
    return bool(_TXN_SIGNAL.search(evidence_text(item)))


def is_actionable(item):
    """判定是否值得推送：有可领取的动作，且不是二手交易 / 求推荐。

    2026-10-05 放宽为「硬信号 或 可领取动作」：只认硬信号时，
    V2EX 优惠信息 50 条只放行 2 条，其中一条还是二手转卖。
    """
    if is_secondhand_or_request(item):
        return False
    return has_offer_signal(item) or has_claim_signal(item)
