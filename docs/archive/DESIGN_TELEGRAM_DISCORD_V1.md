# Telegram/Discord Native Notification v1 Design Doc

> 日期：2026-08-21  
> 状态：DRAFT，等待用户确认后进入施工

---

## 一、目标

在现有 5 个内置通知渠道（Console/Webhook/Slack/Lark/DingTalk）基础上，新增：
- **Telegram Bot** — 通过 Telegram Bot API 发送消息
- **Discord Webhook** — 通过 Discord Webhook 发送消息

**非目标（v1 不实现）**
- Telegram 用户会话/对话管理
- Discord Bot 交互（slash commands）
- 富媒体/附件/Embed 复杂格式
- 消息编辑/删除/回调查询

---

## 二、范围与边界

### 会改的

| 模块 | 改动 |
|------|------|
| `src/web_watcher/channel_senders.py` | 新增 `TelegramSender`、`DiscordSender` |
| `src/web_watcher/cli.py` | `notify` / `digest` 子命令增加 `--channel telegram` / `discord` 及相关参数 |
| `tests/test_channel_senders.py` | **新增** Telegram/Discord sender 测试 |
| `README.md` | 补充 Telegram/Discord 使用说明 |

### 不会改的

- `scheduled_runner.py` — core pipeline 不变
- `notify` dispatch 逻辑 — 仅增加新 sender 选项
- `repository.py` — 不新增表
- DB schema — 不变

---

## 三、Telegram Bot 设计

### 3.1 依赖

- `requests`（已存在）或 `httpx`（如已安装）
- 无额外依赖

### 3.2 TelegramSender

```python
class TelegramSender(BaseChannelSender):
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def send(self, content: str, title: str = "") -> bool:
        text = f"*{title}*\n{content}" if title else content
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        resp = requests.post(self.api_url, json=payload, timeout=10)
        return resp.status_code == 200 and resp.json().get("ok")
```

### 3.3 CLI 参数

```bash
# notify 子命令
python -m web_watcher.cli notify \
  --channel telegram \
  --telegram-bot-token <BOT_TOKEN> \
  --telegram-chat-id <CHAT_ID>

# digest 子命令
python -m web_watcher.cli digest daily \
  --channel telegram \
  --telegram-bot-token <BOT_TOKEN> \
  --telegram-chat-id <CHAT_ID>
```

新增参数：
- `--telegram-bot-token` — Telegram Bot Token（必需）
- `--telegram-chat-id` — Telegram Chat ID（必需）

---

## 四、Discord Webhook 设计

### 4.1 依赖

- `requests`（已存在）

### 4.2 DiscordSender

```python
class DiscordSender(BaseChannelSender):
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, content: str, title: str = "") -> bool:
        # Discord 限制：content 最多 2000 字符
        # 使用 embed 格式提升可读性
        payload = {
            "embeds": [
                {
                    "title": title or "Web-Watcher",
                    "description": content[:4000],
                    "color": 5814783,  # 绿色
                }
            ]
        }
        resp = requests.post(self.webhook_url, json=payload, timeout=10)
        return resp.status_code in (200, 204)
```

### 4.3 CLI 参数

```bash
# notify 子命令
python -m web_watcher.cli notify \
  --channel discord \
  --webhook-url <DISCORD_WEBHOOK_URL>

# digest 子命令
python -m web_watcher.cli digest daily \
  --channel discord \
  --webhook-url <DISCORD_WEBHOOK_URL>
```

复用现有 `--webhook-url` 参数，通过 `--channel discord` 区分。

---

## 五、与现有架构集成

### 5.1 channel_senders 注册

在 `src/web_watcher/channel_senders.py` 中：

```python
def resolve_sender(channel: str, **kwargs):
    if channel == "telegram":
        return TelegramSender(
            bot_token=kwargs["telegram_bot_token"],
            chat_id=kwargs["telegram_chat_id"],
        )
    if channel == "discord":
        return DiscordSender(webhook_url=kwargs["webhook_url"])
    # ... 现有逻辑
```

### 5.2 notify 子命令参数

在 `cli.py` 的 `notify_parser` 中新增：

```python
notify_parser.add_argument(
    "--telegram-bot-token",
    dest="telegram_bot_token",
    default=None,
    help="Telegram Bot Token (for telegram channel)",
)
notify_parser.add_argument(
    "--telegram-chat-id",
    dest="telegram_chat_id",
    default=None,
    help="Telegram Chat ID (for telegram channel)",
)
```

### 5.3 digest 子命令参数

在 `cli.py` 的 `digest_parser` 中新增：

```python
digest_parser.add_argument(
    "--telegram-bot-token",
    dest="telegram_bot_token",
    default=None,
    help="Telegram Bot Token (for telegram channel)",
)
digest_parser.add_argument(
    "--telegram-chat-id",
    dest="telegram_chat_id",
    default=None,
    help="Telegram Chat ID (for telegram channel)",
)
```

---

## 六、验收标准

1. `notify --channel telegram --telegram-bot-token XXX --telegram-chat-id YYY` 成功发送消息
2. `notify --channel discord --webhook-url XXX` 成功发送消息
3. `digest daily --channel telegram ...` 成功发送 digest
4. `digest daily --channel discord ...` 成功发送 digest
5. 错误处理：无效 token / webhook 返回明确错误信息
6. 网络异常时返回 False，不抛异常
7. 不修改 core pipeline / DB schema
8. 全量测试回归 **1525+ passed**

---

## 七、测试计划

| 测试 | 场景 |
|------|------|
| `test_telegram_sender_success` | 成功发送消息 |
| `test_telegram_sender_invalid_token` | 无效 token 返回 False |
| `test_telegram_sender_network_error` | 网络异常返回 False |
| `test_discord_sender_success` | 成功发送消息 |
| `test_discord_sender_invalid_webhook` | 无效 webhook 返回 False |
| `test_discord_sender_content_truncation` | 超长内容截断 |
| `test_notify_with_telegram_channel` | CLI notify 集成 |
| `test_digest_with_discord_channel` | CLI digest 集成 |

---

## 八、后续扩展（不在 v1）

- Telegram 富媒体/附件/Inline Keyboard
- Discord Embed 复杂格式（字段/图片/颜色）
- 消息状态追踪/重试
- 频道/群组自动发现

---

## 九、确认项

请确认以下设计约束：
1. v1 仅基础文本消息，无富媒体
2. Telegram 依赖 Bot Token + Chat ID
3. Discord 复用现有 `--webhook-url`，通过 `--channel discord` 区分
4. 错误处理：返回 False + log，不抛异常
5. 不修改 DB schema / core pipeline
