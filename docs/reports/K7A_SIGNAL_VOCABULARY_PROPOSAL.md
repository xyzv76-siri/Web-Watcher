# Phase 11-B / K.7-A — Signal Vocabulary Architecture Freeze 提案

> **文档状态**: 提案草案 · 待人类审阅签署
> **诊断范围**: K.7-A.1（信号层）+ K.7-A.2（事件层）+ K.7-A.3（分类层）
> **诊断日期**: 2026-08-17
> **修改目标**: `src/web_watcher/event_correlator.py`、`src/web_watcher/policy.py`、`src/web_watcher/models.py`（如需要新增枚举）
> **保护约束**: 不修改 K.1–K.6 文件、Phase 10 文件、`investigation_engine.py`、`investigation_planner.py`、`investigate_requested.py`

---

## 1. 问题陈述

### 1.1 生产信号链路

```
GitHub API → GitHubRepositoryAdapter → GitHubRepositorySnapshot (14 fields)
    → FetchService (content_hash comparison only)
        → Signal(signal_type="content_change", value=content_hash)  ← 唯一信号类型
            → EventCorrelator.correlate()
                → _derive_event_type()  → return signal.signal_type  ← 透传 "content_change"
                → _classify_importance() → return config.default_importance  ← 默认 "medium"
                → _derive_status()       → return "open"                  ← 硬编码
                → Event(event_type="content_change", importance="medium", status="open")
                    → [零消费者]   ← main.py 是 stub
```

### 1.2 四层断裂

| 层 | 断裂点 | 后果 |
|----|--------|------|
| **L1: 信号词汇表** | 生产仅 1 种 signal_type（`"content_change"`） | InvestigationPlan.scope 除 `content_change` 外永不匹配 |
| **L2: 事件推导** | `_derive_event_type()` 透传信号类型，不映射 | Event 永远无业务语义 |
| **L3: 事件分级** | `_classify_importance()` 返回配置默认值 | 所有事件同级，无优先级 |
| **L4: 策略匹配** | Policy 期望 `{"critical","important","interesting"}`，但 Event 从不产生这些值 | **永远 `IGNORE → DISCARD`** |

### 1.3 测试与生产之间的词汇表鸿沟

| 词汇表 | 来源 | 值 | 到达生产？ |
|--------|------|-----|-----------|
| 生产信号 | `fetch_service.py:55` | `"content_change"` | ✅ |
| 注释规划信号 | `event_correlator.py:179` | `"star_velocity","release","trending","commit_velocity"` | ❌ 零实现 |
| 测试 mock 信号 | `tests/` | `"release","star_velocity","push","star"` | ❌ 零到达 |
| 测试事件类型 | `test_domain_state.py:28` | `"major_release"` | ❌ 零到达 |
| 测试决定类型 | `test_decide.py:26` | `"interesting","important","critical"` | ❌ 零到达 |
| Policy 期望类型 | `policy.py:66-75` | `"critical","important","interesting"` | ❌ 永不匹配 |

**六套词汇表互不相交，每层各自定义了自己的语义。**

---

## 2. 目标词汇表定义（K.7-A 核心交付物）

### 2.1 Signal Vocabulary（信号层）

**定义位置**: 新增 `src/web_watcher/signal_types.py`

```python
"""Signal type vocabulary — the canonical signal types produced by the Fetch layer."""

from enum import Enum, StrEnum  # StrEnum requires 3.11+; use Enum[str] compat

# Signal types — what the Fetch layer observes from a source.
# These are LOW-LEVEL observations, not business conclusions.

class SignalType(str, Enum):
    """Canonical signal types produced by data source adapters.

    Each signal type represents a DISTINCT OBSERVATION from a source.
    Multiple signals may correlate into a single Event (see EventCorrelator).

    Mapping to GitHub Repository Adapter fields:
        - content_change: any field in the repository snapshot changed (content_hash diff)
        - stars_changed: stargazers_count changed
        - forks_changed: forks_count changed
        - issues_changed: open_issues_count changed
        - pushed: pushed_at changed (new commit detected)
        - description_changed: description field changed
        - metadata_changed: any other field changed (visibility, license, etc.)
    """

    CONTENT_CHANGE = "content_change"
    STARS_CHANGED = "stars_changed"
    FORKS_CHANGED = "forks_changed"
    ISSUES_CHANGED = "issues_changed"
    PUSHED = "pushed"
    DESCRIPTION_CHANGED = "description_changed"
    METADATA_CHANGED = "metadata_changed"
```

**设计理由**:
- 枚举而非自由字符串 → 编译期可检查、grep 可发现
- 信号类型描述"观察到了什么"，不描述"意味着什么"
- 映射到 GitHub Snapshot 的 14 个字段，每个可独立变化

### 2.2 Event Type Vocabulary（事件层）

**定义位置**: 新增 `src/web_watcher/event_types.py`

```python
"""Event type vocabulary — business-level event classifications."""

from enum import Enum

class EventType(str, Enum):
    """Canonical event types — what the Event layer classifies signals into.

    Events represent BUSINESS-LEVEL interpretations of one or more signals.
    Multiple signals (e.g. stars_changed + pushed) may combine into a single event.

    Mapping from SignalType → EventType:
        content_change        → CONTENT_UPDATED
        stars_changed         → STAR_VELOCITY
        forks_changed         → STAR_VELOCITY      (combined metric)
        issues_changed        → ISSUE_VELOCITY
        pushed                → NEW_COMMIT
        description_changed   → METADATA_UPDATED
        metadata_changed      → METADATA_UPDATED
    """

    CONTENT_UPDATED = "content_updated"
    STAR_VELOCITY = "star_velocity"
    ISSUE_VELOCITY = "issue_velocity"
    NEW_COMMIT = "new_commit"
    METADATA_UPDATED = "metadata_updated"
    RELEASE_PUBLISHED = "release_published"
    UNKNOWN = "unknown"
```

### 2.3 Importance Vocabulary（策略对齐层）

**现有定义**（`policy.py:13-20`），无需修改：

```python
class Importance(str, Enum):
    IGNORE = "ignore"
    INTERESTING = "interesting"
    IMPORTANT = "important"
    CRITICAL = "critical"
```

**但 Event 层的 `importance` 字段目前是 `str`（自由字符串）**，需要：

- `models.py` 中 `Event.importance` 从 `str` 改为 `Importance` 枚举
- `event_correlator.py` 中 `_classify_importance()` 返回 `Importance` 枚举值
- 所有 `Event` 创建点使用枚举值

### 2.4 Event Status Vocabulary（生命周期层）

**定义位置**: 新增 `src/web_watcher/event_status.py`

```python
from enum import Enum

class EventStatus(str, Enum):
    """Event lifecycle statuses."""
    OPEN = "open"
    CORRELATED = "correlated"    # signals merged into existing event
    CLOSED = "closed"            # event aged out or resolved
```

---

## 3. 映射表：SignalType → EventType → Importance → Action

### 3.1 核心映射矩阵

| SignalType | EventType | Default Importance | Policy Action |
|------------|-----------|-------------------|---------------|
| `content_change` | `content_updated` | `INTERESTING` | `SUMMARIZE` |
| `stars_changed` | `star_velocity` | `INTERESTING` | `SUMMARIZE` |
| `stars_changed` (significant) | `star_velocity` | `IMPORTANT` | `NOTIFY` |
| `forks_changed` | `star_velocity` | `INTERESTING` | `SUMMARIZE` |
| `issues_changed` | `issue_velocity` | `INTERESTING` | `SUMMARIZE` |
| `pushed` | `new_commit` | `INTERESTING` | `SUMMARIZE` |
| `description_changed` | `metadata_updated` | `IGNORE` | `DISCARD` |
| `metadata_changed` | `metadata_updated` | `IGNORE` | `DISCARD` |

### 3.2 重要性提升规则（信号聚合时）

| 条件 | 提升等级 |
|------|----------|
| `stars_changed` delta ≥ 100 in correlation window | `IMPORTANT` |
| `stars_changed` delta ≥ 500 in correlation window | `CRITICAL` |
| `issues_changed` delta ≥ 10 in correlation window | `IMPORTANT` |
| `pushed` + `stars_changed` within correlation window | `IMPORTANT` |
| `content_change` + `pushed` within correlation window | `IMPORTANT` |

### 3.3 映射在 K.7-A 中的实现位置

```python
# event_correlator.py — 新增模块级映射表
_SIGNAL_TO_EVENT: dict[SignalType, EventType] = {
    SignalType.CONTENT_CHANGE: EventType.CONTENT_UPDATED,
    SignalType.STARS_CHANGED: EventType.STAR_VELOCITY,
    SignalType.FORKS_CHANGED: EventType.STAR_VELOCITY,
    SignalType.ISSUES_CHANGED: EventType.ISSUE_VELOCITY,
    SignalType.PUSHED: EventType.NEW_COMMIT,
    SignalType.DESCRIPTION_CHANGED: EventType.METADATA_UPDATED,
    SignalType.METADATA_CHANGED: EventType.METADATA_UPDATED,
}

# 默认重要性映射
_SIGNAL_TO_IMPORTANCE: dict[SignalType, Importance] = {
    SignalType.CONTENT_CHANGE: Importance.INTERESTING,
    SignalType.STARS_CHANGED: Importance.INTERESTING,
    SignalType.FORKS_CHANGED: Importance.INTERESTING,
    SignalType.ISSUES_CHANGED: Importance.INTERESTING,
    SignalType.PUSHED: Importance.INTERESTING,
    SignalType.DESCRIPTION_CHANGED: Importance.IGNORE,
    SignalType.METADATA_CHANGED: Importance.IGNORE,
}
```

---

## 4. 代码修改范围

### 4.1 新增文件（3 个）

| 文件 | 内容 | 行数估算 |
|------|------|----------|
| `src/web_watcher/signal_types.py` | `SignalType` 枚举 | ~35 |
| `src/web_watcher/event_types.py` | `EventType` 枚举 | ~25 |
| `src/web_watcher/event_status.py` | `EventStatus` 枚举 | ~18 |

### 4.2 修改文件（3 个）

| 文件 | 修改内容 | 行数估算 |
|------|----------|----------|
| `src/web_watcher/models.py` | `Event.importance: str` → `Importance` | ~5 |
| `src/web_watcher/event_correlator.py` | 实现 `_derive_event_type()`、`_classify_importance()`、`_derive_status()` | ~40 |
| `src/web_watcher/policy.py` | `_importance()` 增加对 `EventType` 枚举的映射 | ~20 |

### 4.3 受保护 — 不修改

```
src/web_watcher/ai_contract.py
src/web_watcher/ai_errors.py
src/web_watcher/ai_config.py
src/web_watcher/ai_provider.py
src/web_watcher/llm_provider.py
src/web_watcher/policy.py      ← 仅修改 _importance() 方法
src/web_watcher/decide.py
src/web_watcher/final_decision.py
src/web_watcher/investigation_engine.py
src/web_watcher/investigation_planner.py
src/web_watcher/investigation_contract.py
src/web_watcher/investigation_policy.py
src/web_watcher/investigation_plan.py
src/web_watcher/investigation_result.py
src/web_watcher/investigate_requested.py
src/web_watcher/notify_allowed.py
```

### 4.4 新增测试文件

| 文件 | 内容 | 测试数估算 |
|------|------|-----------|
| `tests/test_signal_types.py` | SignalType 枚举完备性 | 5 |
| `tests/test_event_types.py` | EventType 枚举完备性 | 4 |
| `tests/test_event_classification.py` | Signal → Event 映射 | 12 |
| `tests/test_importance_derivation.py` | 信号特征 → Importance | 8 |

---

## 5. 关键设计决策

### D1: 信号层 vs. 事件层分离

**决策**: Signal 描述"观察到了什么"（低层、原子），Event 描述"这意味着什么"（业务层、聚合）。

**理由**: 一个 `stars_changed` 信号在 1 小时内出现 50 次，应该聚合为 1 个 `STAR_VELOCITY` 事件，而不是 50 个 `CONTENT_CHANGE` 事件。

**风险**: 需要 EventCorrelator 实现聚合窗口逻辑（已有 `correlation_window_seconds`）。

### D2: 枚举 vs. 自由字符串

**决策**: 信号层和事件层全部使用 `str, Enum`，不使用自由字符串。

**理由**:
- 编译期可检查（`SignalType.NONEXISTENT` 直接报错）
- grep 可发现所有使用点
- 避免 `"critical"`/`"important"`/`"interesting"` 这种裸字符串散落在代码中

**成本**: 需要修改 `models.py` 的 `Event.importance` 类型，以及所有测试 mock 创建点。

### D3: 不修改 `policy.py` 的 `Importance` 枚举

**决策**: 保留现有 `IGNORE/INTERESTING/IMPORTANT/CRITICAL` 四值。

**理由**: 这是 Phase 10 的核心决策语义，已被 `final_decision.py`、`decide.py`、`ai_contract.py` 等大量引用。修改会带来不可控的连锁变更。

### D4: `content_change` 信号保留，但细化

**决策**: 保留 `CONTENT_CHANGE` 作为信号类型，但从 v2 开始，FetchService 将产生多种信号类型（`stars_changed`、`pushed` 等）。

**理由**:
- 向后兼容：已有测试引用 `content_change`
- 渐进迁移：`content_change` 可以作为一个 catch-all，新信号类型逐步加入

### D5: 不修改 Investigation Engine/Planner

**决策**: K.7-A 仅补信号词汇表层，不改 Investigation 层。

**理由**:
- Investigation 层的 `scope` 机制可以透明地消费新信号类型
- 无需修改 `investigation_engine.py` 的任何行
- 降低变更范围，符合 Phase 11-A 的"最小可行层"原则

---

## 6. 与现有测试的兼容性

### 6.1 现有测试中使用的信号类型

| 测试文件 | 使用值 | 兼容性 |
|----------|--------|--------|
| `test_ai_contract.py:76` | `"content_change"` | ✅ 匹配 `SignalType.CONTENT_CHANGE` |
| `test_domain_state.py:20` | `"release"` | ❌ 无对应枚举 → 需要更新为 `"content_change"` 或新增 `RELEASE_PUBLISHED` |
| `test_event_correlator.py:44` | `"content_change"` | ✅ |
| `test_event_correlator.py:421` | `"star_velocity"` | ❌ 这是 `signal_type` 位置但用了 `event_type` 语义 → 需要修正 |
| `test_models.py:21` | `"push"` | ❌ 无对应 → 应改为 `SignalType.PUSHED` |
| `test_models.py:26` | `"star"` | ❌ 无对应 → 应改为 `SignalType.STARS_CHANGED` |

### 6.2 需要更新的测试

| 测试 | 修改 |
|------|------|
| `test_domain_state.py` | `"release"` → `"content_change"` |
| `test_event_correlator.py:421` | `"star_velocity"` → `"content_change"` |
| `test_models.py:21` | `"push"` → `"content_change"` |
| `test_models.py:26` | `"star"` → `"content_change"` |

---

## 7. 验收条件（AC）

| # | 条件 | 验证方式 |
|---|------|----------|
| AC-1 | `SignalType` 枚举定义 7 个值，每个值映射到 GitHub Snapshot 的已知字段 | 代码审查 + `test_signal_types.py` |
| AC-2 | `EventType` 枚举定义 7 个值，每个值可由至少一个 SignalType 推导 | 代码审查 + `test_event_types.py` |
| AC-3 | `_derive_event_type()` 使用映射表，返回 `EventType` 枚举值（或字符串值） | `test_event_classification.py` |
| AC-4 | `_classify_importance()` 返回 `Importance` 枚举值，非自由字符串 | `test_importance_derivation.py` |
| AC-5 | `_derive_status()` 返回 `EventStatus` 枚举值 | `test_event_classification.py` |
| AC-6 | `Event.importance` 类型从 `str` 改为 `Importance`，所有创建点更新 | `pytest` 全量通过 |
| AC-7 | `policy._importance()` 能匹配 `EventType` 枚举值 | `test_policy.py` 更新 |
| AC-8 | 不修改 K.1–K.6 保护文件 | `git diff` 验证 |
| AC-9 | 不修改 Phase 10 保护文件 | `git diff` 验证 |
| AC-10 | 全量测试 ≥ 817 pass | `pytest` |
| AC-11 | `compileall` 零错误 | `python -m compileall` |
| AC-12 | 新信号词汇表在 grep 中可发现（`grep -r SignalType` 返回所有使用点） | grep 验证 |

---

## 8. 未解决问题（需人类决策）

1. **是否新增 `signal_types.py` / `event_types.py` / `event_status.py` 三个新文件？** 还是将枚举合并到 `models.py`？
   - 推荐：独立文件（清晰分离，便于 import）

2. **`stars_changed` delta 阈值（100/500）**是否合理？是否需要配置化？
   - 推荐：硬编码起始，后续通过 config 暴露

3. **`release_published` 信号**是否从 GitHub API 的 `releases` endpoint 获取？
   - 当前 GitHubRepositoryAdapter 不调用 `/releases`，该信号类型在 K.7-A 中仅定义、不实现

4. **是否需要修改 `models.py` 的 `Event.importance` 类型？** 这将影响所有测试 mock。
   - 推荐：需要修改，但可推迟到 K.7-A 实现阶段与测试更新同步进行

---

## 9. 待签署项

```
[ ] 我审阅并批准 K.7-A Signal Vocabulary 架构定义
    - SignalType 枚举（7 值）
    - EventType 枚举（7 值）
    - 映射表（SignalType → EventType → Importance）
    - 重要性提升规则
    - 代码修改范围（新增 3 文件 + 修改 3 文件）
    - 验收条件 AC-1 至 AC-12

[ ] 我审阅并批准上述 4 项未解决问题的推荐决策

[ ] 我理解 K.7-A 仅补信号词汇表层，不连接运行时 pipeline
    （pipeline 连接属于 K.8 范畴）
```

---

*本提案为只读诊断输出。签署后才进入实现阶段。*
*K.1–K.6 保护文件零修改。Phase 10 保护文件零修改。*
