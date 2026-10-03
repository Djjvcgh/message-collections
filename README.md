# AI 情报推送器

自动把「现在能领、错过就没」的信息推送到 Telegram：

- 🎁 **福利**（送 token、免费额度、学生优惠、免费域名、白嫖服务器……）
- ⏳ **限时窗口**（开放注册、限量名额、领取截止……）

多信源聚合（RSS / Telegram 公开频道 / AI News Radar），完全运行在 GitHub Actions 上，
零服务器、零 API Key、零费用。

## 架构

```
GitHub Actions (cron 错峰调度)
  → 采集：radar JSON + RSS/Atom 多源 + Telegram 公开频道网页预览
  → 关键词分类：福利词表 / 限时词表 + 排除词
  → 去重：URL 哈希 + 标题归一化哈希（state/ 随仓库持久化）
  → Telegram Bot 推送
```

- **单层即时推送**：每小时 :13 / :43 运行，命中即推，不设日报
- **条数上限**：单轮最多 8 条（`config/settings.yml`），超出的条目下一轮继续，不会丢
- **失败重试**：投递失败的条目不记账，下一轮自动重推

## 消息样式

```
🎁 [福利] AdGuard Family Plan 终身订阅优惠，9 设备永久授权家庭版

▎价格：¥59.20（到手价）
▎折扣码：LIFETIMEO

via V2EX 优惠信息 · 10-01 21:43
```

- **标题完整**：去掉源站的 `[免费赠送]` 这类标记与「我试用了…」转发腔调；
  标题过长或残缺时用信源摘要重做标题，而不是硬砍
- **细节行**：价格、折扣、折扣码、截止日期逐行列出，抽不到就不占行；
  折扣码强制单独成行，便于一眼扫到
- **摘要**：只补细节行没覆盖的内容，剔除「想知道…读这篇」这类评论腔
- **两种体裁**：资讯类用「细节行 + 摘要」；论坛/社区类（V2EX）用 `layout: facts_only`，
  只给要点、不倒流水账正文
- **预算约 350 字**：超出按整句压缩（不切半句），内容本来就短的不硬凑

限时窗口类情报抬头换成 `⏳ [限时]`。

## 信源

全部在 `config/sources.yml` 里增删，代码不用改。三种 type：

| type | 说明 | 主要选项 |
|---|---|---|
| `radar` | AI News Radar 公开 JSON（AI 圈福利，已降为普通信源之一） | `url` `max_age_hours` |
| `rss` | RSS / Atom 订阅（社区板块、厂商博客、羊毛站） | `url` `name` `text_fallback` `max_age_days` |
| `telegram` | Telegram 公开频道网页预览（无需鉴权） | `channel` `name` `proxy` |

已内置**实测可用**的信源（9 个启用）：NodeSeek（综合 + 福利）、V2EX 优惠信息、吾爱破解、
Telegram `freebiesglobal` / `vpsfree`、Cloudflare Blog、GitHub Blog、AI News Radar。

已停用但保留在清单里便于复核：V2EX 免费赠送（feed 为空）、全球主机交流（返回非 RSS）、
HN free tier（SSL 不稳定）。**Linux.do 已整段移除**：它在 GitHub Actions 上返回 403
（对数据中心 IP 封锁），本机也不可达。

> 教训已写进 `config/sources.yml` 注释：**新信源必须先探测通过再启用**；
> 频道名不能靠记忆猜（`yangmaoshe` 实测是空壳）；论坛 RSS 的版块号要逐个试。

**先探测再上线**（本地或 Actions 手动触发都能跑，不推送、不写 state）：

```bash
python -m pusher.run --probe                  # 逐源报告可用性与样例条目
python -m pusher.run --probe --only nodeseek  # 只看单个源
```

## 部署

1. Fork / 使用本仓库（公开仓库，Actions 免费）
2. 找 @BotFather 创建机器人，拿 token
3. 给机器人发一条消息（拿 chat_id），Settings → Secrets 配置：
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. 到 Actions 页启用 workflow（手动触发时可选 `probe=true` 先测信源）

## 本地调试

```bash
pip install -r requirements.txt pytest
python -m pytest -q
python -m pusher.run --dry-run          # 只打印不发送
python -m pusher.run --probe            # 检查每个信源能否抓到
```

本地发真实消息：仓库根目录建 `.env`（已被 gitignore）：

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_PROXY=http://127.0.0.1:7897   # 本地无代理可不填；翻译接口与部分信源同样复用
```

## 配置

| 文件 | 内容 |
|---|---|
| `config/keywords.yml` | 福利词表 / 限时词表 / 排除词 |
| `config/settings.yml` | 渠道开关、单轮推送条数上限 |
| `config/sources.yml` | 信源清单（所有渠道都在这里增删） |

详细设计见 [DESIGN.md](DESIGN.md)。
