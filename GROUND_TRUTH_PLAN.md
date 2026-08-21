# GROUND_TRUTH — Tag Filtering + Hot Reload + Rule Registry 联合验收

## 目标
以 9583897 为候选最新基线，完成三组件联合 Ground Truth，确认组合语义正确。

## 基线
- 提交: 9583897
- 测试: 1458 passed
- 组件: Tag Filtering v1 + Hot Reload v1 + Rule Registry v1

## 必须验证的场景

### 1. 普通 rule
- YAML 中的 enabled rule 正常执行

### 2. include tag
- `--include-tag pricing` 只执行 pricing tagged rules

### 3. exclude tag
- `--exclude-tag pricing` 排除 pricing tagged rules

### 4. disabled registry rule
- registry 中 disabled 的 rule 不执行
- 即使 YAML 中 status=enabled

### 5. priority
- 高 priority rule 先执行
- 不破坏 claim/fencing 语义

### 6. YAML 修改
- 修改 rules.yaml 后，下次 run_once 自动 reload

### 7. YAML 新增
- 新增 rule 到 rules.yaml，reload 后出现

### 8. YAML 删除
- 从 rules.yaml 删除 rule，reload 后消失

### 9. reload + registry
- YAML reload 后，registry 状态不被覆盖
- disabled 状态保持

### 10. tag + registry
- Tag filter 和 registry 过滤正确叠加

### 11. registry + priority + claim
- Priority 排序发生在 claim 之前
- 不破坏 multi-worker fencing

### 12. daemon
- 长驻状态下自动 reload + tag + registry 全部成立

### 13. CLI reload
- `python -m web_watcher.cli reload` 与自动 reload 语义一致

## 验收方法
- 单元测试覆盖场景 1-11
- 集成测试覆盖场景 12-13
- 全量测试回归

## 下一步
验收通过后，进入 Runtime State / Configuration Source-of-Truth Design。
