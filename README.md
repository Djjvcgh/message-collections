# AI 情报推送器

自动把 AI 界的两类信息推送到 Telegram：

- 🎁 **福利活动**（送 token、学生优惠、限时免费……）—— 关键词命中即时推送
- 🚀 **行业大事**（模型发布、产品更新……）—— 每日早晚各一条分组日报

完全运行在 GitHub Actions 上，零服务器、零 API Key、零费用。

## 架构

```
GitHub Actions (cron 每30分钟)
  → 读取 AI News Radar 公开数据 (news.learnprompt.pro)
  → 福利关键词过滤 + URL 去重 (state/state.json 随仓库持久化)
  → Telegram Bot 推送
```

推送渠道是可插拔接口（`pusher/notify_base.py`），已内置
Telegram（主渠道）、企业微信群机器人（预留）、邮件（可选）。

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
```

本地发真实消息：仓库根目录建 `.env`（已被 gitignore）：

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_PROXY=http://127.0.0.1:7897   # 本地无代理可不填
```

## 配置

| 文件 | 内容 |
|---|---|
| `config/keywords.yml` | 福利词表 / 排除词 |
| `config/settings.yml` | 渠道开关、日报各组条数 |
| `config/sources.yml` | 数据源地址、页面监控清单（M2） |

详细设计见 [DESIGN.md](DESIGN.md)。
