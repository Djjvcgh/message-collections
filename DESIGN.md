# AI 情报推送器 · 设计方案

> 版本 v0.1（草案，待评审） · 2026-09-12
> 状态：**待用户过目**，确认后按里程碑 M1 → M2 → M3 实施

## 1. 背景与目标

持续跟踪两类 AI 信息：

| 类型 | 示例 | 优先级 |
|---|---|---|
| **A. 福利活动** | ZCode 送 token、Gemini 学生优惠、限时免费、API 折扣 | 最高（即时推送） |
| **B. 行业大事** | DeepSeek 降价、Codex 重置、Gemini 新模型发布 | 次高（日报+大事即时） |

需求约束：

- **推送制**：信息主动送达，不是手动查询拉取
- **免挑选**：自动过滤，不需要人工刷信息流
- **防错过**：重要消息即时提醒 + 每日摘要兜底
- **零成本、免维护**：无自有服务器、无付费 API
- **渠道**：Telegram Bot 主渠道（已确认：App 内置 MTProto 代理，非全天开代理也能收）；邮件兜底（可选）

## 2. 总体架构

```
┌─────────────────────────────────────────────┐
│            GitHub Actions (cron)            │
│                                             │
│  ┌───────────┐   ┌───────────┐   ┌───────┐  │
│  │  数据采集  │ → │ 过滤/去重  │ → │ 推送器 │  │
│  └───────────┘   └─────┬─────┘   └───┬───┘  │
└────────────────────────┼─────────────┼──────┘
                         ▼             ▼
                   state/ 快照     Telegram Bot
                  （commit 回仓库）  （+ 可选邮件）
```

- 运行环境：GitHub Actions 定时任务（公开仓库免费不限时）
- **数据源 1**：ai-news-radar 公开 JSON（主源，覆盖行业大事 + 大量 X/媒体信号）
- **数据源 2**：官方页面 diff 监控（补"定价变更/优惠上线"这类新闻盲区）
- **数据源 3（二期可选）**：RSSHub 补 X 账号、Linux.do、Telegram 公开频道

## 3. 信息源设计

### 3.1 ai-news-radar（主源，即时 + 日报）

- 地址：`https://news.learnprompt.pro/data/latest-24h.json`
  （注意：原 `learnprompt.github.io` 地址已 301 到此域名）
- 已验证的数据质量（2026-09-12 实测）：85 信源 / 24h 窗口 231 条 AI 强相关 / 30 分钟更新 / 官方一手源（T0）优先排序 / `ai_label` 分类齐全
- 用途：福利关键词即时推送、每日日报、行业大事追踪
- 降级策略：`generated_at` 距今超过 36 小时视为上游故障 → 本轮跳过，并在下一条日报中如实标注

### 3.2 页面 diff 监控（补盲区）

新闻抓取对"定价调整、学生优惠悄悄上线"这类**页面内容变化**不敏感。对固定清单页面定期抓正文（反爬失败时用 r.jina.ai 兜底），与上次快照对比，内容变化即推送。

初始监控清单（实施时逐个验证可达性与反爬策略，可增删）：

- DeepSeek API 定价页
- OpenAI 定价页 / Codex 相关页面
- Google One / Gemini 学生优惠页
- Z.ai（ZCode）/ BigModel 公告与定价页
- GitHub Copilot 学生/教师免费页

### 3.3 RSSHub 补源（二期，可选）

- 官方 X 账号 → RSSHub 路由（免费方案需自建 RSSHub + 账号 cookie，token 会过期需偶尔维护；或 SocialData 等付费 API，量小很便宜）
- Linux.do 福利板块、`t.me/s/<频道名>` 公开频道网页预览（零鉴权可抓）
- 你加入的 Telegram 私群：Telethon 用户号 API 可读（灰色用法，二期再评估）

## 4. 推送策略（两层，解决"免挑选 + 防错过"）

### 4.1 即时层（低量高保真）

- 触发条件：**AI 相关 × 福利词** 双重命中（页面 diff 有变化默认触发）
- 福利词表（配置化，`config/keywords.yml`）：
  - 中文：免费 / 白嫖 / 送 / 赠 / 福利 / 优惠 / 折扣 / 学生 / 限时 / 领取 / 试用 / 额度
  - 英文：free / promo / discount / student / credit / giveaway / trial / coupon / deal
  - 排除词：广告、标题党黑名单
- 防轰炸：单日即时推送上限 8 条，超出的并入次日日报；同一 URL 永不重复推送
- 时效：30 分钟内送达（跟随 radar 更新节奏）

### 4.2 日报层（兜底）

- 时间：每日北京时间 09:00 / 21:00 各一条（可调）
- 内容：按类别分组（福利速递 / 模型发布 / 产品与工具 / 值得注意），官方一手源优先、重要度排序，每条带原文链接
- 可选同步发一份邮件副本

## 5. 状态管理与去重

- `state/state.json`：已推送条目索引（url hash → 日期），滚动保留 7 天
- `state/pages/<name>.txt`：页面监控快照 + 内容 hash
- 每次运行后把 state 变化 commit 回仓库 —— 副作用是仓库持续有提交，**Actions 定时任务不会触发 GitHub"60 天无活动自动停用"**（TrendRadar 需要手动签到续期的问题在这里天然规避）

## 6. 项目结构

```
Message Collections/
├── pusher/
│   ├── fetch_radar.py       # 拉取 + 解析 radar JSON（含新鲜度检查）
│   ├── watch_pages.py       # 页面 diff 监控
│   ├── filter.py            # 关键词过滤 + 去重 + 限额
│   ├── notify_telegram.py   # Telegram 推送
│   ├── notify_email.py      # 邮件推送（可选）
│   ├── digest.py            # 日报组装
│   └── run.py               # 统一入口
├── config/
│   ├── sources.yml          # 页面监控清单 + 各源开关
│   └── keywords.yml         # 福利词表 / 排除词 / 限额
├── state/                   # 运行状态（git 管理）
├── tests/                   # pytest：过滤、去重、日报组装等纯逻辑
└── .github/workflows/pusher.yml
```

技术栈：Python 3.11 + requests，最小依赖；无数据库、无服务器，git 即数据库。

## 7. GitHub Actions 调度

| Workflow | Cron (UTC) | 说明 |
|---|---|---|
| pusher.yml | `*/30 * * * *` | 与 radar 30 分钟更新节奏对齐 |
| 日报（同一 workflow 内） | `0 1,13 * * *` | UTC 01:00/13:00 = 北京 09:00/21:00 |

- `workflow_dispatch` 手动触发用于调试
- Secrets：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、（可选）`SMTP_*`
- 注意：Actions cron 有 ±数分钟漂移，福利场景可接受

## 8. 消息格式（示意）

**即时提醒（福利命中）：**

```
🎁 [福利] DeepSeek V4.1-Flash 登陆 WorkBuddy，限时免费试用
来源：AIbase · 官方/媒体分层 · 2 小时前
https://www.aibase.com/news/30995
```

**即时提醒（页面变化）：**

```
📉 [定价变更] DeepSeek API 定价页内容有更新
对比摘要：输入价格 $0.27/M → $0.20/M（待实施时自动生成）
https://api-docs.deepseek.com/quick_start/pricing
```

**每日日报：**

```
🤖 AI 日报 · 09-12
■ 福利速递 (2)
 ■ 🎁 大模型六重奏：6 款模型 14 天免费不限量（9.11–9.25） — @nhxao
■ 模型发布 (5)
 ■ OpenAI 开放全双工语音模型 GPT-Live-1 API（$0.05/min） — AIbase
 ■ …
■ 产品与工具 (3)
■ 值得注意 (2)
```

## 9. 实施里程碑

| 里程碑 | 内容 | 验收标准 |
|---|---|---|
| **M1 主链路** | radar 接入 + 福利即时推送 + Telegram + 每日日报 + 去重状态 | 手动触发跑通真实推送；pytest 全绿 |
| **M2 页面监控** | diff 监控 + 变更推送 | 初始清单 5 个页面跑通 |
| **M3 调优补源** | RSSHub X/Linux.do、词表按误报率调优、邮件兜底 | 观察一周误报 < 每天 1 条 |

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| radar 上游故障 | 新鲜度检查 + 日报标注；极端情况 fork 一份自持 |
| Actions cron 漂移/排队 | 接受 5–15 分钟延迟；福利场景可接受 |
| 页面反爬 | r.jina.ai 兜底 / 换 RSS 路由 |
| 关键词误报 | 排除词 + 上限保护 + 观察期调优 |
| X 直连成本 | 二期再评估：免费 cookie 方案 vs 付费 API |
| Telegram 代理依赖 | 内置 MTProto 已确认可行；邮件兜底可选 |

## 11. 仓库与协作方式

- 仓库：当前 workspace（Message Collections）直接作为项目仓库，push 到你的 GitHub **公开仓库**（Actions 免费的关键）
- 遵循 AGENTS.md：每个里程碑完成后单独 commit，pytest 全绿再交付
- **待你确认的事项**：
  1. 仓库放当前 workspace 并新建 GitHub 公开 repo，是否 OK？
  2. 日报时间 09:00 / 21:00（北京时间）是否合适？
  3. Bot token 届时配置为 GitHub Secret（不进代码、不进对话）
