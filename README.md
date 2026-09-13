# AI 情报推送器

自动把 AI 界的两类信息推送到 Telegram：

- 🎁 **福利活动**（送 token、学生优惠、限时免费……）—— 关键词命中即时推送
- 🚀 **行业大事**（模型发布、产品更新……）—— 每日 09:23 / 21:23（北京时间）分组日报

完全运行在 GitHub Actions 上，零服务器、零 API Key、零费用。

## 架构

```
GitHub Actions (cron 错峰调度)
  → 读取 AI News Radar 公开数据 (news.learnprompt.pro)
  → 福利关键词过滤 + URL 去重 (state/ 随仓库持久化)
  → Telegram Bot 推送
```

- **即时层**：每小时 :13 / :43 运行，福利词命中即推（同 URL 永不重复）
- **日报层**：每天 09:23 / 21:23，每组各发一条消息
- **页面监控**：DeepSeek/OpenAI/BigModel/Z.ai 定价页 diff，内容变化即推（M2）

## 消息样式

日报按组分四条消息（🎁 福利速递 / 🚀 模型发布 / 🧰 产品与工具 / 💬 值得注意），条目三段式、空行分隔：

> 1. **新闻标题**
> ▎完整中文摘要（英文自动翻译）
> via 来源 · 层级　← 来源名即可点击的原文链接

- **英文自动翻译**：Google gtx → MyMemory → 保留原文，三级降级；译文按标题缓存，只译一次
- **标签页脚**：消息末尾带 `#AI日报 #组名 #信号词`，可在 Telegram 内点击筛选历史消息
- **按组去重**：同一 URL 永不重复推送；日报按「日期 + 槽位 + 组」记账，某组失败下轮只补发该组

推送渠道是可插拔接口（`pusher/notify_base.py`），已内置 Telegram（主渠道）、企业微信群机器人（预留）、邮件（可选）。

## 部署

1. Fork / 使用本仓库（公开仓库，Actions 免费）
2. 找 @BotFather 创建机器人，拿 token
3. 给机器人发一条消息（拿 chat_id），Settings → Secrets 配置：
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. 到 Actions 页启用 workflow

## 本地调试

```bash
pip install -r requirements.txt pytest
python -m pytest -q                        # 跑测试
python -m pusher.run instant --dry-run     # 只打印不发送
python -m pusher.run digest --dry-run
```

本地发真实消息：仓库根目录建 `.env`（已被 gitignore）：

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_PROXY=http://127.0.0.1:7897   # 本地无代理可不填；翻译接口同样复用
```

## 配置

| 文件 | 内容 |
|---|---|
| `config/keywords.yml` | 福利词表 / 排除词 |
| `config/settings.yml` | 渠道开关、日报各组条数 |
| `config/sources.yml` | 数据源地址、页面监控清单 |

详细设计见 [DESIGN.md](DESIGN.md)。
