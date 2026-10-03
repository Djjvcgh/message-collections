"""渲染预览：用真实渲染器打印各类消息样张，不发 Telegram、不写 state。

用法（仓库根目录）：
    python probe/preview.py            # 只打印
    python probe/preview.py --send     # 额外推送到 Telegram（需 .env 凭据，用于人工验收）

样张覆盖：论坛帖（facts_only）、带折扣码的帖子、资讯类、长推文标题重做、
限时情报，以及已知会误报的「免费」评测文（用于对照过滤口味）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pusher.notify_telegram import build_instant_message  # noqa: E402
from pusher.sources.base import as_item  # noqa: E402

SAMPLES = [
    (
        "welfare",
        "论坛帖（facts_only）：原文是一整段流水账，只给要点",
        {
            "title": "[免费赠送] 内网云 2026 国庆活动余额兑换券，附实名认证说明",
            "url": "https://www.v2ex.com/t/1246092",
            "source": "V2EX 优惠信息",
            "layout": "facts_only",
            "extra": {"published": "2026-10-02T02:11:00+00:00"},
            "summary": (
                "几年前在 V2EX 发过内网云的第一版： 原帖链接 当时是基于 SSH 协议做的内网穿透，"
                "这几年陆续更新了不少东西，借国庆发一批余额兑换券，先到先得。 🔗 内网云网址： "
                "https://www.neiwangyun.net 🚀 这几年主要更新 🌐 节点从早期单节点扩展到多地区，"
                "🔒 域名和 SSL 证书集成得更完整 📊 仪表盘和活动中心做了一轮重构 "
                "🎁 兑换券码： NWY-CIYUM-4UYZD-40694 📌 兑换方式： 进入平台仪表盘 →"
            ),
        },
    ),
    (
        "welfare",
        "带折扣码：价格取到手价、码单独成行、速览行不重复",
        {
            "title": "AdGuard Family Plan 终身订阅优惠,9 设备永久授权家庭版 / 60 元",
            "url": "https://www.v2ex.com/t/1246054",
            "source": "V2EX 优惠信息",
            "layout": "facts_only",
            "extra": {"published": "2026-10-01T13:43:00+00:00"},
            "summary": (
                "AdGuard Family Plan 终身订阅优惠 StackSocial 上 AdGuard 家庭版终身订阅： "
                "价格：¥74.00 CNY 折扣码： LIFETIMEO， 额外再减 20% 最终到手价： ¥59.20 CNY"
            ),
        },
    ),
    (
        "welfare",
        "资讯类：标题去掉「别错过这个」、摘要保留完整背景",
        {
            "title": "受裁员影响？别错过这个 75 美元的 TechCrunch Disrupt 2026 Expo+ Pass 优惠",
            "url": "https://techcrunch.com/2026/10/02/disrupt-2026-layoff-expo-plus-passes/",
            "source": "TechCrunch AI",
            "tier_label": "精选AI媒体",
            "summary": (
                "如果你正受 layoffs 影响，这篇内容值得读：TechCrunch Disrupt 2026 的 Expo+ Pass 只要 $75，"
                "限前 100 名符合条件者，原文还提醒 Oct 2 是向 10,000+ tech leaders 订展桌的最后一天。"
            ),
        },
    ),
    (
        "welfare",
        "长推文标题：用摘要重做标题，摘要不再复读",
        {
            "title": (
                "Anthropic 的工程师说：我们内部已经不怎么写 prompt 了，写的是循环。 她在台上花了半小时，"
                "展示了 Claude 团队如何创建能够自我提示的循环。 如果这堂课卖 400 刀，今年 Agent 课的榜单"
                "大概就是它。但它是免费的。 真正拉开代际差距的，不是谁的 Prompt 写得…"
            ),
            "url": "https://x.com/huxlab/status/2106027067527901393",
            "source": "@huxlab",
            "tier_label": "高级源",
            "summary": (
                "Anthropic 工程师用半小时展示 Claude 团队如何创建自我提示的循环，称内部已不写 prompt；"
                "这堂若卖 400 刀的 Agent 课免费，讲记忆从 CLAUDE.md 变成 Agent 自己读写的 memory/。"
            ),
        },
    ),
    (
        "opportunity",
        "限时情报：词表已收窄到「可领取 + 有期限」",
        {
            "title": "某某云开放注册，新用户注册即送 100 元额度，限量 500 个名额",
            "url": "https://example.com/signup",
            "source": "NodeSeek",
            "extra": {"published": "2026-10-03T02:30:00+00:00"},
            "summary": "开放申请截止 10月15日，需实名认证，先到先得。额度可用于云服务器与对象存储。",
        },
    ),
    (
        "welfare",
        "长摘要（资讯类）：起因、历史背景、官方说法都保留，不点原文也能看懂",
        {
            "title": "X 云新用户注册即送 100 元额度，限量 500 个名额",
            "url": "https://example.com/xcloud-promo",
            "source": "NodeSeek",
            "extra": {"published": "2026-10-03T03:00:00+00:00"},
            "summary": (
                "最近云服务市场又有新动作。想知道这家新秀值不值得上手，读这篇就够了。"
                "X 云宣布新用户注册即可免费领取 100 元额度，可用于云服务器与对象存储，"
                "名额限前 500 名，活动截止 10月15日。"
                "之所以推出这轮补贴，是因为年底前要冲一波开发者规模。"
                "此前该平台在 2024 年也做过类似的赠送活动，当时额度只有 50 元，两小时就被领完。"
                "官方表示这次会分批放量，并承诺不绑定自动续费。"
            ),
        },
    ),
    (
        "welfare",
        "对照：收窄「免费」后不再命中的评测文（此前会误报）",
        {
            "title": "我试用了 Gemini 免费的新电影制作工具；好莱坞完全没什么好担心的",
            "url": "https://www.androidpolice.com/i-tried-geminis-free-new-movie-maker/",
            "source": "Android Police",
            "tier_label": "热议参考",
            "summary": (
                "想知道 Google Vids 的免费 AI 视频生成到底多好笑，读这篇：作者称它像 Windows Movie Maker "
                "般易上手，却把他写的 Apple Store 员工发现 Android 手机的脚本生成得一团乱。"
            ),
        },
    ),
]


def strip_tags(text):
    return (
        text.replace("<b>", "")
        .replace("</b>", "")
        .replace("<blockquote>", "")
        .replace("</blockquote>", "")
    )


def main():
    messages = [
        (kind, note, build_instant_message(as_item(raw), kind=kind))
        for kind, note, raw in SAMPLES
    ]
    for index, (kind, note, msg) in enumerate(messages, 1):
        visible = strip_tags(msg)
        print(f"\n{'=' * 72}\n样张 {index}（{kind}，{len(visible)} 字）· {note}\n{'=' * 72}")
        print(visible)

    if "--send" in sys.argv:
        from pusher.run import SETTINGS_PATH, build_channels, load_env, load_yaml

        load_env()
        channels = build_channels(load_yaml(SETTINGS_PATH))
        if not channels:
            print("\n未找到可用渠道（检查 .env 里的 TELEGRAM_*）")
            return 1
        for index, (_, _, msg) in enumerate(messages, 1):
            for channel in channels:
                channel.send(f"<b>【样张 {index}】</b>\n\n{msg}")
        print(f"\n已推送 {len(messages)} 条样张")
    return 0


if __name__ == "__main__":
    sys.exit(main())
