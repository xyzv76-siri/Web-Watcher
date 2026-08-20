# GA 最终全局验收报告

**项目**: web-watcher
**日期**: 2026-08-20
**状态**: GA ACCEPT
**全量测试**: 1372 passed / 0 failed
**Git**: 干净，无未提交工作
**Branch**: audit/global-architecture-snapshot-20260819（与 origin 同步）

---

## 一、用户指定重点复核项

### 1. transaction / SQLite 副作用

| 维度 | 验证结果 | 证据 |
|------|----------|------|
| 原子边界 | PASS | `finalize_execution()` 在单事务内完成 fencing + Target update + Signal insert + Event create/update + Link create |
| 事务管理 | PASS | 仅 Repository 内部管理 transaction；Scheduler/Adapter 无自行模拟跨表 atomicity |
| SQLite 副作用 | PASS | 无 `direct DB writes`；所有写入通过 Repository；`host_rate_limits` 表 claim/release 也由 Repository 原子执行 |
| Schema 副作用 | PASS | 无 migration；`CREATE TABLE IF NOT EXISTS` 安全；`fetch_state` 为 legacy 未写入 |

**关键代码路径**:
- `repository.py:1561-1820` `finalize_execution()` — 单事务 `with self.connection:`
- `repository.py:1396-1480` `claim_targets()` — 原子 `UPDATE ... WHERE id = ? AND (lease_until IS NULL OR lease_until < ?)`
- `repository.py:1230-1290` `reap_stale_claims()` — 原子 `UPDATE ... WHERE claim_until IS NOT NULL AND claim_until <= ?`

### 2. stale host claim 边界问题

| 维度 | 验证结果 | 证据 |
|------|----------|------|
| stale claim 检测 | PASS | `commit_target_execution()` / `release_target_lease()` / `finalize_execution()` 均用 `WHERE id = ? AND claim_token = ?` |
| stale claim 拒绝 | PASS | 不匹配时返回 `False`，不产生 partial state |
| stale claim 无副作用 | PASS | 旧 worker 无法更新 Target、插入 Signal、修改 Event、释放新 worker 的 lease |
| lease 过期回收 | PASS | `scheduled_runner.py:312` 每次 `run_once()` 调用 `reap_stale_claims(older_than=now)` |

**关键代码路径**:
- `repository.py:1481-1560` `commit_target_execution()` — `cursor.rowcount > 0` 判断
- `repository.py:1561-1650` `finalize_execution()` — fencing check 在事务内最早执行
- `scheduled_runner.py:312-316` `reap_stale_claims()` — crash recovery 入口

### 3. multi-worker race

| 维度 | 验证结果 | 证据 |
|------|----------|------|
| claim 原子性 | PASS | SQLite 保留锁保证 `claim_targets()` 串行；两个 worker 同时 claim 同一 target，仅一个成功 |
| 失败方行为 | PASS | 得到 `rowcount == 0`，不降级执行，不 fallback 到 `list_schedulable_targets()` |
| duplicate finalization | PASS | 重复 `finalize_execution()` 因 fencing 返回 `False` |
| host rate limit race | PASS | `host_rate_limits` 表同一 host 的 claim 由 `acquire_host_request` 原子管理 |

**关键代码路径**:
- `repository.py:1443-1467` `claim_targets()` — 原子 UPDATE + `ORDER BY next_allowed_at ASC NULLS FIRST LIMIT ?`
- `scheduled_runner.py:321-327` — 无违禁 `elif self._rule_cache:` 回退

### 4. crash recovery

| 维度 | 验证结果 | 证据 |
|------|----------|------|
| lease 过期 | PASS | 默认 `lease_duration_sec=300.0`；crash 后 lease 过期 |
| 新 worker re-claim | PASS | `reap_stale_claims()` 清除过期 lease；新 worker 获得新 `claim_token` |
| 旧 worker 返回 | PASS | 旧 token 被 fencing 拒绝；永远不得清除新 worker 的 lease |
| 状态恢复 | PASS | Target status / etag / last_modified / content_hash / consecutive_failures / next_allowed_at 均持久化在 `targets` 表 |
| 无 partial transaction | PASS | 任何失败均触发 SQLite rollback |

**关键代码路径**:
- `docker_run.py:70-80` — 启动时 `_validate_database()` 强制 schema 初始化
- `repository.py:1230-1245` `reap_stale_claims()` — crash recovery 核心

### 5. Target ↔ Host 两层状态冲突

| 维度 | 验证结果 | 证据 |
|------|----------|------|
| Target 状态 authority | PASS | `TargetStatus` / `next_allowed_at` / `consecutive_failures` 由 Target 单一 authority |
| Host 状态 authority | PASS | `host_rate_limits` 表独立管理 per-host claim / next_allowed_at / rate limit |
| 状态合并 | PASS | `fetch_policy.py:121-200` `prepare_request()` 先检查 Target `next_allowed_at`，再检查 Host rate limit；任一阻止则 `allowed=False` |
| 无隐藏共享状态 | PASS | Host rate limiter 仅通过 `host_rate_limits` 表持久化；内存 `_active_claims` 为进程内缓存 |

**关键代码路径**:
- `fetch_policy.py:121-200` `prepare_request()` — 两层状态检查顺序
- `host_rate_limiter.py:36-60` `prepare_request()` — host-level atomic acquire
- `github_target.py:198-215` / `284-301` — subresource 独立 host claim

### 6. Generic Web / GitHub / Scheduler 是否都真正使用同一个 Host authority

| 组件 | Host authority 使用 | 验证结果 |
|------|---------------------|----------|
| Scheduler | PASS | `scheduled_runner.py:64-68` 创建唯一 `HostRateLimiter(repository=repo)` |
| GenericWebTarget | PASS | `generic_web_target.py:117-118` 通过 `policy.host_rate_limiter.release_request(decision.host)` 释放 |
| GitHubTarget | PASS | `github_target.py:162-169` `_claim()` / `_release_claims()` 使用 `policy.host_rate_limiter` |
| HostRateLimiter | PASS | 单例 per-repository；`prepare_request()` → `repository.acquire_host_request()` / `renew_host_request()` / `release_host_request()` |

**结论**: 三组件共享同一 `HostRateLimiter` 实例，底层同一 `Repository` + `host_rate_limits` 表。

### 7. Notification 的 at-least-once 语义是否被正确声明

| 维度 | 验证结果 | 证据 |
|------|----------|------|
| retry 机制 | PASS | `notification_dispatcher.py:134-168` `max_retries` + `base_backoff_sec * (2 ** (retries - 1))` |
| 状态持久化 | PASS | `finalize_notification_dispatch()` / `update_notification_status()` 持久化 `delivered` / `failed` / `retry_pending` |
| 幂等性 | PASS | `dispatch_token` + `finalize_notification_dispatch()` fencing；重复 dispatch 不会重复投递同一 claimed 通知 |
| 抑制/静音 | PASS | `AlertSilencer.should_silence()` 在发送前拦截；suppressed 状态持久化 |
| at-least-once 声明 | PASS | `NotificationDispatcher` 类 docstring 已明确声明 at-least-once external delivery，并说明 crash 后可能重复投递 |

**关键代码路径**:
- `notification_dispatcher.py:97-168` `dispatch_one()` — retry + backoff + fencing
- `notification_dispatcher.py:189-198` `fetch_pending()` — `claim_notifications()` fencing

**语义澄清**:
- Database-side fencing 防止 stale workers 篡改/释放不属于自己的 lease。
- 外部发送与数据库 finalization 之间不是 exactly-once：若外部渠道已接受但进程在 DB finalize 前 crash，另一个 worker 可能再次发送相同通知。
- 因此正确语义是：**at-least-once external delivery**，不是 exactly-once。

### 8. Docker 生产入口是否完整

| 维度 | 验证结果 | 证据 |
|------|----------|------|
| Dockerfile | PASS | `python:3.11-slim` 基础镜像；非 root 用户 `appuser:1000`；仅生产依赖 |
| entrypoint.sh | PASS | 创建 `/data` `/logs`；轻量环境检查；`exec "$@"` 信号透传 |
| docker_run.py | PASS | 配置校验 + 数据库校验 + `ScheduledRunner` 主循环 + SIGTERM/SIGINT graceful shutdown |
| 卷声明 | PASS | `/data` (DB) + `/logs` (日志) 必须由 orchestrator 提供 |
| 无开发依赖 | PASS | `pip install --no-deps -e .` + `requests pyyaml` 仅 |

**关键代码路径**:
- `Dockerfile:1-60` — 完整生产镜像定义
- `entrypoint.sh:1-25` — 轻量 guard
- `docker_run.py:1-103` — 配置/数据库/ graceful shutdown 完整闭环

### 9. 12 Part 是否存在遗漏或互相覆盖

| Part | 状态 | 说明 |
|------|------|------|
| 01 — Target Declarative Config | PASS | `targets.py` + `generic_web_target.py` 构造期校验；33 tests |
| 02 — Extraction / Normalize / Fingerprint / Diff | PASS | 4 新模块 + 72 tests；raw→selector→normalize→fingerprint→diff 链路 |
| 03 — Selector Missing 语义 | PASS | `EXTRACTION_FAILURE`；不触发删除事件；32 tests |
| 04 — GitHub API Adapter | PASS | 官方 API + ETag + 304 + dual-endpoint subresource state；25 tests |
| 05 — UnifiedPipeline 统一因果链 | PASS | 15 tests；Scheduler→Claim→Fetch→Policy→Finalize→Signal→Event→Investigation→Notification |
| 06 — 完整集成/恢复/并发/GA门控 | PASS | 1281 passed；0 架构越界 |
| 20-01 — Pipeline Finalization | PASS | 12/12 causality passed；signal_id placeholder 解析；1334 passed |
| 20-02 — Production Configuration | PASS | 12 项待办全部完成 |
| 20-03 — Docker / Production Packaging | PASS | 16 项待办全部完成 |
| 20-04 — Doctor / Observability | PASS | 9 项待办全部完成 |
| 20-05 — Recovery / Chaos / Acceptance | PASS | 24 项待办全部完成；18/18 acceptance passed |
| 20-06 — Final Global Re-Audit / GA Gate | PASS | 16 维度审计；3 P0 已修复；GA PASS |

**结论**: 12 Part 无遗漏、无互相覆盖。各 Part 边界清晰：
- Part 19-01~02: 基础设施（配置 + 提取/指纹）
- Part 19-03: 语义边界（selector missing）
- Part 19-04: 专有适配器（GitHub）
- Part 19-05~06: 统一管道 + 集成/并发
- Part 20-01~06: 生产就绪（finalization / config / docker / doctor / chaos / final audit）

---

## 二、GA 十问验证

### Q1: 为什么这个 Target 现在被抓？

**答案**: Target 被 `claim_targets()` 选中，因为：
- `status IN ('normal', 'recovering', 'cooldown')`
- `next_allowed_at IS NULL OR next_allowed_at <= now`
- `lease_until IS NULL OR lease_until < now`
- 按 `next_allowed_at ASC NULLS FIRST` 排序，取 `LIMIT ?`

**代码证据**: `repository.py:1413-1434`

### Q2: 为什么这个 Target 现在不能被抓？

**答案**: 任一条件阻止：
1. `TargetStatus` 不在 `('normal', 'recovering', 'cooldown')`
2. `next_allowed_at > now`（在 backoff/cooldown 保护期）
3. `lease_until >= now`（被其他 worker 持有有效 lease）

**代码证据**: `repository.py:1418-1423` WHERE 条件

### Q3: 上一次抓取是什么时候？

**答案**: `targets.last_fetched_at` 字段，由 `finalize_execution()` / `commit_target_execution()` 在每次成功/失败 finalization 时更新为 `now_iso`。

**代码证据**: `repository.py:1656` `last_fetched_iso = now_dt.isoformat()`

### Q4: 为什么这次产生 Signal？

**答案**: Signal 产生条件：
1. `fetch_policy.evaluate_response()` 返回 `should_emit_signal=True`（仅 200-299 成功 + 非 304）
2. Adapter 的 observation 层检测到真实内容变化（`compute_diff()` 返回 `changed=True`）
3. `EventCorrelator.process_signal()` 创建 `CorrelationPlan`，包含 `signals_to_persist`
4. `finalize_execution()` 原子持久化 Signal

**不产生 Signal 的场景**: 304 / 403 / 404 / 429 / 5xx / timeout / selector missing / empty after transform / same content / formatting-only change

**代码证据**:
- `fetch_policy.py:237-252` 304 → `should_emit_signal=False`
- `fetch_policy.py:253-270` 200-299 → `should_emit_signal=True`
- `generic_web_target.py:391` `emit_signal=bool(signals)`

### Q5: 为什么 Signal 被提升成 Event？

**答案**: `EventCorrelator.process_signal()`:
1. 调用 `_resolve_event_type()` 映射 `SignalType` → `EventType`
2. 调用 `_evaluate_importance()` 评估重要性
3. 查找 open event（`find_open_event_for_entity()`，24h 窗口）
4. 若 `open_event is None` → 创建新 Event
5. 若 `importance >= open_event.importance` → 更新 Event importance
6. 创建 `LinkToCreate(event_id, signal_id=-1)`，在 `finalize_execution()` 中解析为实际 signal_id

**代码证据**: `event_correlator.py:135-192`

### Q6: 为什么 Event 触发 Investigation？

**答案**: `EventCorrelator.dispatch_investigation()`:
1. `adapter.is_eligible(event)` — 检查 event type / status / importance 是否满足调查条件
2. `repository.get_investigation_result_by_event(event.id)` — 检查是否已调查过
3. `adapter.run_for_event(event, planner, engine)` — 执行调查
4. `repository.save_investigation_result()` — 持久化调查结果

**触发条件**: Event 必须 eligible + 无重复 investigation

**代码证据**: `event_correlator.py:230-281`

### Q7: Investigation 的证据在哪里？

**答案**: `repository.save_investigation_result()` 持久化到 `investigation_results` 表，包含：
- `investigation_id`
- `event_id`
- `task_type`
- `status`
- `summary`
- `metadata` (JSON)
- `evidence_items` (JSON array)

每次 `dispatch_investigation()` 返回 `True` 表示已持久化；返回 `False` 表示未触发或已存在。

**代码证据**: `event_correlator.py:256-275`

### Q8: 为什么最终发送/不发送 Notification？

**答案**: `NotificationDispatcher.dispatch_one()`:
1. `AlertSilencer.should_silence()` — 若抑制 → 标记 `suppressed`，不发送
2. `sender.send(notification)` — 实际投递
3. `success=True` → 标记 `delivered`
4. `success=False`:
   - `retries < max_retries` → 标记 `retry_pending`，指数退避
   - `retries >= max_retries` → 标记 `failed`
5. `fetch_pending()` 通过 `claim_notifications()` fencing 获取 pending notifications

**不发送场景**: suppressed / delivery failed / max retries exceeded / no pending notifications

**代码证据**: `notification_dispatcher.py:97-168`

### Q9: VPS/container 重启后状态还能恢复吗？

**答案**: 能。所有关键状态持久化在 SQLite：
- `targets` 表: `status` / `etag` / `last_modified` / `content_hash` / `consecutive_failures` / `last_fetched_at` / `next_allowed_at` / `lease_owner` / `lease_until` / `claim_token` / `metadata_json`
- `signals` 表: `entity_id` / `signal_type` / `observed_at` / `value` / `fingerprint`
- `events` 表: `entity_id` / `event_type` / `status` / `importance`
- `event_signals` 表: `event_id` / `signal_id`
- `investigation_results` 表: `event_id` / `task_type` / `status` / `summary` / `metadata` / `evidence_items`
- `notifications` 表: `event_id` / `channel` / `status` / `payload`
- `host_rate_limits` 表: `host` / `next_allowed_at` / `failure_count` / `consecutive_failures` / `claim_token` / `claimed_at` / `claim_until`

重启流程:
1. `docker_run.py:main()` → `_validate_database()` → `Repository._get_connection()` → schema 自动初始化
2. `ScheduledRunner.run_once()` → `reap_stale_claims()` 清除过期 lease
3. `claim_targets()` 根据 `targets` 表状态恢复调度

**代码证据**: `docker_run.py:46-52` / `repository.py:_init_target_table()` / `repository.py:_init_signal_tables()`

### Q10: 连续 429 一天系统会不会继续撞？

**答案**: 不会。429 处理链路：
1. `fetch_policy.evaluate_response()` 收到 429
2. `parse_retry_after()` 解析 `Retry-After`（秒数或 HTTP-Date）
3. `bounded_delay = max(1.0, min(retry_after_sec, retry_after_cap_sec=86400.0))`
4. 若 `failures >= max_consecutive_failures` 或 status 在 `(COOLDOWN, RECOVERING)` → 进入 `COOLDOWN`（阶梯 1800s）
5. 否则 → `BACKOFF` + deterministic jitter
6. `host_rate_limiter.update_after_response(host, next_allowed)` 更新 host-level 限制
7. `next_allowed_at` 持久化到 `targets` 表
8. 下次 `claim_targets()` 检查 `next_allowed_at <= now`，未到期则跳过

**保护机制**:
- per-target backoff + jitter
- per-host rate limit
- cooldown ladder (30min → 1h → 2h → 4h)
- `retry_after_cap_sec=86400.0`（上限 1 天）

**代码证据**: `fetch_policy.py:281-360`

---

## 三、GA 门控检查表

| 维度 | 状态 | 备注 |
|------|------|------|
| Pipeline | PASS | 单一路径：Scheduler→Claim→Fetch→Policy→Finalize→Signal→Event→Investigation→Notification |
| Configuration | PASS | `_validate_config()` + `_validate_database()` fail-fast |
| Docker | PASS | 非 root / 仅生产依赖 / graceful shutdown / 卷声明 |
| Doctor | PASS | `tests/test_doctor*.py` 覆盖；只读不修改业务状态 |
| Recovery | PASS | crash recovery / stale lease reap / state persistence |
| Concurrency | PASS | SQLite 保留锁 + fencing + atomic claim |
| Security | PASS | 无 FORBIDDEN 调用；无 anti-bot bypass；无 mock 工具残留 |
| Database | PASS | schema 安全 / migration 安全 / NULL/old data 兼容 |
| Vocabulary | PASS | 无 `FORBIDDEN` / `LEGACY` / `DEPRECATED` / `TEST_ONLY` / `MOCK_EVIDENCE_TIME` 残留 |
| Tests | PASS | **1372 passed / 0 failed** |
| Git | PASS | 干净工作树；无未提交生产代码 |
| VPS GroundTruth | PASS | 16 维度审计完成（Phase 20-06） |

---

## 四、非阻断项（P1）

| 项 | 严重度 | 说明 |
|----|--------|------|
| `fetch_service.py` TEST_ONLY 混在 `src/` | P1 | 建议移至 `tests/` 或标记 `TEST_ONLY` |
| 无 `schema_version` 表 | P1 | 当前 `CREATE TABLE IF NOT EXISTS` 足够；未来 migration 需要 |
| CLI worker 缺少 graceful shutdown | P1 | `docker_run.py` 已有；CLI 子命令未统一 |
| `NotificationStatus` 未使用强类型 Enum | **已修复** | 新增 `notification_status.py` `NotificationStatus(StrEnum)`；`models.py` / `pipeline.py` / `pipeline_runner.py` / `notification_enricher.py` / `notification_dispatcher.py` 已收紧边界；1372 tests passed |

---

## 五、最终结论

| 项目 | 状态 |
|------|------|
| **transaction / SQLite 副作用** | ✅ PASS — 单事务原子边界，无副作用 |
| **stale host claim 边界** | ✅ PASS — fencing 全覆盖，旧 token 零写权限 |
| **multi-worker race** | ✅ PASS — SQLite 保留锁 + atomic claim + fencing |
| **crash recovery** | ✅ PASS — lease 过期 + reap + 状态持久化 |
| **Target ↔ Host 两层状态冲突** | ✅ PASS — 分层检查，无隐藏共享状态 |
| **Host authority 统一** | ✅ PASS — GenericWeb / GitHub / Scheduler 共享同一 `HostRateLimiter` |
| **Notification at-least-once** | ✅ PASS — 代码实现 + 类 docstring 已明确声明 at-least-once external delivery |
| **Docker 生产入口** | ✅ PASS — 完整镜像 + entrypoint + graceful shutdown |
| **12 Part 覆盖** | ✅ PASS — 无遗漏、无覆盖、边界清晰 |
| **GA 十问** | ✅ 10/10 可回答 |
| **全量测试** | ✅ 1372 passed / 0 failed |
| **Git 状态** | ✅ 干净，无未提交工作 |

**最终状态**: **GA ACCEPT**

所有技术验证通过，报告状态已统一。代码层面无新的 P0，文档层面已补全 Notification 模型语义文档及 at-least-once 语义声明，并收紧 `NotificationStatus` enum boundary。

---

*报告生成时间: 2026-08-20*
*验证方法: 源码级 grep/read + 记忆库 12 Part 合同交叉验证 + 全量 pytest 1372 passed*
