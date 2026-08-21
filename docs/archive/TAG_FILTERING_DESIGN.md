# Tag Filtering v1 Design

## 目标
为 `run` / `daemon` 提供按标签筛选监控任务的能力，与 Preset 默认标签和 TAGGING 分组能力形成闭环。

## 范围
- 仅支持命令行参数 `--include-tags` / `--exclude-tags`
- 暂不支持配置文件层面的标签过滤
- 仅修改 `PipelineRunner` 过滤逻辑，不改变底层 diff / signal / notification 语义

## 语义设计

### 1. `--include-tags` 多值解释
- **OR 语义**：规则 tags 与任一 include 值相交即命中
- 例：`--include-tags price --include-tags ecommerce` 会选中带 `price` 或 `ecommerce` 的规则

### 2. `--exclude-tags` 多值解释
- **OR 语义**：规则 tags 与任一 exclude 值相交即被排除
- 例：`--exclude-tags status --exclude-tags ops` 会排除带 `status` 或 `ops` 的规则

### 3. include + exclude 同时存在时的优先级
- **exclude 优先**：先执行 exclude 过滤，再对剩余规则执行 include 过滤
- 逻辑：`if exclude_match: skip; elif include_match: keep; else: skip`

### 4. 无 tags 的旧规则处理
- **视为空集合**：无 tags 的规则：
  - include 模式：不命中（空集合与任何集合的交集为空）
  - exclude 模式：不命中（空集合不包含任何 exclude 值）
  - 无 include/exclude：正常执行

### 5. CLI 与配置文件支持
- v1 仅支持 CLI 参数
- 配置文件支持留待 v2（可能需要 `rules.yaml` 字段扩展或独立 `tags.yaml`）

### 6. run 与 daemon 共享过滤逻辑
- **共享**：两者都使用 `PipelineRunner` 的同一套过滤方法
- 避免行为不一致

### 7. PipelineRunner 过滤位置
- **在 claim 之前**：在 `PipelineRunner.run_batch_signals` 入口处，基于当前 ruleset 过滤
- 原因：减少不必要的 lease claim 和后续计算

### 8. 被过滤的 rule 是否算 skipped
- **不算 skipped**：标签过滤属于"未进入执行队列"，不是执行过程中的跳过
- 不产生 event / notification
- 可在 summary 中新增 `rules_filtered` 计数（仅统计，不持久化）

### 9. skipped 是否产生 event / notification
- **不产生**：被过滤的规则不进入 `process_signal`，无 event，无 notification

### 10. 与 `status=disabled` 的优先级关系
- **status 优先**：先过滤 `status=disabled`，再执行标签过滤
- 逻辑：
  1. 移除 disabled rules
  2. 对剩余 enabled rules 执行 tag filtering
  3. 剩余 rules 进入执行队列

### 11. Preset 默认 tags 是否正式参与执行
- **正式参与**：Preset 生成的 tags 与手动编辑的 tags 无差别，都参与过滤
- 用户可通过 `--no-preset-tags`（未来扩展）或直接编辑 rules.yaml 覆盖

### 12. Backward compatibility
- 无 `--include-tags` / `--exclude-tags` 时，行为完全不变
- 旧 rules 无 `tags` 字段时，按空集合处理（见第 4 点）
- 不影响现有 event / notification / retention 语义

## 伪代码

```python
def filter_rules_by_tags(
    rules: List[WatcherRule],
    include_tags: List[str],
    exclude_tags: List[str],
) -> Tuple[List[WatcherRule], int]:
    filtered = []
    filtered_count = 0
    
    for rule in rules:
        # 10. status 优先（在调用前已过滤 disabled）
        rule_tags = set(rule.tags or [])
        
        # 3. exclude 优先
        if exclude_tags and rule_tags & set(exclude_tags):
            filtered_count += 1
            continue
        
        # 1/2. include 检查
        if include_tags and not (rule_tags & set(include_tags)):
            filtered_count += 1
            continue
        
        filtered.append(rule)
    
    return filtered, filtered_count
```

## CLI 参数设计

```bash
# run 子命令
python -m web_watcher.cli run --once \
  --include-tags price --include-tags ecommerce \
  --exclude-tags status

# daemon 子命令
python -m web_watcher.cli daemon \
  --include-tags critical \
  --exclude-tags test
```

## 待确认事项
1. summary 中的 `rules_filtered` 是否输出到日志？
2. `PipelineRunner` 是否接受 `include_tags` / `exclude_tags` 构造参数？
3. 是否需要 `--tag` 单数别名（兼容 users 习惯）？
4. 错误提示：当 include/exclude 都为空时，是否静默忽略？

## 验收标准
- [ ] `PipelineRunner` 支持标签过滤
- [ ] CLI `run` / `daemon` 支持 `--include-tags` / `--exclude-tags`
- [ ] 全量测试通过，无回归
- [ ] 新增 6+ 标签过滤边界测试
- [ ] README 补充用法示例

## 状态
设计已确认（2026-08-21），等待实现。
