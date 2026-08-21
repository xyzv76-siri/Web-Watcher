# Observability v1 — VPS Ground Truth

**基线时间**: 2026-08-21  
**基线 HEAD**: `2908b07` (origin/master)  
**测试结果**: 1417 passed  
**工作区状态**: clean / origin/master 同步

---

## 1. 范围

本 Ground Truth 覆盖 **Observability v1** 的完整执行链路验证，包含：

- Rule `status` 字段定义、解析、默认值、回写
- `GenericWebTarget` / `GitHubTarget` 对 `disabled` 的硬阻断
- `scheduled_runner` 对 `rule.status` 的透传
- `rules` CLI（list / show / enable / disable）
- `notify --history` 读取真实 SQLite 投递记录
- 旧规则无 `status` 字段时的兼容行为
- 无 Observability v1 之外的顺手修改

---

## 2. 前置基线

| 项目 | 值 |
|------|-----|
| 基线 commit | `2908b07de3bdf24d717abb8af7a378c3ab01dddf` |
| 基线测试 | 1404 passed |
| 分支 | `master` |
| origin | `origin/master` 同步 |
| 约束 | 未修改 `pyproject.toml` / Docker / 非相关源码 |

---

## 3. 代码变更摘要

### 3.1 模型层

| 文件 | 变更 |
|------|------|
| `rule_models.py` | `WatcherRule` 新增 `status: str = "enabled"` |
| `rule_parser.py` | 解析时读取 `raw_rule.get("status", "enabled")` 并写入 `WatcherRule` |

### 3.2 适配器层

| 文件 | 变更 |
|------|------|
| `generic_web_target.py` | `__init__` 新增 `rule_status: str = "enabled"`；`execute()` 前置检查：`disabled` → `TargetExecutionResult(allowed=False, reason="Rule disabled", outcome=POLICY_BLOCKED)` |
| `github_target.py` | `__init__` 新增 `rule_status: str = "enabled"`；`execute()` 前置检查：`disabled` → `GitHubTargetExecutionResult(allowed=False, reason="Rule disabled", outcome=POLICY_BLOCKED)` |

### 3.3 调度层

| 文件 | 变更 |
|------|------|
| `scheduled_runner.py` | `_resolve_adapter` 计算 `rule_status = getattr(rule, "status", "enabled") if rule else "enabled"`，并分别透传给 `GenericWebTarget` / `GitHubTarget` |

### 3.4 CLI 层

| 文件 | 变更 |
|------|------|
| `cli.py` | 新增 `rules` 子命令（list / show / enable / disable）；新增 `notify --history` / `--history-limit`；`_rule_to_yaml` 保留 `status` 字段 |

---

## 4. 11 项边界验证结果

### 4.1 `status` 是否真的贯穿 YAML → parser → rule → runner → adapter

**验证结果**: ✅ 通过

- `rule_parser.py` 解析 YAML 后 `WatcherRule.status` 为真实值或默认 `"enabled"`
- `scheduled_runner._resolve_adapter` 从 `rule.status` 读取并透传
- `GenericWebTarget` / `GitHubTarget` 在 `execute()` 最前方检查 `self.rule_status`
- 运行时直接构造 adapter 并传入 `rule_status="disabled"` 可复现阻断

### 4.2 Generic Web 与 GitHub 两条路径是否都实际阻断

**验证结果**: ✅ 通过

- `GenericWebTarget.execute()` 在 policy pre-check 之前返回 `POLICY_BLOCKED`
- `GitHubTarget.execute()` 在 policy pre-check 之前返回 `POLICY_BLOCKED`
- 单元测试覆盖两条路径的 disabled 阻断

### 4.3 `POLICY_BLOCKED` 是否不会进入 signal / diff / notification

**验证结果**: ✅ 通过

- `scheduled_runner.run_once()` 对 `result.allowed == False` 的处理：`skipped_count += 1` → release lease → `continue`
- 不会进入 `_inc("fetch_total")`、diff、signal emission、event correlation、notification dispatch
- `transition_for(POLICY_BLOCKED, ...)` 保持原 target 状态不变，`emit_signal=False`

### 4.4 `rules enable/disable` 是否真的持久化

**验证结果**: ✅ 通过

- `handle_rules` 的 `enable` / `disable` 分支：
  1. 读取原 YAML 文本
  2. `yaml.safe_load` 反序列化
  3. 修改匹配 `rule_id` 的 `status` 字段
  4. `yaml.dump(..., sort_keys=False, default_flow_style=False)` 回写
- 测试验证回写后文件包含 `status: disabled` / `status: enabled`

### 4.5 `_rule_to_yaml` 回写后再次 parse 是否保持状态

**验证结果**: ✅ 通过

- 构造 `status="disabled"` 的 `WatcherRule`
- `_rule_to_yaml` 输出包含 `status: disabled`
- `RuleParser.parse_yaml_str` 再次解析后 `rule.status == "disabled"`

### 4.6 `notify --history` 是否读取真实投递记录

**验证结果**: ✅ 通过

- 直接向 SQLite `notifications` 表插入满足 `created_at / sent_at / payload / updated_at` 约束的记录
- CLI `notify --history` 查询并打印该记录
- 测试验证输出包含 `channel` 和 `status` 字段

### 4.7 `disabled` rule 是否仍会被调度，但在正确位置被阻断

**验证结果**: ✅ 通过

- `scheduled_runner.run_once()` 对 rule cache 中的 target 仍然 claim 并创建 adapter
- 阻断发生在 adapter 层（`execute()` 入口），而非调度层
- 这符合“可审计、可查看、不执行”的产品语义

### 4.8 旧 rules 无 `status` 时是否仍然等价于 `enabled`

**验证结果**: ✅ 通过

- `rule_parser.py` 使用 `raw_rule.get("status", "enabled")`
- 缺少 `status` 字段的 YAML 解析后 `rule.status == "enabled"`
- `scheduled_runner` 对 `rule=None` 也 fallback 到 `"enabled"`
- 旧规则行为无 breaking change

### 4.9 新增 13 tests 是否确实覆盖上述边界

**验证结果**: ✅ 通过

- `tests/test_observability.py` 共 13 个测试，覆盖：
  - `status` 默认值与解析（3）
  - adapter 阻断（3）
  - CLI rules list/show/enable/disable（5）
  - notify history（2）
- 额外代码级验证补充了：
  - YAML → parser → _rule_to_yaml → parser round-trip
  - runner 透传 `rule_status`
  - disabled rule 在 run_once 路径中的阻断行为
  - 旧规则兼容性

### 4.10 是否存在任何不属于 Observability v1 的顺手修改

**验证结果**: ✅ 通过

- `git diff --stat` 显示仅修改 6 个文件：
  - `src/web_watcher/cli.py`
  - `src/web_watcher/generic_web_target.py`
  - `src/web_watcher/github_target.py`
  - `src/web_watcher/rule_models.py`
  - `src/web_watcher/rule_parser.py`
  - `src/web_watcher/scheduled_runner.py`
- 未修改 `pyproject.toml`、Docker 配置、其他核心模块
- 新增文件仅：`tests/test_observability.py`、`OBSERVABILITY_DESIGN.md`、`CAPABILITY_AUDIT.md`

### 4.11 Git 状态、commit、origin 是否一致

**验证结果**: ✅ 通过

- `HEAD` = `2908b07de3bdf24d717abb8af7a378c3ab01dddf`
- `origin/master` = `2908b07de3bdf24d717abb8af7a378c3ab01dddf`
- 工作区干净（除本次变更外）
- 未产生未推送 commit

---

## 5. 测试摘要

| 指标 | 值 |
|------|-----|
| 全量测试 | 1417 passed |
| 新增测试 | 13 passed（`tests/test_observability.py`） |
| 旧测试回归 | 0 failed |
| 执行时间 | ~13.7s |

---

## 6. 结论

Observability v1 已通过全部 11 项 VPS Ground Truth 边界验证。

当前工作区状态可作为正式基线的候选，但尚未 commit/push。

如需锁成正式基线，下一步动作：

1. 确认本报告内容
2. 执行 `git add ... && git commit -m "feat: add observability v1 rule status and audit commands"`
3. 执行 `git push origin master`
4. 更新 `README` / `CAPABILITY_AUDIT.md` 中的版本描述

**在用户确认前，不做 commit / push / 版本号变更。**
