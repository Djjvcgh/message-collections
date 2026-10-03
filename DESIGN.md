# 情报推送器 · 设计方案

> 版本 v0.5 · 2026-10-03
> 本版为**减法 + 拓源**：删掉日报层与页面 diff 监控，只保留福利/限时情报推送，
> 并把单一主源改造为多信源聚合。
> 实施状态：代码已改完，**待本地验证与提交**（见第 10 节「验证与提交」）。

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

已接入清单（`config/sources.yml`）：

- **中文社区**：V2EX 优惠信息（`require_actionable` + `facts_only`）、吾爱破解
- **Telegram 频道**：Freebies Global、VPS Free
- **境外**：Cloudflare Blog、GitHub Blog
- **AI 资讯**：AI News Radar（`match_summary: false`）
- **已停用**：NodeSeek（实测以二手交易与闲聊为主）、v2ex-free（feed 为空）、
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

信源可用性无法靠猜（各家反爬策略不同），所以提供探测模式：

```bash
python -m pusher.run --probe                  # 逐源报告：OK/FAILED、条数、样例标题+链接
python -m pusher.run --probe --only nodeseek  # 只测一个源
```

Actions 手动触发也支持 `probe=true`（不推送、不写 state）。
**新增信源的标准流程：先 probe 通过，再进正常轮次。**

## 4. 推送策略

### 4.1 命中判定（两层词表）

- `config/keywords.yml` 里 `welfare_keywords` 与 `opportunity_keywords` 两张词表
- 中文词在去空白文本上做子串匹配（`送 token` ≡ `送token`）
- 英文词用词边界匹配并容忍复数（`student` 命中 `students`，但不命中 `industrial`）
- 匹配范围：标题(中/英) + 信号词 + 短摘要；超过 300 字的摘要视为正文，不参与匹配
- `exclude_words` 优先级最高，命中即整条丢弃
- 同时命中两类词表时按**福利**处理（信息量更大）

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

## 5. 状态管理

- `state/state.json`：已推送键值（URL 哈希 + 标题哈希，滚动 7 天）
- `state/translations.json`：译文缓存
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
│   ├── filter.py            # 福利/限时词表匹配、分类、去重
│   ├── state.py             # 去重记账（URL + 标题双键）
│   ├── translate.py         # 英文翻译（降级链 + 缓存）
│   ├── notify_base.py       # 推送渠道抽象
│   ├── notify_telegram.py   # Telegram 实现（主渠道）
│   ├── notify_wecom.py      # 企业微信机器人（预留）
│   ├── notify_email.py      # 邮件（预留）
│   └── run.py               # 入口（推送 / --dry-run / --probe）
├── config/
│   ├── sources.yml          # 信源清单
│   ├── keywords.yml         # 福利词表 / 限时词表 / 排除词
│   └── settings.yml         # 渠道开关、单轮条数上限
├── state/                   # 运行状态（git 管理）
├── tests/                   # pytest
└── .github/workflows/       # instant-push / tests
```

技术栈：Python 3.11 + requests + PyYAML；无数据库、无服务器，git 即数据库。

## 7. GitHub Actions 调度

| Workflow | Cron (UTC) | 说明 |
|---|---|---|
| instant-push | `13,43 * * * *` | 每小时 :13/:43；手动触发支持 `probe` / `only` |
| tests | push / PR 触发 | pytest 全绿门禁 |

**教训**：cron 排在整点/半点会被 GitHub 高峰期大幅延迟（实测 `*/30` 退化为约 2 小时一次），
故错峰到 :13/:43。Secrets：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、
（预留）`WEWORK_WEBHOOK_URL`、（可选）`SMTP_*`。

## 8. 消息格式

```
🎁 [福利] AdGuard Family Plan 终身订阅优惠，9 设备永久授权家庭版

▎价格：¥59.20（到手价）
▎折扣码：LIFETIMEO

via V2EX 优惠信息 · 10-01 21:43
```

限时窗口类情报抬头为 `⏳ [限时]`。两种体裁（信源配 `layout` 决定）：

| 体裁 | 用于 | 结构 |
|---|---|---|
| `bullets`（默认） | 资讯类（radar、博客） | 标题 → 细节行 → 摘要（剔除评论腔）→ 速览行 |
| `facts_only` | 论坛/社区类（V2EX、NodeSeek） | 标题 → 细节行 → 速览行，**不倒原始正文** |

`facts_only` 的存在理由：V2EX 帖子正文是一整段没有句末标点的流水账（还混着博客签名与网址），
`build_summary` 的拆句对它无效，倒出来既读不下去也压不掉——不如只给要点，原文交给链接。

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
- 价格优先取「到手价/实付/券后」，它比原价有用。

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 新信源抓不到（403 / 反爬 / 改版） | `--probe` 上线前逐源验证；单源故障隔离；`enabled: false` 快速下线 |
| 中文社区对境外 IP 限流（Actions 在海外） | 优先选 RSS 出口；必要时给该源配 `proxy`；probe 日志留证 |
| Telegram 预览页在 Actions 上偶发超时 | 单源隔离 + 下轮重试；必要时走 `options.proxy` |
| 关键词误报 | 排除词 + 长摘要不参与匹配 + 观察期调优 |
| 多源刷屏 | 标题归一化去重 + 单轮条数上限 |
| radar 上游故障 | 新鲜度检查 + 跳过该轮；radar 已是普通信源，失效不影响其他源 |
| Google 翻译 429 | MyMemory 降级 + 缓存；全败回退原文 |

## 10. 验证记录

DSH 沙箱默认策略无法启动 shell（`SetNamedSecurityInfoW failed (Win32 5)` 发生在准备阶段，
与仓库权限无关），本次以一次性放行模式执行全部验证：

| 验证项 | 结果 |
|---|---|
| `python -m pytest -q` | **73 passed** |
| `python -m pusher.run --probe`（仅启用源） | **9/9 可用** |
| 已下线文件删除 | 已 `git rm`，工作区干净 |
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
| v2ex-free | ⚠️ 0 字节 | ⚠️ 0 条 | 停用：feed 无效 |
| hostloc | ⚠️ 返回 HTML | ⚠️ 0 条 | 停用：非 RSS |
| hn-free-tier | ❌ SSL 断连 | ⚠️ 0 条 | 停用：不稳定且长期无命中 |
| tg-yangmaoshe / zaihua / yanggou / baipiao | ❌ 空壳 | — | 已删/未启用 |

**教训**：
1. 中文社区站对 Actions 的数据中心 IP 不一定友好（linux.do 直接 403），
   **信源必须先 probe 再上线**，这条流程已写进 `config/sources.yml` 的注释。
2. 频道名不能靠记忆猜（`yangmaoshe` 是空壳），Telegram 源必须探测消息块数量。
3. 论坛类 RSS 常按版块给 feed，版块号要逐个试（52pojie 只有 2/16/41/42 有内容）。

```bash
# 本地复现验证
python -m pytest -q
python -m pusher.run --probe                 # 逐源体检（不推送、不写 state）
python -m pusher.run --dry-run               # 只看消息样式
```

## 11. 仓库与协作

- 仓库：[Djjvcgh/message-collections](https://github.com/Djjvcgh/message-collections)（public，main 分支）
- 遵循 AGENTS.md：每次改动单独 commit，pytest 全绿再交付
- 运行记录：Actions 页可查每次推送日志；手动触发 `probe=true` 可随时体检信源
