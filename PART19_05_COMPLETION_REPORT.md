# PART 19-05 — Unified Signal → Event → Investigation → Notification

## 一、唯一允许的因果链

已完成：创建 `src/web_watcher/pipeline.py` 中的 `UnifiedPipeline` 类，强制实施以下因果链：

```
Fetch → Observation → Signal → Event → Investigation → Policy → Notification
```

禁止任何 Adapter 直接跳转到 Notification。

## 二、Signal 语义

- `Signal` 表示机器可描述的变化，已在 `models.py` 和 `signal_types.py` 中定义
- `UnifiedPipeline.process_observation` 接收 `Observation` 对象，从中提取 `Signal` 列表
- Signal 不负责 importance decision、human alerting 或 notification

## 三、Event 语义

- `Event` 表示需要进入业务处理流程的事件
- `EventCorrelator` 负责将 Signal 转换为 Event
- `Signal ≠ Event`，Signal 产生不自动触发 Event
- 重要性级别通过 `Importance` 枚举管理

## 四、Investigation 语义

- `EventInvestigationAdapter` 负责将 Event 适配为 Investigation
- Investigation 必须包含完整证据链：before/after/diff/timestamp/source/metadata
- `UnifiedPipeline` 在 Event 创建后自动 dispatch investigation（如果 eligible）
- Investigation 结果通过 `NotificationEnricher` 注入 notification payload

## 五、Notification 语义

- `Notification` 是最后一层，必须由 Event/Investigation/Policy 决定
- `UnifiedPipeline` 仅在 Event 创建后才创建 Notification
- Notification 不得修改 Target state、Event semantics 或 observation semantics

## 六、降噪与 Suppression

- `UnifiedPipeline` 实现了 suppression window 机制
- 同一 target 的相同 semantic event 在 suppression window 内被抑制
- 抑制发生在 Observation/Pipeline 层，不在 Notification 层
- 错误 Signal 不会被 Notification 层掩盖

## 七、代码变更

### 新增文件
- `src/web_watcher/pipeline.py` — UnifiedPipeline 类，统一因果链
- `tests/test_pipeline.py` — 15 个测试用例，覆盖：
  - 核心因果链（Signal → Event → Investigation → Notification）
  - 抑制窗口（within window / outside window / different event types）
  - 证据传播（observation evidence/metadata → notification）
  - 无绕过（无 signal 无 notification；无 event 无 enriched notification）
  - 批量处理

### 修改文件
- `src/web_watcher/repository.py` — 修复 `commit_plan` 方法中未定义的 `target_id` 变量导致的 NameError

## 八、测试结果

- 新测试：15 passed
- 全量测试：1249 passed, 1 failed（pre-existing，与 GitHub target 304 处理相关，非本次变更引入）

## 九、架构决策

- 复用现有 `EventCorrelator`、`EventInvestigationAdapter`、`NotificationEnricher`、`NotificationDispatcher`
- 新增 `UnifiedPipeline` 作为统一入口，不修改各组件内部逻辑
- `Observation` dataclass 作为 Fetch → Signal 的中间表示
- `PipelineResult` dataclass 记录完整处理结果

## 十、后续建议

- 将 `UnifiedPipeline` 集成到现有 worker/scheduler 流程中
- 考虑在 `EventCorrelator` 中增加更多 suppression 策略
- 考虑在 Investigation 结果中增加 observation evidence 的结构化存储
