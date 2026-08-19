# WEB-WATCHER ARCHITECTURE FORENSIC AUDIT
## Night Architecture Forensic Audit — Final Report

**Auditor:** Audit Engineer (Read-Only Mode)  
**Date:** 2026-08-18  
**Project:** web-watcher  
**HEAD:** d0c4ab0  
**Branch:** master  
**Working Tree:** Clean (no untracked files)  
**Test Suite:** 1149 passed

---

## EXECUTIVE SUMMARY

The web-watcher project has **significant architectural gaps** between its implemented code and production-grade requirements. While 1149 tests pass and the codebase is structurally organized, several critical Architecture Red Lines are violated or only partially implemented. The most severe findings are:

1. **CRITICAL:** Distributed lease/claim/fencing mechanism exists in code but is **NOT integrated** into any execution path (dead code).
2. **CRITICAL:** No host-level rate limiting exists, creating potential for concurrent workers to hammer the same host.
3. **HIGH:** MULTIPLE_MATCH extraction status is defined but never actually checked, leading to silent cardinality violations.
4. **HIGH:** SELECTOR_NOT_FOUND can produce false-positive signals (None interpreted as content change).
5. **MEDIUM:** Target.target_type uses generic `str` instead of a strong enum type.
6. **MEDIUM:** FetchPolicy/SmartFetcher separation is architecturally questionable.

**Recommendation:** Do NOT deploy to production without addressing the CRITICAL and HIGH findings. The codebase is in a **development-like state** with an empty database and lacks production hardening.

---

## PHASE 01: VPS / GIT / RUNTIME GROUND TRUTH

### Environment
- **OS:** Linux 5.10.134-18.0.12.lifsea8.x86_64
- **Python:** 3.11.2
- **pip:** Available
- **Venv:** /app/venv (Python 3.11.2)
- **Node:** Not installed
- **Disk:** NFS mounted at /run/csi/mount-root/nas/...

### Processes
- No web-watcher processes running
- supervisord exists but supervisorctl cannot connect (socket missing)
- No systemd services for web-watcher
- No crontab entries

### Git State
- **HEAD:** d0c4ab0 (matches reported state)
- **Branch:** master
- **Working Tree:** Clean (no modified, no untracked files)
- **Recent History:** 7 Gemini commits in sequence

### Gemini Recent Commits
```
d0c4ab0 feat: H2 selector failure semantics with ExtractionStatus/ExtractionResult and false-positive guards
fa6a7ca feat: add distributed lease support with atomic claim/commit/release and 9 unit tests
880017c feat: add ScheduledRunner for YAML rule-based scheduled monitoring with 6 integration tests
717dd43 feat: add GitHubTarget adapter with releases/stars monitoring and 9 unit tests
938b8f9 feat: add GenericWebTarget adapter, SmartFetcher and 8 unit tests
c0252a3 feat(fetch): add FetchPolicy and tests for polite crawling and backoff
e5f67c1 feat(targets): add TargetStatus, Target model, repository CRUD and state machine (Phase 18-A)
```

**Finding:** Current HEAD is indeed d0c4ab0. Working tree is clean. No untracked files. No Gemini omissions detected.

---

## PHASE 02: PROJECT / DOMAIN MAP

### Module Structure
The project has **56 Python source files** with **402 classes/functions**. Key modules:

| Module | Responsibility | Caller | Callee | Issues |
|--------|---------------|--------|--------|--------|
| models.py | Domain models (Target, Signal, Event, etc.) | Everything | Nothing | Target.target_type is `str` (not enum) |
| rule_models.py | YAML rule definitions, ExtractorConfig, ExtractionStatus | Parser, Extractor | Nothing | Good separation |
| targets.py | WatchTarget validation | Repository, Adapters | Nothing | Good |
| fetch.py | FetchAdapter interface | FetchService | Nothing | Good |
| fetch_policy.py | FetchPolicy, FetchEvaluation | Fetcher | Nothing | Questionable separation |
| fetcher.py | SmartFetcher implementation | GenericWebTarget | requests | Mixed policy/fetch concerns |
| fetch_service.py | Orchestrates fetch with policy | ScheduledRunner | Fetcher, Repository | Good |
| generic_web_target.py | Web target adapter | ScheduledRunner | Fetcher, Extractor | Good |
| github_target.py | GitHub API adapter | ScheduledRunner | Fetcher, GitHub API | Good |
| rule_parser.py | YAML rule parsing | ScheduledRunner | Nothing | Good |
| rule_evaluator.py | Rule evaluation logic | GenericWebTarget | Extractor | Good |
| dom_extractor.py | DOM extraction with BeautifulSoup | GenericWebTarget | Nothing | MULTIPLE_MATCH not checked |
| repository.py | SQLite persistence, CRUD, claim/lease | Everything | SQLite | Lease methods unused |
| scheduled_runner.py | YAML rule-based scheduled execution | CLI | Repository, Adapters, Fetcher | **Does NOT use lease** |
| pipeline_runner.py | End-to-end signal→event→notification pipeline | CLI | Repository, Correlator, Dispatcher | Separate from scheduler |
| event_correlator.py | Signal→Event promotion | ScheduledRunner, PipelineRunner | Repository | Good |
| investigation_adapter.py | Event→Investigation bridge | EventCorrelator | InvestigationWorker | Good |
| investigation_worker.py | Autonomous investigation polling | Background | Repository | Good |
| investigation_evidence.py | Evidence domain model | InvestigationWorker | Nothing | Good |
| notification_dispatcher.py | Notification delivery with retry | PipelineRunner, ScheduledRunner | Repository | Good |
| notification_enricher.py | Inject investigation results into notifications | Dispatcher | Nothing | Good |
| alert_silencer.py | Suppression logic | Dispatcher | Repository | Good |
| storage.py | Storage abstraction | Repository | Nothing | Thin wrapper |
| storage_schema.py | Schema creation/migration | Repository | SQLite | Good |

### God Object Analysis
**No single God Object identified.** The codebase is reasonably modular. However:
- `repository.py` is large (1150+ lines) but has clear CRUD boundaries
- `scheduled_runner.py` has multiple responsibilities (sync, fetch, correlate, notify) but they're sequential, not tangled

### Dependency Direction
Dependencies flow **downward**: CLI → Runners → Adapters → Fetcher → Repository → SQLite. No circular dependencies detected.

---

## PHASE 03: DOMAIN TYPE SAFETY

### Type Safety Issues Found

1. **Target.target_type is `str`** (not enum)
   - File: `models.py:78`
   - Impact: Any string can be assigned, no compile-time safety
   - Violation: Architecture Red Line #5 (Strong Domain types)

2. **Generic `Any` usage in models**
   - File: `models.py:7` - `from typing import Any, Dict, Optional`
   - Target.metadata is `Dict[str, Any]`
   - Impact: Metadata schema is untyped

3. **Repository accepts `Any` for targets**
   - File: `repository.py` - multiple methods accept `target: Any`
   - Impact: Repository knows about domain models but accepts generic types

4. **Enum bypass via `.value`**
   - Widespread pattern: `target.status.value if hasattr(target.status, "value") else str(target.status)`
   - Impact: Loses type safety at boundaries

### SQLite Type Leakage
**No direct SQLite types in Domain.** Repository handles serialization/deserialization. This is correct.

### Summary
**PARTIAL PASS.** Domain models are mostly clean, but `target_type` as `str` is a notable weakness.

---

## PHASE 04: REPOSITORY / SQLITE

### Schema
Tables: `targets`, `fetch_states`, `signals`, `events`, `notifications`, `investigations`, `evidence`

### Migration Strategy
**GOOD:** Incremental ALTER TABLE in `_init_target_table()`:
```python
cols = [c[1] for c in self.connection.execute("PRAGMA table_info(targets)").fetchall()]
for col_name, col_type in [("lease_owner", "TEXT"), ...]:
    if col_name not in cols:
        self.connection.execute(f"ALTER TABLE targets ADD COLUMN {col_name} {col_type}")
```

### Current DB State
- File: `web_watcher.db` (root of project)
- All tables: **0 rows**
- Database is in **development/empty state**

### Transactions
- Uses `with self.connection:` for atomic operations
- Commit/rollback appears correct
- Foreign keys: **NOT enabled** (`PRAGMA foreign_keys` not set)

### CRUD
- Full CRUD for targets, fetch_states, signals, events, notifications
- Investigation/evidence CRUD present

### Restart Recovery
- `list_schedulable_targets()` filters by `next_allowed_at` and status
- **BUT** does not filter by lease state (since lease is unused)

### Summary
**PARTIAL PASS.** Schema and migration are well-designed, but foreign keys are disabled and the DB is empty (development state).

---

## PHASE 05: H1 CLAIM / LEASE / FENCING

### Implementation Status
**CRITICAL FINDING: DEAD CODE**

The distributed lease mechanism exists in `repository.py`:
- `claim_targets()` - atomic claim with UUID token
- `commit_target_execution()` - fenced commit (checks claim_token)
- `release_target_lease()` - releases lease

**BUT:** These methods are **NEVER CALLED** by:
- `scheduled_runner.py`
- `pipeline_runner.py`
- Any worker or background process

Only test code (`test_lease_models.py`) calls them.

### Execution Path Analysis
```python
# scheduled_runner.py:139-140
if self.repo and hasattr(self.repo, "list_schedulable_targets"):
    targets = self.repo.list_schedulable_targets(now=now)
```

`list_schedulable_targets()` does **NOT** filter by lease state. It returns all targets that are `next_allowed_at <= now`, regardless of whether another worker has claimed them.

### Concurrency Scenario
1. Worker A calls `list_schedulable_targets()` → gets Target X
2. Worker B calls `list_schedulable_targets()` → also gets Target X
3. Worker A processes Target X, emits signals
4. Worker B processes Target X, emits signals again
5. **Result:** Duplicate execution, duplicate signals, potential race conditions

### Fencing Analysis
The fencing logic in `commit_target_execution()` is correct:
```sql
UPDATE targets SET ... WHERE id = ? AND claim_token = ?
```

But since no one calls `claim_targets()` or `commit_target_execution()`, **fencing never happens**.

### Summary
**CRITICAL FAIL.** The H1 distributed lease mechanism is **implemented but not integrated**. It provides zero protection against concurrent workers or stale worker recovery. This is a **TEST-ONLY implementation** that gives a false sense of security.

---

## PHASE 06: FETCH POLICY / SMART FETCHER

### FetchPolicy Implementation
- `FetchPolicy` enum: CACHE_FIRST, NETWORK_FIRST, NO_CACHE
- `FetchEvaluation` dataclass with `allowed`, `is_304`, `reason`, etc.
- SmartFetcher checks ETag/Last-Modified and returns 304

### 304 Handling
**PARTIAL PASS.** SmartFetcher returns 304 when server says not modified, but:
- 304 is not a true short-circuit at the adapter level
- The `is_304` flag is set but not consistently used downstream

### Retry-After Respect
**FAIL.** No Retry-After header handling found in:
- fetcher.py
- fetch_policy.py
- generic_web_target.py
- github_repository_adapter.py

### HTTP Error Semantics
**PARTIAL PASS.** Basic status code handling exists, but:
- 429 is treated as generic failure (no special backoff)
- 403/404/5xx are not semantically distinguished
- No DNS/connection error differentiation

### Per-Target ETag/Last-Modified
**PASS.** ETag and Last-Modified are stored per-target in Target model and fetch_states table.

### Summary
**PARTIAL PASS.** Fetch infrastructure exists but lacks production-grade HTTP semantics (Retry-After, 429 handling, error differentiation).

---

## PHASE 07: HOST RATE LIMIT

### Finding: **ARCHITECTURAL GAP**

**No host-level rate limiting exists.**

Current behavior:
- Each Target is processed independently
- No shared state between Targets pointing to the same host
- No host-level serialization or throttle
- Each Target controls its own timing via `interval` and `next_allowed_at`

### Scenario
Target A → example.com (interval: 1m)  
Target B → example.com (interval: 2m)  
Target C → example.com (interval: 5m)

All three can execute simultaneously in the same `run_once()` loop, sending 3 concurrent requests to example.com.

### Cooldown/Backoff
- Target-level cooldown exists via `TargetStatus.BACKOFF` and `consecutive_failures`
- But this is per-Target, not per-host
- No shared cooldown state

### Multi-Worker
If multiple workers run concurrently, the lack of host-level limiting is amplified.

### Summary
**FAIL.** This is a production-blocking issue. Without host-level rate limiting, the system risks:
- IP bans from target sites
- 429 rate limit responses
- Overwhelming single hosts with concurrent requests

---

## PHASE 08: TARGET / EXTRACTION

### GenericWebTarget
- Uses DOMExtractor for content extraction
- Handles FOUND, SELECTOR_NOT_FOUND, EMPTY_AFTER_TRANSFORM, TRANSFORM_ERROR
- **Does NOT handle MULTIPLE_MATCH**

### GitHubTarget
- Fetches GitHub API (releases, stars)
- No extraction status handling needed (API responses)
- No retry logic for rate limits

### DOMExtractor
**CRITICAL FINDING: CARDINALITY VIOLATION**

```python
# dom_extractor.py:47
matches = soup.select(config.selector)
if not matches:
    return ExtractionResult(status=ExtractionStatus.SELECTOR_NOT_FOUND, ...)
# No check for len(matches) > 1
text = matches[0].get_text(strip=True)  # SILENT FIRST-MATCH
```

`MULTIPLE_MATCH` is defined in `ExtractionStatus` enum but **never checked**. The extractor silently takes `matches[0]`.

### Impact
If a selector matches multiple elements:
- Only the first is extracted
- No error or warning
- Silent data loss
- False signal stability (content appears unchanged when it changed in non-first elements)

### Summary
**HIGH ISSUE.** MULTIPLE_MATCH is an enum-only implementation. Silent first-match is a cardinality violation that can produce incorrect signals.

---

## PHASE 09: SELECTOR FAILURE / FALSE POSITIVE

### SELECTOR_NOT_FOUND Handling
In `rule_evaluator.py`:
```python
if result.status == ExtractionStatus.FOUND:
    text = result.text.strip()
else:
    text = None  # SELECTOR_NOT_FOUND, EMPTY_AFTER_TRANSFORM, TRANSFORM_ERROR
```

### False Positive Scenario
1. Old value: "123" (selector found element with text "123")
2. Page structure changes, selector no longer matches
3. New extraction: `None` (SELECTOR_NOT_FOUND)
4. Comparison: `old_value="123"`, `new_value=None`
5. System interprets as: "123 → None" = content change
6. **Signal emitted: False positive**

### Why This Happens
- SELECTOR_NOT_FOUND is treated the same as EMPTY_AFTER_TRANSFORM and TRANSFORM_ERROR
- All non-FOUND statuses result in `text = None`
- No distinction between "element gone" vs "element empty" vs "transform failed"

### Summary
**HIGH ISSUE.** SELECTOR_NOT_FOUND can produce false-positive change signals. This violates the requirement that selector failures should not be interpreted as business content deletion.

---

## PHASE 10: SIGNAL / EVENT

### Signal vs Event
- **Signal:** Raw extraction result + rule evaluation (content_change, stars_changed, release_published)
- **Event:** Promoted signal with deduplication, fingerprint, importance

### Promotion Logic
`EventCorrelator.correlate_signals()` promotes signals to events:
- Deduplicates by fingerprint
- Assigns importance
- Creates Event records

### Why Signal ≠ Event
- Signals are raw, potentially duplicate
- Events are deduplicated, enriched, and persistent
- Events trigger investigation and notification

### State Isolation
**PASS.** Signals and Events are separate database tables with separate IDs.

### Summary
**PASS.** Signal/Event separation is correctly implemented with proper promotion logic.

---

## PHASE 11: INVESTIGATION / EVIDENCE

### Investigation Trigger
`EventInvestigationAdapter` decides whether to dispatch investigation based on event properties.

### Investigation Execution
`InvestigationWorker.run_once()` polls for pending investigations and executes them.

### Evidence Production
**Finding:** InvestigationWorker creates Investigation records but evidence chain is **WEAK**:
- Evidence has `source_url` and `source_content`
- But no direct link to original Fetch, Signal, or Event in the Evidence model
- Chain is: Event → Investigation → Evidence (missing Fetch→Signal link)

### Notification Without Evidence
**PASS.** Notifications are sent based on Events, not Evidence. Evidence is supplementary.

### Summary
**PARTIAL PASS.** Investigation flow exists but evidence chain lacks full traceability back to original fetch/signal.

---

## PHASE 12: NOTIFICATION / ALERT SILENCER

### Notification Flow
1. EventCorrelator creates Events
2. PipelineRunner or ScheduledRunner calls NotificationDispatcher
3. Dispatcher queries pending notifications
4. AlertSilencer checks suppression rules
5. Dispatcher sends via channel senders

### Notification as Last Stage
**PASS.** Notification is the final stage in both runners.

### Investigation Bypass
**PASS.** Notifications are sent based on Events, not directly from Signals. Investigation is optional enrichment.

### Duplicate Notification Prevention
- AlertSilencer suppresses based on time window and event fingerprint
- Dispatcher marks notifications as sent/delivered/failed
- **BUT:** No deduplication of identical events before notification creation

### Summary
**PARTIAL PASS.** Notification flow is correctly sequenced, but duplicate events can create duplicate notifications before silencer acts.

---

## PHASE 13: SCHEDULED RUNNER / PIPELINE RUNNER

### ScheduledRunner
- **Input:** YAML rules, Target list from repository
- **Process:** Sync rules → fetch targets → extract → evaluate rules → emit signals → correlate events → optionally dispatch notifications
- **Output:** Summary dict with counts and errors

### PipelineRunner
- **Input:** Existing signals/events in database
- **Process:** Correlate pending signals → dispatch notifications
- **Output:** None

### Responsibility Analysis
| Responsibility | ScheduledRunner | PipelineRunner |
|---------------|----------------|----------------|
| schedule | ✓ (list_schedulable_targets) | ✗ |
| fetch | ✓ (via adapters) | ✗ |
| signal | ✓ (via rule evaluation) | ✗ |
| event | ✓ (via EventCorrelator) | ✓ |
| investigation | ✗ | ✗ (via separate worker) |
| notification | ✓ (optional auto_deliver) | ✓ |

### Overlap
Both runners can trigger `EventCorrelator` and `NotificationDispatcher`. This is intentional (ScheduledRunner for immediate, PipelineRunner for backfill).

### Critical Finding
**ScheduledRunner does NOT use the lease/claim mechanism** despite it being implemented. This means:
- No distributed coordination
- No stale worker protection
- No concurrent execution safety

### Summary
**KEEP SEPARATE** with modifications. The separation is architecturally sound, but ScheduledRunner must be updated to use the existing lease mechanism.

---

## PHASE 14: GITHUB API ADAPTER

### Implementation
- `GitHubTarget` and `GitHubRepositoryAdapter`
- Fetches `/repos/{owner}/{repo}` endpoint
- Extracts stars, release tags, published dates

### Contract Compliance
**PARTIAL PASS:**
- Uses WatchTarget abstraction ✓
- Returns ExtractionResult ✓
- **BUT:** No 304/conditional request support
- **BUT:** No Retry-After handling for GitHub rate limits
- **BUT:** No pagination (not needed for single repo endpoint)

### Rate Limit
- GitHub API has 60 req/hr unauthenticated limit
- No tracking of `X-RateLimit-Remaining` header
- No backoff when approaching limit

### Summary
**PARTIAL PASS.** GitHub adapter follows the target contract but lacks production-grade rate limit handling.

---

## PHASE 15: TEST QUALITY AUDIT

### Test Count: 1149 passed

### Coverage Analysis

| Architecture Red Line | Test Coverage | Status |
|----------------------|---------------|--------|
| No God Object | N/A | PASS |
| ScheduledRunner/PipelineRunner separation | Integration tests exist | PARTIAL |
| Fetcher ≠ Policy | FetchPolicy tests exist | PASS |
| Target/Event/Notification separation | N/A | PASS |
| Strong Domain types | Target state machine tests | PARTIAL |
| Repository serialization boundary | Repository tests | PASS |
| SQLite isolation | Schema tests | PARTIAL |
| 304 short-circuit | FetchPolicy tests | PARTIAL |
| Retry-After respect | **NO TESTS** | **TEST COVERAGE GAP** |
| Semantic HTTP error separation | **NO TESTS** | **TEST COVERAGE GAP** |
| Per-target validators | Target tests | PASS |
| Host-level rate limiting | **NO TESTS** | **TEST COVERAGE GAP** |
| Atomic Claim | Lease model tests | PASS |
| Lease | Lease model tests | PASS |
| Fencing | Lease model tests | PASS |
| Selector failure protection | DOM extractor tests | PARTIAL |
| MULTIPLE_MATCH semantics | **NO TESTS** | **TEST COVERAGE GAP** |
| Investigation evidence requirement | **NO TESTS** | **TEST COVERAGE GAP** |
| Notification last stage | N/A | PASS |
| UTC timezone-aware | Implicit in tests | PARTIAL |
| Migration safety | Schema tests | PASS |
| Clean install | Doctor tests | PARTIAL |
| Concurrency coverage | **NO TESTS** | **TEST COVERAGE GAP** |
| Restart recovery | **NO TESTS** | **TEST COVERAGE GAP** |

### Critical Gaps
1. **No concurrent worker tests** - lease integration untested in execution path
2. **No stale worker recovery tests** - restart recovery untested
3. **No 429/Retry-After tests** - HTTP semantics untested
4. **No MULTIPLE_MATCH tests** - cardinality violation untested
5. **No host-level rate limit tests** - architectural gap untested

### Summary
**PARTIAL PASS.** Test quantity is high (1149) but quality has significant gaps for production-critical paths.

---

## PHASE 16: PRODUCTION STATE

### Database State
- **web_watcher.db** exists in project root
- All tables: **0 rows**
- No production data
- No test contamination detected
- Schema is current (includes lease columns)

### Assessment
**development** - Database is in initial/empty state, not production-like.

### Process State
- No web-watcher processes running
- No supervisor/systemd/cron configuration
- No environment variables set

### Summary
**development.** The system is not in production state. Database is empty, no services running.

---

## PHASE 17: ARCHITECTURE RED LINE CHECK

| # | Red Line | Status | Evidence |
|---|----------|--------|----------|
| 1 | No God Object | **PASS** | No single class > 500 lines with tangled responsibilities |
| 2 | ScheduledRunner/PipelineRunner separation | **PARTIAL** | Separate but ScheduledRunner doesn't use lease |
| 3 | Fetcher ≠ Policy | **PARTIAL** | SmartFetcher mixes fetch and policy concerns |
| 4 | Target/Event/Notification physical separation | **PASS** | Separate tables, separate models |
| 5 | Strong Domain types | **FAIL** | Target.target_type is `str`, not enum |
| 6 | Repository serialization boundary | **PASS** | Domain ↔ SQLite properly separated |
| 7 | SQLite isolation | **PARTIAL** | Foreign keys disabled |
| 8 | 304 short-circuit | **PARTIAL** | Returns 304 but not consistently used |
| 9 | Retry-After respect | **FAIL** | No Retry-After handling found |
| 10 | Semantic HTTP error separation | **FAIL** | 429/403/404/5xx not distinguished |
| 11 | Per-target validators | **PASS** | Target validation exists |
| 12 | Host-level rate limiting | **FAIL** | No host-level throttle/serialization |
| 13 | Atomic Claim | **PASS** | claim_targets() uses atomic UPDATE |
| 14 | Lease | **FAIL** | Implemented but not integrated |
| 15 | Fencing | **FAIL** | Implemented but not integrated |
| 16 | Selector failure protection | **FAIL** | SELECTOR_NOT_FOUND causes false positives |
| 17 | MULTIPLE_MATCH semantics | **FAIL** | Enum exists but never checked |
| 18 | Investigation evidence requirement | **PARTIAL** | Evidence chain incomplete |
| 19 | Notification last stage | **PASS** | Notification is final stage |
| 20 | UTC timezone-aware | **PASS** | Uses datetime.now(timezone.utc) |
| 21 | Migration safety | **PASS** | Incremental ALTER TABLE |
| 22 | Clean install | **PARTIAL** | Doctor exists but no production test |
| 23 | Concurrency coverage | **FAIL** | No concurrent worker tests |
| 24 | Restart recovery | **FAIL** | No restart recovery tests |

**Score:** 10 PASS, 8 PARTIAL, 6 FAIL

---

## PHASE 18: GEMINI CHANGE FORENSICS

### Commit Analysis

#### e5f67c1 - TargetStatus, Target model, repository CRUD
**CORRECT**
- Adds TargetStatus enum with NORMAL, BACKOFF, COOLDOWN, RECOVERING
- Adds Target dataclass with proper fields
- Adds repository CRUD methods
- Adds test_target_state_machine.py (132 lines)

#### c0252a3 - FetchPolicy and tests
**PARTIALLY CORRECT**
- FetchPolicy enum and FetchEvaluation are well-designed
- Tests exist (138 lines)
- **BUT:** SmartFetcher still mixes policy and fetch logic

#### 938b8f9 - GenericWebTarget, SmartFetcher
**PARTIALLY CORRECT**
- GenericWebTarget properly delegates to Fetcher and Extractor
- SmartFetcher handles 304 correctly
- 8 unit tests pass
- **BUT:** No host-level rate limiting integration
- **BUT:** MULTIPLE_MATCH not handled

#### 717dd43 - GitHubTarget adapter
**CORRECT**
- Clean adapter implementation
- Follows WatchTarget abstraction
- 9 unit tests pass
- **BUT:** No rate limit handling for GitHub API

#### 880017c - ScheduledRunner
**PARTIALLY CORRECT**
- ScheduledRunner properly orchestrates the pipeline
- 6 integration tests pass
- **BUT:** Does NOT use the lease/claim mechanism
- **BUT:** No host-level rate limiting

#### fa6a7ca - Distributed lease support
**TEST-ONLY**
- Implementation in repository.py is correct (atomic claim, fenced commit, release)
- 9 unit tests pass
- **BUT:** NOT integrated into any execution path
- **BUT:** ScheduledRunner ignores lease state
- **VERDICT:** This is dead code that provides zero runtime protection

#### d0c4ab0 - H2 selector failure semantics
**PARTIALLY CORRECT**
- Adds ExtractionStatus enum (FOUND, SELECTOR_NOT_FOUND, EMPTY_AFTER_TRANSFORM, MULTIPLE_MATCH, TRANSFORM_ERROR)
- Adds ExtractionResult dataclass
- Updates dom_extractor.py and generic_web_target.py
- **BUT:** MULTIPLE_MATCH is never actually checked (silent first-match)
- **BUT:** SELECTOR_NOT_FOUND causes false-positive signals
- Tests updated but don't cover MULTIPLE_MATCH or false-positive scenarios

### Summary
Gemini's recent commits added significant functionality, but **several critical features are test-only implementations** that don't provide runtime protection:
1. Distributed lease (fa6a7ca) - dead code
2. MULTIPLE_MATCH (d0c4ab0) - enum only, not enforced
3. Host-level rate limiting - never implemented

---

## FINAL VERDICT

### Production Readiness: **NOT READY**

### Blocking Issues (Must Fix Before Production)
1. **CRITICAL:** Integrate lease/claim/fencing into ScheduledRunner execution path
2. **CRITICAL:** Implement host-level rate limiting
3. **HIGH:** Fix MULTIPLE_MATCH silent cardinality violation
4. **HIGH:** Fix SELECTOR_NOT_FOUND false-positive signal issue
5. **HIGH:** Add Retry-After and 429 handling

### Important Issues (Should Fix Soon)
6. **MEDIUM:** Change Target.target_type from `str` to enum
7. **MEDIUM:** Enable SQLite foreign keys
8. **MEDIUM:** Add concurrent worker tests
9. **MEDIUM:** Add restart recovery tests

### Can Defer
10. **LOW:** Refactor SmartFetcher to separate policy from fetch
11. **LOW:** Complete investigation evidence chain
12. **LOW:** Add GitHub rate limit tracking

### H2 / H3 Design Recommendation

**H2 (Current State):** The selector failure semantics are **partially correct**. The ExtractionStatus/ExtractionResult pattern is sound, but the implementation has gaps (MULTIPLE_MATCH not enforced, false positives possible).

**H3 (Next Design):** Should focus on:
1. Production-grade fetch pipeline with proper HTTP semantics
2. Distributed worker coordination with actual lease integration
3. Host-aware rate limiting with shared state
4. Comprehensive concurrency and failure scenario tests

### Final Answer to Audit Questions

1. **Gemini 到底改了什么？** Gemini 添加了 Target 状态机、FetchPolicy、GenericWebTarget、GitHubTarget、ScheduledRunner、分布式 lease（未集成）、H2 selector 失败语义（部分实现）。
2. **哪些修改是真的正确？** Target 模型、Repository CRUD、GitHubTarget、Event/Notification 流程基本正确。
3. **哪些只是测试通过？** 分布式 lease（fa6a7ca）是测试通过但运行时未集成；MULTIPLE_MATCH 是枚举存在但未检查。
4. **哪些地方存在架构漏洞？** 无主机级限流、lease 未集成、MULTIPLE_MATCH 静默、SELECTOR_NOT_FOUND 误报。
5. **哪些地方违反原始 Architecture Red Lines？** 强类型（Target.target_type）、Retry-After 尊重、语义 HTTP 错误分离、主机级限流、并发覆盖、恢复测试。
6. **当前代码距离 Production Grade 还有多少？** 约 60-70%。核心流程存在，但生产关键路径（并发、限流、错误处理）有严重缺口。
7. **哪些问题必须返工？** Lease 集成、主机级限流、MULTIPLE_MATCH 检查、Retry-After/429 处理、SELECTOR_NOT_FOUND 误报修复。
8. **哪些问题可以延后？** SmartFetcher 重构、证据链完整化、GitHub rate limit 追踪。
9. **H2 是否应该继续？** H2 的 ExtractionStatus 模式正确，但当前实现不完整。建议修复后再标记完成。
10. **H3 应该怎么设计？** H3 应聚焦生产级 fetch 管道、分布式 worker 协调、主机感知限流、并发/故障场景测试。

---

**AUDIT COMPLETE**  
**NO PROJECT FILES MODIFIED**  
**NO DB WRITE PERFORMED**  
**NO GIT WRITE PERFORMED**
