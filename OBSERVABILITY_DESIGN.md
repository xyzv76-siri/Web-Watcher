# Observability v1 — Design (frozen)

## 1. 问题

规则和通知目前只能通过文件 + DB 原始查询管理，缺少可观测性：
- 无法列出所有规则及其状态
- 无法查看通知历史
- 无法在不编辑文件的情况下启用/禁用规则

## 2. 目标

在 **不改变 core pipeline** 的前提下，增加规则与通知的可观测性。

## 3. v1 精确边界

### 3.1 新增 CLI 子命令

```bash
# 规则管理
web-watcher rules list [--channel <name>] [--enabled|--disabled]
web-watcher rules show <rule_id>
web-watcher rules enable <rule_id>
web-watcher rules disable <rule_id>

# 通知历史
web-watcher notify history [--rule <rule_id>] [--channel <name>] [--limit <n>]
```

### 3.2 规则状态模型

```yaml
# rules.yaml 中每个规则新增可选字段：
status: enabled   # 默认值，规则正常执行
# status: disabled  # 规则被跳过，不影响已有数据
```

- `enabled`：正常参与调度与评估
- `disabled`：跳过该规则，不产生新信号/事件/通知
- 状态变更仅影响后续执行，不删除历史数据

### 3.3 通知历史模型

通知持久化已有 DB schema，只需增加查询接口：
- 按 rule_id 过滤
- 按 channel 过滤
- 按时间范围过滤
- 默认最近 50 条，可 `--limit` 调整

### 3.4 明确不加入

- 规则 import/export（v2）
- 规则版本控制（v2）
- 通知重发/重试（已有至少一次语义）
- Web UI / API（CLI 优先）
- 规则分组/标签（v2）

## 4. 实现计划

1. 扩展 `WatcherRule`，增加 `status: str = "enabled"` 字段
2. 扩展 `rule_parser`，解析 `status` 字段
3. 在 `GenericWebTarget.execute()` 中检查规则状态，disabled 则跳过
4. 新增 `rules` CLI 子命令组（list/show/enable/disable）
5. 新增 `notify history` 子命令
6. 补充测试：
   - rules list 过滤
   - rules show 详情
   - rules enable/disable 状态变更
   - disabled 规则不产生信号
   - notify history 查询
7. 全量 pytest 验证

## 5. 待确认

1. 设计是否按此冻结边界进入施工？
