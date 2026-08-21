# Digest v1 Design Doc

> 日期：2026-08-21  
> 状态：DRAFT，等待用户确认后进入施工

---

## 一、目标

在现有 Web-Watcher 的「实时推送」之外，增加**非实时汇总**能力：
- 过去 24 小时 / 7 天的变化，按 target 汇总为一份报告
- 高优先级（critical）仍保持实时推送，不进入 digest
- 低/中优先级（important / interesting）进入 digest 缓冲

**非目标（v1 不实现）**
- AI 自动摘要
- 定时自动 digest（v1 仅手动触发）
- digest 持久化（不存 digest 记录）
- Web UI 集成（独立 Phase）

---

## 二、范围与边界

### 会改的

| 模块 | 改动 |
|------|------|
| `src/web_watcher/digest.py` | **新增** `DigestBuilder` + `DigestReport` |
| `src/web_watcher/cli.py` | 新增 `digest` 子命令 |
| `tests/test_digest.py` | **新增** 5-8 个测试 |

### 不会改的

- `scheduled_runner.py` — core pipeline 不变
- `notify` 子命令 — dispatch 逻辑不变
- `repository.py` — 复用现有 `list_events` / `get_event_signals`
- DB schema — 不新增表

---

## 三、CLI 设计

```bash
# 日报（默认过去 24h）
python -m web_watcher.cli digest daily \
  --channel email \
  --to user@example.com

# 周报（过去 7d）
python -m web_watcher.cli digest weekly \
  --webhook-url https://example.com/digest

# 自定义时间窗口
python -m web_watcher.cli digest \
  --since 2026-08-20T00:00:00Z \
  --until 2026-08-21T00:00:00Z \
  --channel console

# 仅汇总高优先级以上
python -m web_watcher.cli digest daily \
  --min-importance important \
  --channel email \
  --to user@example.com
```

参数：
- `daily` / `weekly` — 预设时间窗口（互斥）
- `--since` / `--until` — 自定义 ISO 时间戳（与 preset 互斥）
- `--channel` — 派发渠道（console / webhook / email / slack / lark / dingtalk）
- `--to` / `--webhook-url` / 其他渠道参数 — 复用现有 notify 参数
- `--min-importance` — 仅汇总 ≥ 该优先级的事件（default: interesting）

---

## 四、核心逻辑

### 4.1 DigestBuilder

```python
class DigestBuilder:
    def build(self, since: datetime, until: datetime, min_importance: Importance) -> DigestReport:
        # 1. 查询事件
        events = repo.list_events(since=since, until=until, ...)
        # 2. 过滤重要性
        # 3. 按 target 分组
        # 4. 对每个 target 的最新 signal 生成摘要
        # 5. 返回 DigestReport
```

### 4.2 DigestReport

```python
@dataclass
class DigestReport:
    time_range: Tuple[datetime, datetime]
    total_events: int
    by_target: Dict[str, List[Dict]]
    by_importance: Dict[str, int]
    summary: str  # 纯文本摘要
    markdown: str  # Markdown 格式报告
```

### 4.3 摘要生成规则

- **有 Signal 的事件**：取 Signal.value 中的 `extracted_values` 或 `diffs` 生成一句话摘要
- **无 Signal 的事件**：显示事件类型 + target_id
- **按 target 分组**：每个 target 下列出事件数量、最新变化、重要性分布
- **优先级排序**：critical > important > interesting

---

## 五、派发逻辑

Digest 不经过 `notify --once`，而是直接调用 `channel_senders`：

```python
sender = resolve_sender(channel, config)
sender.send(markdown_report)
```

**不创建 Notification 记录**，因为 digest 是只读汇总，不是事件生命周期的一部分。

---

## 六、验收标准

1. `digest daily` 输出过去 24h 的 Markdown 报告
2. `digest weekly` 输出过去 7d 的 Markdown 报告
3. `--since` / `--until` 覆盖自定义窗口
4. `--min-importance` 正确过滤
5. 无事件时输出「无变化」空报告
6. 通过 `--channel email` 成功发送
7. 不修改 existing events / notifications / signals
8. 全量测试回归 **1518 passed**

---

## 七、测试计划

| 测试 | 场景 |
|------|------|
| `test_build_daily_empty` | 24h 内无事件 |
| `test_build_daily_with_events` | 正常汇总 |
| `test_build_weekly` | 7d 窗口 |
| `test_custom_since_until` | 自定义时间 |
| `test_min_importance_filter` | 重要性过滤 |
| `test_report_markdown_format` | Markdown 格式校验 |
| `test_dispatch_email` | Email 派发 |
| `test_dispatch_webhook` | Webhook 派发 |

---

## 八、后续扩展（不在 v1）

- `digest --auto` — 定时自动 digest（需 cron / scheduled_runner 集成）
- `digest --ai-summary` — AI 生成自然语言摘要
- Web UI 展示 digest 历史
- digest 持久化（存库）

---

## 九、确认项

请确认以下设计约束：
1. v1 仅手动触发，不自动调度
2. digest 不经过 notify pipeline，直接调用 channel_senders
3. 不修改 DB schema
4. 不修改 core pipeline（scheduled_runner / notify）
5. 优先级过滤：`--min-importance` 默认 `interesting`
