# Debug / Inspection Mode v1 Design

## 1. 问题

现有 `test-rule` 只能看到 extractor 的最终值，看不到中间链路：
- fetch 是否成功
- scope_selector 是否命中
- normalize 前后变化
- diff 具体差异
- signal 为什么被发出/抑制

用户调试规则时缺少可观测性。

## 2. 目标

新增 `inspect` 子命令，对单个 rule 执行一次完整 pipeline，
并以人类可读格式输出每个阶段的详细结果。

## 3. v1 精确边界

### 3.1 支持输入

| 来源 | 说明 |
|------|------|
| `--rule <yaml>` | 规则文件路径 |
| `--url <url>` | 远程 URL，实时抓取 |
| `--html-file <path>` | 本地 HTML 文件，用于离线调试 |

### 3.2 输出内容

执行完整 pipeline：
fetch → extract → scope → normalize → diff → observation

输出每个 extractor 的：
- raw_value（裁剪后）
- scope_selector 命中情况
- normalized_value
- previous_value（如有）
- diff.changed / diff.summary
- diff.before / diff.after（截断显示）
- evidence 关键字段

最终输出：
- observation.status
- observation.reason
- signal 是否会被发出
- outcome

### 3.3 不做的事（v1 排除）

- 不修改数据库
- 不持久化 observation / signal
- 不触发 notification
- 不支持 daemon / loop 模式
- 不暴露内部实现细节（fencing、locking 等）
- 不提供 JSON/structured 输出（v1 仅 human-readable）

### 3.4 CLI 设计

```
python -m web_watcher.cli inspect --rule config/rules.yaml --url https://example.com
python -m web_watcher.cli inspect --rule config/rules.yaml --html-file sample.html
```

参数：
- `--rule`：规则文件（必填）
- `--url`：远程 URL（二选一）
- `--html-file`：本地 HTML 文件（二选一）
- `--extractor <name>`：只检查指定 extractor（可选）
- `--verbose`：显示完整 diff/evidence（可选）

### 3.5 实现路径

1. 新增 `handle_inspect()` 函数
2. 复用 `RuleParser`、`GenericWebTarget`、`SmartFetcher`
3. 不经过 `ScheduledRunner` / `PipelineRunner`，直接执行单次
4. 输出格式：plain text，结构化但人类可读

## 4. 验收标准

1. `inspect --rule X --url Y` 能完整执行并输出各阶段结果
2. `inspect --rule X --html-file Y` 能离线执行
3. scope miss 时明确显示，不静默回退
4. 无 scope 的规则行为与 `test-rule` 一致
5. 输出不包含敏感信息（token、password 等）
6. 全量测试不减少
