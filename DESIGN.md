# 情报推送器 · 设计方案

> 版本 v0.6 · 2026-10-05
> 本版是**内容与精度**修复：消息必须有正文、噪音必须进不来、【限时】通道恢复，
> 并把「为什么没推」变成可观测的数字（`--report`）。
> 上一版 v0.5 是减法 + 拓源（删掉日报层与页面 diff 监控，单主源改多信源聚合）。
>
> **本版核心发现（实测，见 4.6 节）**：线上 47.5 小时只推了 3 条，而这 3 条**全是噪音**；
> 修复后同一批信源里噪音全部被拦下，剩下暴露出来的是**真供给本身稀疏**——
> 这不是放宽筛选能解决的，只能靠信源扩充 + 可观测性（`--report` 与候选体检工具）持续调。

## 1. 背景与目标

只推一类信息：**现在能拿到手、错过就没了的东西**。

| 类型 | 示例 | 标识 |
|---|---|---|
| **A. 福利** | 送 token、免费额度、学生优惠、免费域名、白嫖服务器、优惠码 | `🎁 [福利]` |
| **B. 限时窗口** | 开放注册、限量名额、领取截止、即将收费 | `⏳ [限时]` |

需求约束：

- **推送制**：主动送达，不是手动查询
- **免挑选**：自动过滤，不用人工刷信息流
- **零成本、免维护**：无服务器、无付费 API、无 Key
- **不限 AI 领域**：AI 福利只是其中一类，泛互联网活动同样覆盖
- **渠道**：Telegram Bot（App 内置 MTProto 代理，非全天开代理也能收）

**明确不做**（v0.5 移除）：

- ~~日报层~~：分组推送行业大事。信息价值低于福利，且与「只保留福利推送」的定位冲突
- ~~页面 diff 监控~~：对定价页做指纹快照。含轮播/计数器的页面持续误报，维护成本高于收益

## 2. 总体架构

```
┌──────────────────────────────────────────────────────┐
│                GitHub Actions (cron)                 │
│                                                      │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐          │
│  │ 多信源采集 │ → │ 去重/分类 │ → │ Telegram │          │
│  └──────────┘   └────┬─────┘   └────┬─────┘          │
└──────────────────────┼──────────────┼────────────────┘
                       ▼              ▼
                 state/ 快照      Telegram Bot
                （commit 回仓库）
```

- 运行环境：GitHub Actions 定时任务（公开仓库免费不限时），单 job 串行采集
- 信源：radar JSON + RSS/Atom + Telegram 公开频道（见第 3 节）
- 推送渠道：接口抽象（`notify_base.py`），Telegram 为标准实现；企微/邮件为预留实现

### 2.1 运行方式（本地无需开机）

到点自动在临时容器里执行「采集 → 去重 → 分类 → 推送 → state commit 回仓库」，跑完销毁。
本地只在改配置 / 调试时需要开机。费用为 0。

## 3. 信源设计

### 3.1 统一条目模型

所有信源产出同一种 `Item`（`pusher/sources/base.py`），后续过滤/去重/推送只认它：

| 字段 | 说明 |
|---|---|
| `title` / `title_en` | 标题（中/英），参与关键词匹配 |
| `url` / `source` / `source_id` | 原文链接、展示来源名、配置里的信源 id |
| `summary` | 摘要，短摘要参与匹配兜底，超 300 字视为正文不参与 |
| `tier` / `score` | 权威度与热度，仅用于排序 |
| `extra` | `kind`（welfare/opportunity）、`published`、`permalink` 等 |

### 3.2 三种信源类型

| type | 实现 | 覆盖 |
|---|---|---|
| `radar` | `sources/radar.py` | AI News Radar 公开 24h JSON（**不再是唯一主源**，「AI 相关」也不再是推送前置门槛） |
| `rss` | `sources/feed.py` | 社区板块、厂商博客、羊毛站点的 RSS/Atom |
| `telegram` | `sources/telegram.py` | `t.me/s/<channel>` 公开频道网页预览，无需鉴权 |

已接入清单（`config/sources.yml`，2026-10-05 复核）：

- **中文社区**：V2EX 优惠信息（`require_actionable` + `facts_only`）、
  **V2EX 免费赠送节点 feed**（`/feed/free.xml`，`require_actionable`，`max_age_days: 7`）、吾爱破解
- **境外免费版块**：**Reddit 免费版块**（freebies/eFreebies/AppHookup/freegames/GameDealsFree/
  FreeGameFindings/FreeEBOOKS 合并为一个 feed，一次请求，`max_age_days: 3`）
- **Telegram 频道**：Freebies Global、VPS Free
- **境外厂商**：Cloudflare Blog、GitHub Blog
- **AI 资讯**：AI News Radar（`match_summary: false`）
- **已停用并复核**：NodeSeek 与 NodeSeek 福利、吾爱破解 16/41/42（各 20 条 0 命中）、
  hostloc（非 RSS）、HN free tier（SSL 不稳定）
- **已移除**：Linux.do（Actions 实测 403）

### 3.3 实现要点

- **RSS 解析走标准库 `html.parser`**，RSS 2.0 / Atom / RDF 都能吃，不引入 feedparser
- **`text_fallback`**：Discourse 论坛把整篇正文塞进 `description` 的文本节点，
  此时不做 HTML 反转义，否则代码块里的 `<div class="x">` 会被当成标签清掉
- **Atom 链接**：优先 `rel="alternate"`，跳过 `rel="self"`（那是 feed 自身地址）
- **新鲜度**：radar 超过 `max_age_hours` 视为上游故障跳过；RSS 丢弃超过 `max_age_days` 的老条目，
  解析不出时间的保留（宁滥勿缺）
- **单源故障隔离**：任一源超时/403/解析失败只跳过该源，不影响其他源
- **Telegram**：正文含外链时以该外链为条目 URL（点进去是活动页），同时保留消息 permalink 便于溯源；
  不加代理时 Actions 上可能超时，`options.proxy` 可单独指定出口代理

### 3.4 上线前必须探测

信源可用性无法靠猜（各家反爬策略不同），所以提供两层探测：

```bash
python -m pusher.run --probe                  # 逐源报告：OK/FAILED、条数、样例标题+链接
python -m pusher.run --probe --only nodeseek  # 只测一个源
python probe/probe_candidates.py              # 候选信源体检：抓取量 + 命中量双闸门 + 样例
```

Actions 手动触发也支持 `probe=true`（不推送、不写 state）。
**新增信源的标准流程：先用 `probe_candidates.py` 通过双闸门并人工看过样例，再进正常轮次。**
机械闸门不够——`tg-freebie` 满足「抓取 ≥5 且命中 ≥1」，但 12/20 条是加密空投（详见第 10 节）。

## 4. 推送策略

### 4.1 命中判定（两层词表 + 证据闸门）

`config/keywords.yml` 四张表 + 三个证据谓词（`pusher/facts.py`）：

| 表 / 谓词 | 作用 |
|---|---|
| `welfare_keywords` | **硬福利词**，命中即成立（免费额度、赠送、折扣码、兑换券、free credits……） |
| `weak_welfare_keywords` | **宽词**（优惠、折扣、福利、学生、免费使用、student、discount……）：单独出现**不算**福利，必须与「硬福利证据」共现 |
| `opportunity_keywords` / `weak_opportunity_keywords` | 限时窗口；宽词要求有可领取动作或窗口证据 |
| `has_offer_signal` | 硬证据：折扣码/兑换券/免费领取/半价/`N 折`/`% off`/到手价+金额…… |
| `has_claim_signal` | 可领取动作：领取/申请/注册/报名/兑换/邀请码/sign up |
| `has_window_signal` | 窗口：截止/限量/名额/先到先得/最后 N 天/开放注册 |

判定顺序（`WelfareFilter.explain`，只有这一处实现，`classify` 与 `--report` 共用）：

```
排除词 → 公告已作废（领完/送完/过期）→ 硬福利 → 宽福利（需硬证据）
      → 硬限时 → 宽限时（需可领取）→ 派生限时（硬证据 + 窗口）→ 不推
```

- 匹配范围：标题(中/英) + 信号词 + 短摘要（≤ `filter.max_reason_chars`，默认 120 字）。
  **宽词不匹配 `title_en`**：实测英文标题里的 students 会把 AI 行业新闻整条捞进来。
- **证据类判定走「裁剪视图」**（`matching_item`）：`match_summary: false` 或摘要过长时，
  摘要不参与任何证据判定——否则整篇文章会绕过正文门槛蹭中弱词与派生限时。
- **摘要同样过屏蔽层**：此前只屏蔽标题，摘要里的「This giveaway has ended」
  照样能让 giveaway 命中（实测漏网）。
- **否定语境方向敏感**：`将结束…免费`、`不再免费`、`免费使用…终止`、`恢复原价`、
  `优惠码作废`、`giveaway … ended` 先抹掉；但 `限时免费领取，月底结束` 是截止预告，
  不能被误伤（正反例都在语料里）。
- **二手/索要守卫**：`优惠价出一个 X`、`联系 tg @xxx`、`求推荐/收 X`、
  `有大哥能送个会员吗` 全部拒掉；`免费送出 100 个兑换码` 反过来必须放行。
- 同时命中两类时按**福利**处理（信息量更大）。

### 4.2 去重

两把键，任一命中即视为重复：

1. **URL 哈希**：同一条链接永不重复推送
2. **标题归一化哈希**：去掉空白/标点/emoji/`【】`前缀后比对，
   解决多源转载同一活动、标题只差标点的问题

本轮内部也会折叠重复（同一活动被两个源同时抓到只推一条）。
记账在**投递成功后**才写，失败条目下一轮自动重试。

### 4.3 排序与防刷屏

- 排序：先福利后限时；同类内按信源权威度（`tier`）升序，再按热度（`score`）降序
- 单轮上限 `settings.yml → push.max_per_run`（默认 8），可用 `--limit` 覆盖
- 超出的条目**不记账**，下一轮继续推，不会因为封顶而丢失

### 4.4 英文自动翻译

- 范围：将要展示的标题与摘要；以英文为主（CJK 占比 < 25%）才触发
- 降级链：Google gtx → MyMemory → 保留原文；任一失败静默降级，不阻塞推送
- 缓存：`state/translations.json` 按标题哈希缓存，同一标题只译一次

### 4.5 正文保障（用户第一条反馈）

用户反馈原话：「消息内容缺乏摘要，信息量太少」。实测根因有三层，所以修也是三层：

1. **源数据丢了正文**：`fetch_radar.normalize` 只映射 `recommend_reason_zh`，丢掉了上游
   `summary` 与 `published_at`。实测线上 112 条 radar 里 `summary` 非空 23 条、
   `recommend_reason_zh` 非空 75 条，**89 条（79%）两个字段都空**，
   用随附的 `data/latest-24h-all.json` 按 id/url 回捞能补回 **0 条**。
   → 修：映射两个字段（正文优先文章摘要，退回推荐理由）+ 保留发布时间
   （此前 radar 条目的时间**永远不显示**，因为 `extra` 里根本没有 `published`）。
2. **没正文就只剩标题**：新增 `pusher/enrich.py`，只对**将要推送**的条目（每轮 ≤
   `push.max_enrich`，默认 10）抓一次原文页，按 `og:description` → `meta[description]`
   → 首个 `<p>` 取正文，8 秒超时、失败静默降级，缓存写 `state/bodies.json`
   （抓到空也记账，避免每轮重试失败页面）。
3. **没有内容就不该推**：`push.require_body`（默认 true）在渲染后检查
   `content_line_count(message) >= 1`，除标题外没有任何内容可读的条目**不推**，
   日志给出 `[skip:no-body]` 与漏斗计数。

配套渲染改动：

- `facts_only` 体裁（V2EX/社区源）在细节行 ≤1 时补一行 `▎简介：`（首句 ≤100 字）——
  实测「优惠价出一个香港 CSL esim」原本只剩「标题 + 一行价格」。
- 速览行标签收敛：`short_label()` 只保留一段且 ≤8 字，
  「via Hacker News · 24h最热 · 热议参考」这种挤两个标签的噪声不再出现。
- 摘要打分补「怎么领/门槛」权重（领取/注册/申请/兑换/报名/认证/邀请码）。
- 价格抽取修正：带标注的价格允许无单位（`现价 120`），并跳过 `14 元/年` 这类**单价**——
  此前那条 esim 消息显示的价格其实是「14 元/年保号」，真实报价是 120，数字是错的。

### 4.6 漏斗实测数据（2026-10-05，本机 + 线上 state）

线上 `state.json`（GitHub main）显示 2026-10-03 15:14 UTC 之后的 **47.5 小时只推了 3 条**，
与用户截图逐条对应，**3 条全是噪音**：

| 推送时间（BJT） | 内容 | 命中的词 | 真相 |
|---|---|---|---|
| 10-03 23:14 | Gemini 将结束 Flash 和 Pro 模型的免费使用 | `免费使用` | 福利被取消的资讯 |
| 10-04 20:51 | 我让AI教学生写前端……Web教育者的集体反思 | `学生` | 行业资讯，不是福利 |
| 10-05 01:34 | 优惠价出一个香港 CSL esim | `优惠` | 二手转卖 |

一轮完整采集（`--report`）在修复前后的对照：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 抓取 / 去重后 | 236 / 225 | 262 / 249 |
| 命中（福利·限时） | 2（2·0），且**两条都是噪音** | 1（1·0），是**真福利**（Reddit iOS 应用终身赠送） |
| 拒绝原因可见 | 无 | `drop:no-keyword` / `excluded` / `weak-no-action` / `source-not-actionable` / `no-body` / `finished` |
| 【限时】通道 | 历史 0 命中 | 派生规则上线（硬证据 + 窗口），语料里有正例 |

**供给结论（重要）**：现有可自动化获取的信源池里，真福利本身就是稀疏的——
V2EX 优惠信息 50 条里 1~3 条、NodeSeek 福利版与吾爱破解各版块 **0 条**、
Cloudflare/GitHub 博客 0 条、英文促销站（9to5toys 50 条、ghacks 40 条）0 条。
所以「一天只推一条」**不是筛选太严、也不是 30 分钟间隔太长**（48 轮/天），
而是池子里就这么多。想推得更多，只能加信源——见第 3.5 节的体检流程与候选清单。

## 5. 状态管理

- `state/state.json`：已推送键值（URL 哈希 + 标题哈希，滚动 7 天）
- `state/translations.json`：译文缓存
- `state/bodies.json`：原文页正文缓存（键=URL 哈希，抓到空也记，避免重复重试）
- 每次运行后 state 变化 commit 回仓库 —— 仓库持续有提交，规避 GitHub「60 天无活动停用定时任务」
- 老 `state.json` 里的 `digests` 字段（日报记账）读入即丢弃，不再写回

## 6. 项目结构

```
Message Collections/
├── pusher/
│   ├── sources/
│   │   ├── __init__.py      # 信源注册表 SOURCE_TYPES
│   │   ├── base.py          # Item 模型 + 配置装载
│   │   ├── radar.py         # AI News Radar JSON
│   │   ├── feed.py          # RSS / Atom（标准库解析）
│   │   └── telegram.py      # Telegram 公开频道预览
│   ├── filter.py            # 强/弱词表匹配、否定与作废守卫、分类、去重
│   ├── facts.py             # 硬证据/可领取/窗口三谓词 + 事实抽取 + 二手交易守卫
│   ├── enrich.py            # 正文兜底（原文页 og:description / 首段 + 缓存）
│   ├── report.py            # 逐源漏斗报告（--report）
│   ├── state.py             # 去重记账（URL + 标题双键）
│   ├── translate.py         # 英文翻译（降级链 + 缓存）
│   ├── notify_base.py       # 推送渠道抽象
│   ├── notify_telegram.py   # Telegram 实现（主渠道）+ 消息渲染
│   ├── notify_wecom.py      # 企业微信机器人（预留）
│   ├── notify_email.py      # 邮件（预留）
│   └── run.py               # 入口（推送 / --dry-run / --probe / --report）
├── config/
│   ├── sources.yml          # 信源清单
│   ├── keywords.yml         # 硬福利词 / 宽词 / 硬限时 / 宽限时 / 排除词
│   └── settings.yml         # 渠道开关、条数上限、正文闸门、匹配门槛、抓取超时
├── probe/
│   ├── probe_candidates.py  # 信源候选体检（抓取量 + 命中量双闸门）
│   └── preview_samples.py   # 消息样张预览（--send 可人工验收）
├── state/                   # 运行状态（git 管理：state/translations/bodies）
├── tests/                   # pytest（含真实噪音/福利语料回归）
│   └── fixtures/            # noise_corpus.json / offer_corpus.json / radar-sample.json
└── .github/workflows/       # instant-push / tests
```

技术栈：Python 3.11 + requests + PyYAML；无数据库、无服务器，git 即数据库。

## 7. GitHub Actions 调度

| Workflow | Cron (UTC) | 说明 |
|---|---|---|
| instant-push | `13,43 * * * *` | 每小时 :13/:43（**48 轮/天**）；手动触发支持 `probe` / `only` |
| tests | push / PR 触发 | pytest 全绿门禁 |

**教训**：cron 排在整点/半点会被 GitHub 高峰期大幅延迟（实测 `*/30` 退化为约 2 小时一次），
故错峰到 :13/:43。Secrets：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、
（预留）`WEWORK_WEBHOOK_URL`、（可选）`SMTP_*`。

**间隔不是瓶颈**：48 轮/天，而单轮真福利命中量本身是 0~1 条（见 4.6），
所以「推送少」要靠加信源解决，缩短间隔不会有任何变化。

本地/线上排障用同一条命令看漏斗（不推送、不写 state）：

```bash
python -m pusher.run --report              # 逐源：抓取 / 已推过 / 命中 / 未命中原因 / 命中明细
python -m pusher.run --report --only radar # 只看某个源
```

## 8. 消息格式

```
🎁 [福利] AdGuard Family Plan 终身订阅优惠，9 设备永久授权家庭版

▎价格：¥59.20（到手价）
▎折扣码：LIFETIMEO

<摘要：只补细节行没覆盖的内容，剔除评论腔>
via V2EX 优惠信息 · 10-01 21:43
```

`facts_only` 体裁（论坛帖）在细节行太少时补一句「这是什么」，避免只剩标题 + 一行数字：

```
🎁 [福利] 优惠价出一个香港 CSL esim

▎简介：香港 CSL esim, 15GB 中澳台漫游，14 元/年保号，原价 130
▎价格：<b>120</b>（到手价）
via V2EX 优惠信息 · 10-04 21:05
```

限时窗口类情报抬头为 `⏳ [限时]`。两种体裁（信源配 `layout` 决定）：

| 体裁 | 用于 | 结构 |
|---|---|---|
| `bullets`（默认） | 资讯类（radar、博客）、短帖社区源（V2EX 免费赠送） | 标题 → 细节行 → 摘要（剔除评论腔）→ 速览行 |
| `facts_only` | 长流水账社区源（V2EX 优惠信息、NodeSeek） | 标题 → 简介行（细节太少时）→ 细节行 → 速览行，**不倒原始正文** |

`facts_only` 的存在理由：V2EX 优惠信息帖的正文是一整段没有句末标点的流水账（还混着博客签名与网址），
`build_summary` 的拆句对它无效，倒出来既读不下去也压不掉——不如只给要点，原文交给链接。

**正文闸门**（`push.require_body`，默认 true）：渲染后除标题外没有任何内容可读的条目不推。
这是「消息必须有正文」的硬保证——源数据没有摘要时会先由 `enrich.py` 抓原文页兜底，
抓不到就跳过这一条（日志 `[skip:no-body]`，漏斗里有计数）。

### 渲染规则与实测依据

| 环节 | 规则 | 依据 |
|---|---|---|
| 标题 | 合并换行推文/论坛帖、剥 HTML、**去掉源站方括号标记**（`[免费赠送]` 会与我们自己的 `🎁 [福利]` 叠成两套）、去转发腔调 | 用户实测观感 |
| 长标题 | 用信源自带摘要重做标题，而不是硬砍原文 | 376 条里 11% 标题 >140 字，几乎全是 X 推文流水账 |
| 收口 | 优先句末标点，其次逗号，最后才加省略号 | 避免「如果这堂课卖 400 刀，」这种悬空半句 |
| 细节行 | 抽出 `价格/折扣/折扣码/截止/形式` 逐行列出；**抽不到不占行**；摘要里已出现的项不重复 | 用户要求「分行要点式」 |
| 折扣码 | **强制单独成行**，即使摘要里也出现过 | 摘要会被压缩，码混在长段落里很难扫到 |
| 摘要 | 信息密度重排（价格 +10、截止 +8…），剔除「想知道…读这篇」评论腔 | 倒金字塔 |
| 高亮 | 金额、折扣、日期加粗 | 一眼看到关键数字 |
| 预算 | 正文约 350 字，超出按整句压缩，短内容不硬凑 | 用户明确「不刻意达到」 |

### 事实抽取的实测边界（`pusher/facts.py`）

- **折扣码只认带标签的**（`折扣码：LIFETIMEO`、`兑换券码：NWY-CIYUM-4UYZD-40694`）。
  裸匹配会把 `-8259U`（CPU 型号）、`H11SSL-NC`（主板）、`1086110586937`（快递单号）当折扣码。
- **不再抽取「资格/条件」字段**：正则很容易从论坛正文吞下一整段无关文字，
  条件信息通常已在标题或摘要里出现。
- **价格优先取「到手价/实付/现价/券后」**（允许不带单位），并跳过 `14 元/年` 这类**单价**
  ——实测那条 esim 消息把「14 元/年保号」当成了售价，真实报价 120 元。

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 新信源抓不到（403 / 反爬 / 改版） | `--probe` 上线前逐源验证；单源故障隔离；`enabled: false` 快速下线 |
| 中文社区对境外 IP 限流（Actions 在海外） | 优先选 RSS 出口；必要时给该源配 `proxy`；probe 日志留证 |
| Telegram 预览页在 Actions 上偶发超时 | 单源隔离 + 下轮重试；必要时走 `options.proxy` |
| 关键词误报 | 强/弱词双层 + 否定语境 + 可领取/窗口证据 + **真实语料回归**（`tests/fixtures/*_corpus.json`） |
| 多源刷屏 | 标题归一化去重 + 单轮条数上限 |
| 空壳消息（只有标题） | `enrich.py` 抓原文页兜底 + `push.require_body` 闸门 + 漏斗里的 `[skip:no-body]` |
| radar 上游故障 | 新鲜度检查 + 跳过该轮；radar 已是普通信源，失效不影响其他源 |
| Google 翻译 429 | MyMemory 降级 + 缓存；全败回退原文 |
| Reddit 对同一出口限流（实测连续请求即 429） | 合并多板为**一次**请求；线上每轮只请求一次；失败按单源隔离跳过，`--report` 里可见 |
| 繁体中文源（台/港）match 不到词表 | **已知缺口**：实测 free.com.tw 10 条 0 命中（免費/優惠 是繁体）。需要时加一层繁→简归一化（约 40 字表），本次未做，避免为不确定收益引入新的匹配层 |
| 加密空投刷屏类信源 | 排除词 `airdrop/空投/usdt/crypto` + 候选体检时人工看样例（tg-freebie 机械上「通过」但内容全是空投，已拒用） |

## 10. 信源变动与候选体检（2026-10-05）

`probe/probe_candidates.py` 把「先 probe 再启用」变成一条命令，双闸门是
**抓取 ≥5 条**且**命中 ≥1 条**，同时打印样例标题供人工判断内容质量：

```bash
python probe/probe_candidates.py                     # 体检内置候选清单
python probe/probe_candidates.py --only tg-freebie   # 只看一个
python probe/probe_candidates.py --feed https://example.com/feed   # 临时加一个
```

本轮结论（写进 `config/sources.yml` 注释，避免重复踩坑）：

| 动作 | 信源 | 依据 |
|---|---|---|
| **启用** | V2EX 免费赠送（`/feed/free.xml`，**节点 feed**） | tab feed 返回 0 字节是老结论；节点 feed 50 条里 31 条命中真赠送帖。低频：近 7 天 2 条、近 30 天 16 条，故 `max_age_days: 7` |
| **启用** | Reddit 免费版块（多板合并 feed） | 25 条里 2 条真福利（iOS 应用终身赠送、每周免费有声书）；限流风险已记录 |
| 保持停用 | NodeSeek / NodeSeek 福利 / 吾爱破解 16·41·42 | 各 20 条 0 命中（二手交易、工具分享） |
| 保持停用 | hostloc / hn-free-tier / 52pojie freeshare | 非 RSS / SSL 不稳 / feed 为空（老结论复核仍成立） |
| 拒绝启用 | TG `freebie`、`vpsdeals`、`freebies` | freebie 20 条里 12 条是加密空投刷屏；vpsdeals 0 命中；freebies 只 1 条抽奖 |
| 空壳 | TG `giveaway`/`freenet`/`letsdeel`/`dealsfreenet`/`cnfreebies`/`maoyangmao` 等 20+ 个候选 | 0 条消息块，名字靠猜必踩 |
| 本地不可达 | 中文线报站 `xianbao.net`/`51xianbao`/`luymba`/`ymba` | 本机 SSL/连接被断（GFW 侧），Actions（海外）可能可用——**待线上 probe 复核** |
| 无收益 | `sspai`/小众软件/`free.com.tw`/`9to5toys`/`ghacks`/`producthunt`/`slickdeals`/`post.smzdm.com` | 命中 0~1 条且是文章类噪音 |

## 11. 验证记录

DSH 沙箱默认策略下 shell 起不来（`SetNamedSecurityInfoW failed (Win32 5)` 发生在沙箱准备阶段，
与仓库权限无关；本机文件权限已按沙箱自带的诊断脚本修复过一次），
因此**所有 shell 操作（pytest / git / 联网探测）都在一次性放行模式下执行**，
这也是本仓库的既有做法。文件读写工具不受影响。

### v0.6（2026-10-05）

| 验证项 | 结果 |
|---|---|
| `python -m pytest -q` | **164 passed**（v0.5 记录是 73，本版新增 91 条） |
| 真实语料回归 | 噪音语料 **13/13 不推**；福利语料 **10/10 命中且 kind 正确** |
| `python -m pusher.run --report`（真实网络） | 262 抓取 / 249 去重后 / **命中 1（真福利）** / 每条拒绝原因可见 |
| `python -m pusher.run --dry-run` | 0 条空壳消息（正文闸门生效）；不再因 GBK 崩溃 |
| 线上 3 条历史噪音 | 全部被拦下：`freebie 将结束…` → 否定语境；`学生…反思` → 弱词无硬证据；`esim` → 二手交易守卫 |
| 信源候选体检 | 探测 40+ 候选，新增 2 个真产出源（V2EX 免费赠送节点、Reddit 免费版块） |

### v0.5（2026-10-03，历史记录）

| 验证项 | 结果 |
|---|---|
| `python -m pytest -q` | 73 passed |
| `python -m pusher.run --probe`（仅启用源） | 9/9 可用 |
| 提交 | 已 rebase 到 Actions 的 state 提交之上并推送 main |

信源实测明细（2026-10-03，本机 + GitHub Actions 双向验证）：

| 信源 | 本机 | Actions | 结论 |
|---|---|---|---|
| radar | ✅ 201 条 | — | 启用（上游健康） |
| nodeseek | ✅ 20 条 | 待首批日志 | 启用 |
| nodeseek-free | ✅ 20 条 | 待首批日志 | 启用（新增） |
| v2ex-deals | ✅ 49 条 | 待首批日志 | 启用 |
| 52pojie | ✅ 7 条 | 待首批日志 | 启用 |
| tg-freebies | ✅ 20 条 | 待首批日志 | 启用 |
| tg-vpsfree | ✅ 22 条 | 待首批日志 | 启用（新增，替代空壳的 yangmaoshe） |
| cloudflare-blog | ✅ 20 条 | 待首批日志 | 启用 |
| github-blog | ✅ 5 条 | 待首批日志 | 启用 |
| linux.do（两个源） | ❌ 连接超时 | ❌ **403 Forbidden** | **移除**：对数据中心 IP 封锁 |
| v2ex-free | ⚠️ 0 字节 | ⚠️ 0 条 | 停用：feed 无效（**v0.6 已改用节点 feed 恢复**） |
| hostloc | ⚠️ 返回 HTML | ⚠️ 0 条 | 停用：非 RSS |
| hn-free-tier | ❌ SSL 断连 | ⚠️ 0 条 | 停用：不稳定且长期无命中 |
| tg-yangmaoshe / zaihua / yanggou / baipiao | ❌ 空壳 | — | 已删/未启用 |

**教训**：
1. 中文社区站对 Actions 的数据中心 IP 不一定友好（linux.do 直接 403），
   **信源必须先 probe 再上线**，这条流程已写进 `config/sources.yml` 的注释。
2. 频道名不能靠记忆猜（`yangmaoshe` 是空壳），Telegram 源必须探测消息块数量。
3. 论坛类 RSS 常按版块给 feed，版块号要逐个试（52pojie 只有 2/16/41/42 有内容）。
4. **同一个站点的不同 feed 差别极大**：V2EX `/feed/tab/free.xml` 是 0 字节，
   而 `/feed/free.xml`（节点 feed）有 50 条真赠送帖。停用一个源之前要试过它的其它出口。
5. **机械闸门挡不住内容垃圾**：`tg-freebie` 的「抓取 ≥5 且命中 ≥1」是满足的，
   但 20 条里 12 条是加密空投。体检工具必须打印样例标题给人看。

```bash
# 本地复现验证
python -m pytest -q
python -m pusher.run --probe                 # 逐源体检（不推送、不写 state）
python -m pusher.run --report                # 逐源漏斗 + 未命中原因
python -m pusher.run --dry-run               # 只看消息样式（含正文兜底与闸门）
python probe/probe_candidates.py             # 候选信源体检（上线前必做）
```

## 12. 仓库与协作

- 仓库：[Djjvcgh/message-collections](https://github.com/Djjvcgh/message-collections)（public，main 分支）
- 遵循 AGENTS.md：每次改动单独 commit，pytest 全绿再交付
- 运行记录：Actions 页可查每次推送日志；手动触发 `probe=true` 可随时体检信源
