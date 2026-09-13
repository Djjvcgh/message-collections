# AI 情报推送器 · 设计方案

> 版本 v0.4（随实施同步修订） · 2026-09-13
> 实施状态：**M1 已上线**（即时层 + 日报层）、**M2 已上线**（页面 diff 监控）；M3 调优补源未开始

## 1. 背景与目标

持续跟踪两类 AI 信息：

| 类型 | 示例 | 优先级 |
|---|---|---|
| **A. 福利活动** | ZCode 送 token、Gemini 学生优惠、限时免费、API 折扣 | 最高（即时推送） |
| **B. 行业大事** | DeepSeek 降价、Codex 重置、Gemini 新模型发布 | 次高（分组日报） |

需求约束：

- **推送制**：信息主动送达，不是手动查询拉取
- **免挑选**：自动过滤，不需要人工刷信息流
- **防错过**：福利即时提醒 + 每日两次分组日报兜底
- **零成本、免维护**：无自有服务器、无付费 API
- **渠道**：Telegram Bot 主渠道（App 内置 MTProto 代理，非全天开代理也能收）；邮件兜底（可选）

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
- **数据源 2**：官方页面 diff 监控（M2，补"定价变更/优惠上线"新闻盲区）
- **数据源 3（二期可选）**：RSSHub 补 X 账号、Linux.do、Telegram 公开频道
- 推送渠道：以接口抽象（`notify_base.py`），Telegram Bot 为标准实现；以后接企业微信群机器人只需新增实现 + 一个 Secret

### 2.1 运行方式（本地电脑无需开机）

项目完全运行在 GitHub 云端：到点自动在临时容器里执行"拉数据 → 过滤 → 推送 → state commit 回仓库"，跑完销毁。本地只在改配置/调试时需要开机。Telegram 消息存云端多端同步。费用为 0。

## 3. 信息源设计

### 3.1 ai-news-radar（主源）

- 地址：`https://news.learnprompt.pro/data/latest-24h.json`（原 github.io 地址已 301）
- 实测质量：85 信源 / 24h 窗口 200+ 条 AI 强相关 / 30 分钟更新 / 官方一手源（T0）优先 / `ai_label` 分类 / 自带中文摘要 `recommend_reason_zh`
- 降级策略：`generated_at` 距今超 36 小时视为上游故障 → 本轮跳过

### 3.2 页面 diff 监控（M2，已上线）

对固定清单页面定期抓正文与指纹快照对比，变化即推送：

| 页面 | 覆盖 |
|---|---|
| DeepSeek API 定价页 | 降价/调价 |
| OpenAI 定价页（platform.openai.com/docs/pricing） | 定价/Codex 相关 |
| 智谱 BigModel 定价页 | ZCode/GLM 送 token、调价 |
| Z.ai API 页 | GLM 定价与权益 |

实现要点：

- **抓取走 r.jina.ai**（渲染 JS、输出稳定 markdown），失败回退直连；直连 HTML 因动态内容多、快照噪声大而只做兜底
- 快照只存 **SHA 指纹 + 字符数**（一行文本），不存全文，git 不膨胀
- 首访页面静默落基线不推送；无变化不写盘；推送成功才更新快照（失败自动下轮重试）
- Google 学生优惠页跳登录页已弃用；**GitHub 学生包页因内容含轮播位实测抖动 20 字符被移除**——含轮播/计数器的页面不适合此方案，接入新页面前先观察两轮指纹稳定性

### 3.3 RSSHub 补源（M3，未实施）

官方 X 账号（免费方案需自建 RSSHub + cookie，或 SocialData 付费 API）、Linux.do 板块、`t.me/s/` 公开频道网页预览。

## 4. 推送策略

### 4.1 即时层（福利，低量高保真）

- 触发：**AI 相关 × 福利词**双重命中（中英文词表，英文词带词边界，`config/keywords.yml`）
- 防重复：同一 URL 永不重复推送；不设条数上限
- 样式：`🎁 [福利] 标题` → 引用块摘要 → `via 来源`（URL 内嵌来源名），三段空行分隔

### 4.2 日报层（行业大事，兜底）

- 时间：每天北京 09:23 / 21:23 各一轮（cron 错峰，见第 7 节）
- **每组各发一条消息**：🎁 福利速递（8条）/ 🚀 模型发布（6条）/ 🧰 产品与工具（5条）/ 💬 值得注意（4条），有条目才发
- 条目样式：`编号. 加粗标题` → 引用块完整摘要（不截断）→ `via 来源`，全部空行分隔
- 页脚标签：`#AI日报 #组名 #信号词top3`，Telegram 内可点击筛选
- 排序：官方一手源（tier）优先，同层按 AI 相关分
- **按组去重**：记账键为「日期_槽位_组名」，某组发送失败下轮只补发该组

### 4.3 英文自动翻译

- 范围：将要展示的标题与摘要；以英文为主（CJK 占比 < 25%）才触发
- 降级链：Google gtx → MyMemory → 保留原文；任一失败静默降级，不阻塞推送
- 缓存：`state/translations.json` 按标题哈希缓存，同一标题只译一次

## 5. 状态管理与去重

- `state/state.json`：已推送 URL（滚动 7 天）+ 日报记账（`日期_槽位_组名`）
- `state/translations.json`：译文缓存
- `state/pages/<name>.txt`：页面指纹快照（`sha256前16位 字符数`）
- 每次运行后 state 变化 commit 回仓库 —— 仓库持续有提交，规避 GitHub"60 天无活动停用定时任务"

## 6. 项目结构

```
Message Collections/
├── pusher/
│   ├── fetch_radar.py       # 拉取 + 解析 radar JSON（含新鲜度检查）
│   ├── filter.py            # 福利关键词过滤（中英文）+ 去重
│   ├── translate.py         # 英文标题/摘要翻译（降级链 + 缓存）
│   ├── watch_pages.py       # 页面 diff 监控（jina 优先/直连兜底/指纹快照）
│   ├── digest.py            # 日报分组、渲染（引用块/via/标签页脚）
│   ├── notify_base.py       # 推送渠道抽象接口
│   ├── notify_telegram.py   # Telegram 实现（主渠道）
│   ├── notify_wecom.py      # 企业微信机器人（预留）
│   ├── notify_email.py      # 邮件（可选预留）
│   └── run.py               # 统一入口（instant / digest）
├── config/
│   ├── sources.yml          # 数据源 + 页面监控清单
│   ├── keywords.yml         # 福利词表 / 排除词
│   └── settings.yml         # 渠道开关、日报条数限制
├── state/                   # 运行状态（git 管理）
├── tests/                   # pytest（36 个用例）
└── .github/workflows/       # instant-push / digest-push / tests
```

技术栈：Python 3.11 + requests + PyYAML；无数据库、无服务器，git 即数据库。

## 7. GitHub Actions 调度

| Workflow | Cron (UTC) | 说明 |
|---|---|---|
| instant-push | `13,43 * * * *` | 每小时 :13/:43 |
| digest-push | `23 1,13 * * *` | 北京 09:23 / 21:23 |
| tests | push / PR 触发 | pytest 全绿门禁 |

**教训**：cron 排在整点/半点会被 GitHub 高峰期大幅延迟（实测 `*/30` 退化为约 2 小时一次、日报直接错过），官方也建议避开整点，故全部错峰。Secrets：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、（预留）`WEWORK_WEBHOOK_URL`、（可选）`SMTP_*`。`workflow_dispatch` 支持手动触发，digest 可传 `slot` 参数用于预览（如 `v3`）。

## 8. 消息格式（实际样式）

**即时提醒：**

```
🎁 [福利] DeepSeek V4.1-Flash 登陆 WorkBuddy，开启限时免费试用

▎DeepSeek 新模型上线第三方客户端，限时全量免费……

via AIbase · AI垂直源
```

**日报（每组一条，示意两条条目）：**

```
🚀 模型发布 · 2026-09-13 09:23（6条）

1. OpenAI 开放 GPT-Live-1 语音 API

▎每分钟 $0.05，把 ChatGPT 同款语音能力交给开发者……

via OpenAI News · 官方一手源

2. DeepSeek 发布 V4.1-Flash

▎登陆 WorkBuddy，开启限时免费试用

via AIbase · AI垂直源

#AI日报 #模型发布 #deepseek #openai
```

**页面更新（M2）：**

```
📄 [页面更新] Models & Pricing | DeepSeek API Docs

▎监控页面内容发生变化（23331 → 23410 字符），可能与定价或优惠调整有关……

via deepseek-pricing
```

## 9. 实施里程碑

| 里程碑 | 状态 | 说明 |
|---|---|---|
| **M1 主链路** | ✅ 2026-09-13 上线 | radar 接入 + 即时推送 + 分组日报 + 翻译 + 按组去重 |
| **M2 页面监控** | ✅ 2026-09-13 上线 | 4 页清单（DeepSeek/OpenAI/BigModel/Z.ai）+ 指纹快照 |
| **M3 调优补源** | 未开始 | 词表按误报率调优、RSSHub 补源、邮件兜底、抖动页面防抖 |

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| radar 上游故障 | 新鲜度检查 + 跳过该轮；极端情况 fork 自持 |
| cron 整点被 GitHub 限流 | 已错峰（:13/:43、:23）；仍可能有小幅漂移 |
| 页面反爬（M2） | r.jina.ai 兜底 / 换 RSS 路由 |
| 关键词误报 | 排除词 + 观察期调优（`deal` 已因误报除名） |
| Google 翻译 429 | MyMemory 降级 + 缓存；全败回退原文 |
| Telegram 代理依赖 | App 内置 MTProto 已确认可行；邮件兜底可选 |

## 11. 仓库与协作

- 仓库：[Djjvcgh/message-collections](https://github.com/Djjvcgh/message-collections)（public，main 分支）
- 遵循 AGENTS.md：每次改动单独 commit，pytest 全绿再交付
- 运行记录：Actions 页可查每次推送日志；`slot` 参数可发预览不污染正常收报
