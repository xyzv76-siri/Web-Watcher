# Web Watcher Architecture Review v2 — READ-ONLY AUDIT

**Date:** 2026-08-17
**Auditor:** default agent (read-only)
**Scope:** All files under `src/web_watcher/`, `tests/`, `config/`, `reports/`
**Constraint:** No source/test/config modification. No external calls. No secrets read.

---

## PART 0 — Baseline Verification

| Claim | Verification |
|-------|-------------|
| QwenPaw 2.1.0 | ✅ Confirmed (`qwenpaw --version`) |
| Python 3.11.2 | ✅ Confirmed |
| 355/355 tests | ✅ Confirmed (2026-08-17T06:35:02Z run) |
| Git branch master | ✅ Confirmed |
| 15 historical commits | ✅ Confirmed (`git log --oneline -15`) |
| 7 Phase 10A/10B files untracked | ✅ Confirmed |
| 0 tracked modifications | ✅ Confirmed |

---

## PART 1 — Current Architecture: Module Map

### Tier 1: Domain Models & Pure Data

#### `src/web_watcher/models.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Define all domain entities as frozen dataclasses |
| Models | `Entity`, `Signal`, `Event`, `Notification`, `FetchState` |
| Inputs | None (data containers only) |
| Outputs | Frozen dataclass instances |
| Dependencies | `dataclasses`, `datetime`, `typing` |
| Side effects | None |
| Deterministic | ✅ Yes |
| Network IO | ❌ No |

**Event fields:** `id, entity_id, event_type, status, importance, created_at, updated_at`
- `importance` is a **str** (not the `Importance` enum from `policy.py`)
- `event_type` is a **str** — used by both `PolicyEngine` and `EventCorrelator`

---

### Tier 2: Persistence Layer

#### `src/web_watcher/storage_schema.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Raw SQLite schema as a SQL string constant |
| Tables | `entities`, `signals`, `events`, `event_signals`, `notifications`, `fetch_state` |
| Inputs | None (constant) |
| Outputs | `SCHEMA` string |
| Dependencies | `builtins` only |
| Side effects | None |
| Deterministic | ✅ Yes |
| Network IO | ❌ No |

**Key constraint:** `signals` table has `UNIQUE(entity_id, signal_type, fingerprint)`.
`notifications` table has `UNIQUE(event_id, channel)`.

#### `src/web_watcher/storage.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | SQLite connection management |
| Functions | `open_database(path)`, `initialize_schema(conn)`, `init_schema(conn)` |
| Inputs | `path` (filesystem path to DB file) |
| Outputs | `sqlite3.Connection` |
| Dependencies | `sqlite3`, `pathlib`, `.storage_schema` |
| Side effects | ✅ Creates parent directories, opens DB connection, executes schema DDL |
| Deterministic | ❌ No (filesystem-dependent) |
| Network IO | ❌ No |

#### `src/web_watcher/repository.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | All persistence operations (CRUD) |
| Tables | `entities`, `signals`, `events`, `event_signals`, `fetch_state` |
| Inputs | Domain objects / scalars |
| Outputs | Domain objects (`Entity`, `Event`, `Signal`, `FetchState`) or `Optional` variants |
| Dependencies | `sqlite3`, `.models`, `.storage` |
| Side effects | ✅ All methods mutate the SQLite database |
| Deterministic | ❌ No (depends on DB state) |
| Network IO | ❌ No |

**Key methods:**
- `create_entity(canonical_key, name, entity_type) → Entity` — INSERT, returns new Entity
- `get_entity_by_key(canonical_key) → Optional[Entity]` — SELECT
- `get_or_create_entity(...) → Entity` — upsert by canonical_key
- `create_signal(entity_id, signal_type, observed_at, value, fingerprint) → Optional[Signal]` — INSERT; returns `None` on UNIQUE conflict (duplicate fingerprint)
- `create_event(entity_id, event_type, status="open", importance="medium") → Event` — **writes Event.importance**
- `get_event(event_id) → Optional[Event]`
- `update_event(event_id, status, importance, updated_at) → Optional[Event]`
- `attach_signal_to_event(event_id, signal_id) → bool` — INSERT into junction table; returns `False` on duplicate
- `find_open_event_for_entity(entity_id, cutoff) → Optional[Event]` — finds latest open event
- `get_fetch_state(target_key) → Optional[FetchState]`
- `upsert_fetch_state(state: FetchState) → FetchState` — `INSERT OR REPLACE`

---

### Tier 3: Fetch & Source Layer

#### `src/web_watcher/fetch.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Define Fetch/Adapter **contracts** only (Protocol types) |
| Types | `FetchRequest`, `FetchResult`, `Fetcher`, `SourceAdapter`, `select_adapter()` |
| Inputs | `WatchTarget` |
| Outputs | Protocols (interface-only) |
| Dependencies | `dataclasses`, `datetime`, `typing`, `.targets` |
| Side effects | ❌ None (pure interface module) |
| Deterministic | ✅ Yes |
| Network IO | ❌ No |

`select_adapter()` raises `LookupError` if zero or multiple adapters match a target.

#### `src/web_watcher/adapters.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Adapter registry — holds adapters, resolves by target type |
| Class | `AdapterRegistry` with `resolve(target) → SourceAdapter` |
| Dependencies | `.fetch`, `.targets` |
| Side effects | ❌ None |
| Deterministic | ✅ Yes |
| Network IO | ❌ No |

**NOTE:** `AdapterRegistry` is instantiated in `FetchService.__init__` with `adapters=()` (empty tuple) by default. The only concrete adapter (`GitHubRepositoryAdapter`) is never registered anywhere in the codebase.

#### `src/web_watcher/github_repository_adapter.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Fetch GitHub repository metadata via GitHub REST API (no-auth) |
| Class | `GitHubRepositoryAdapter` implementing `SourceAdapter` |
| Inputs | `FetchRequest` containing a `WatchTarget` with `target_type="github_repository"` |
| Outputs | `FetchResult` with parsed `GitHubRepositorySnapshot` as JSON content |
| Dependencies | `json`, `time`, `urllib`, `.fetch`, `.snapshots`, `.targets` |
| Side effects | ✅ HTTP GET to `api.github.com`, retry with exponential backoff |
| Deterministic | ❌ No (network-dependent, rate-limited) |
| Network IO | ✅ Yes (HTTP GET to `https://api.github.com/repos/{owner}/{repo}`) |

**Retry logic:** 429 and 5xx → retry up to `max_retries` (default 3) with `2**attempt` sleep.
304 → returns `FetchResult(success=True, status_code=304, content=None)` with `"unchanged": "true"` metadata.

#### `src/web_watcher/snapshots.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Immutable value object for GitHub repository data at a point in time |
| Class | `GitHubRepositorySnapshot` (frozen dataclass) |
| Fields | `name, full_name, description, html_url, stars, forks, open_issues, default_branch, created_at, updated_at, pushed_at, license_spdx_id, archived, visibility` |
| Dependencies | `dataclasses` only |
| Side effects | ❌ None |
| Deterministic | ✅ Yes |
| Network IO | ❌ No |

---

### Tier 4: Service & Orchestration

#### `src/web_watcher/fetch_service.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Execute one fetch-and-persist cycle for one target |
| Class | `FetchService` with `fetch_one(target) → FetchResult` |
| Inputs | `WatchTarget` |
| Outputs | `FetchResult` |
| Dependencies | `.adapters`, `.content_hash`, `.fetch`, `.fingerprint`, `.models`, `.repository`, `.targets` |
| Side effects | ✅ Writes `FetchState`, creates `Entity`, creates `Signal` (all via Repository) |
| Deterministic | ❌ No (network-dependent, DB-state-dependent) |
| Network IO | ✅ Yes (via Adapter) |

**Persistence rules:**
- **Failed fetch** → no DB changes, no Signal, returns failure
- **304 Not Modified** → no DB changes, no Signal, returns success
- **200 with NEW content_hash** → upsert FetchState + create Entity + create Signal (content_change)
- **200 with UNCHANGED content_hash** → upsert FetchState (no Signal)

**BUG FOUND:** `FetchService` instantiates `AdapterRegistry()` with an **empty adapter list** by default. `GitHubRepositoryAdapter` exists in the codebase but is **never registered** with the registry. A call to `fetch_one()` on a `github_repository` target would raise `LookupError: no adapter available for target`.

---

### Tier 5: Correlation & Policy

#### `src/web_watcher/event_correlator.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Convert Signals into coherent Events (Phase 8) |
| Class | `EventCorrelator` with `correlate(signal) → Event` and `close_event(event_id)` |
| Algorithm | Correlation Rule V1: same Entity + open Event + within 24h window → merge; else → new Event |
| Inputs | `Signal` |
| Outputs | `Event` |
| Dependencies | `.models`, `.repository` |
| Side effects | ✅ Persists Signal, attaches to Event, may create Event, updates Event.updated_at |
| Deterministic | ✅ Yes (given same inputs and DB state) |
| Network IO | ❌ No |

**Key behavior:**
- `_derive_event_type(signal)` maps `signal.signal_type` → `event_type` (V1: `content_change` → `content_change`, one-to-one)
- Default `importance="medium"` written to new Events
- If fingerprint conflict, appends `-dup-{hash}` suffix to retry
- `now_factory` injectable for test determinism

#### `src/web_watcher/policy.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Deterministic baseline policy — maps `Event` → `PolicyDecision` |
| Classes | `Importance` (enum), `Action` (enum), `PolicyDecision` (frozen), `PolicyEngine` |
| Inputs | `Event` |
| Outputs | `PolicyDecision(importance, action, reason)` |
| Dependencies | `.models` only |
| Side effects | ❌ None |
| Deterministic | ✅ Yes |
| Network IO | ❌ No |

**Importance mapping (from `event_type` string):**
| `event.event_type` | `Importance` | `Action` |
|---|---|---|
| `"critical"` | `CRITICAL` | `INVESTIGATE_AND_NOTIFY` |
| `"important"` | `IMPORTANT` | `NOTIFY` |
| `"interesting"` | `INTERESTING` | `SUMMARIZE` |
| any other value | `IGNORE` | `DISCARD` |

**CRITICAL OBSERVATION:** Policy does NOT read `Event.importance` at all. It uses only `event_type`.

---

### Tier 6: AI Layer

#### `src/web_watcher/ai_errors.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Define typed error hierarchy for all AI failures |
| Hierarchy | `AIError → ProviderError, ProviderTimeoutError, InvalidResponseError → InvalidJSONError, SchemaValidationError → UnsupportedValueError` |
| Dependencies | `builtins` only |
| Side effects | ❌ None |
| Deterministic | ✅ Yes |
| Network IO | ❌ No |

#### `src/web_watcher/ai_contract.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | AI judgment contract: types, protocol, parser, judge |
| Types | `AIContext`, `AIJudgment`, `ProviderResponse`, `AIProvider` (Protocol), `AIJudge`, `MockProvider` |
| Inputs | `AIContext` (for `AIJudge.judge()`) |
| Outputs | `AIJudgment` or raises `AIError` subtype |
| Dependencies | `.ai_errors`, `.models`, `.policy` |
| Side effects | ❌ None (pure — all external work delegated to provider) |
| Deterministic | ✅ Yes (given deterministic provider) |
| Network IO | ❌ No |

**`AIJudge._build_prompt(context)`** constructs a text prompt containing:
```
event_type='...'
event_importance='...'
policy_importance=...
policy_action=...
entity='...' type='...'
signals=...
evidence_N=...
```

**`AIJudge._build_context_headers(context)`** returns `{}` — no metadata sent to provider.

**`MockProvider` scenarios:** `valid`, `invalid_json`, `invalid_schema`, `provider_error`, `timeout`

#### `src/web_watcher/ai_config.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Provider configuration as frozen dataclass |
| Class | `SenseNovaProviderConfig` (frozen) + `load_sensenova_provider_config()` factory |
| Defaults | base_url=`token.sensenova.cn/v1`, model=`sensenova-6.7-flash-lite`, max_attempts=**2**, timeout=30s, response_format_json=False |
| Dependencies | `dataclasses`, `typing` |
| Side effects | ❌ None |
| Deterministic | ✅ Yes |
| Network IO | ❌ No |

#### `src/web_watcher/ai_provider.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Factory: combine config + provider construction |
| Function | `build_sensenova_provider(config, api_key, **kwargs) → SenseNovaLLMProvider` |
| Dependencies | `.ai_config`, `.llm_provider` |
| Side effects | ❌ None |
| Deterministic | ✅ Yes |
| Network IO | ❌ No |

#### `src/web_watcher/llm_provider.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Concrete LLM provider via stdlib urllib |
| Class | `SenseNovaLLMProvider` implementing `AIProvider` Protocol |
| Inputs | `prompt: str`, `context: Mapping[str, str]` (ignored) |
| Outputs | `ProviderResponse` |
| Dependencies | `json`, `os`, `ssl`, `time`, `urllib`, `.ai_contract`, `.ai_config`, `.ai_errors` |
| Side effects | ✅ HTTP POST to SenseNova endpoint |
| Deterministic | ❌ No (network-dependent) |
| Network IO | ✅ Yes (HTTP POST to `https://token.sensenova.cn/v1/chat/completions`) |

**Retry matrix:**
| Condition | Error | Retries |
|-----------|-------|---------|
| Missing API key | `ProviderError` | No |
| 400 | `ProviderError` | No |
| 401/403 | `ProviderError` | No |
| 408 | `ProviderTimeoutError` | No |
| 429/500/502/503/504 | `ProviderError` / `ProviderTimeoutError` | Yes (bounded) |
| Network error | `ProviderError` | Yes |
| TimeoutError | `ProviderTimeoutError` | Yes |
| Non-JSON response | `ProviderError` | No |

Backoff: `min(attempt × 0.5s, 2.0s)`

---

### Tier 7: Configuration & Entry

#### `src/web_watcher/config.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Load and validate watcher JSON config |
| Function | `load_config(path) → list[WatchTarget]` |
| Inputs | Filesystem path to JSON config |
| Outputs | List of validated `WatchTarget` |
| Dependencies | `json`, `pathlib`, `.targets` |
| Side effects | ✅ File read |
| Deterministic | ✅ Yes (given same file) |
| Network IO | ❌ No |

#### `src/web_watcher/targets.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | `WatchTarget` model + validation |
| Types | `WatchTarget` (frozen), `validate_watch_target()`, `validate_target_url_policy()` |
| Supported types | `github_repository`, `official_website`, `news_source` |
| Dependencies | `dataclasses`, `typing` |
| Side effects | ❌ None |
| Deterministic | ✅ Yes |
| Network IO | ❌ No |

#### `src/web_watcher/main.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Minimal entry point — prints "Web Watcher foundation OK" |
| Function | `main() → 0` |
| Dependencies | None |
| Side effects | None |
| Deterministic | ✅ Yes |
| Network IO | ❌ No |

**NOTE:** `main()` does not orchestrate any data flow. There is no scheduler, no CLI, no application loop.

---

## PART 2 — Actual Data Flow Trace

| Arrow | Status | Evidence |
|-------|--------|----------|
| Source → `FetchRequest` | ❌ **NOT IMPLEMENTED** | No source ingestion layer exists. No code constructs `FetchRequest`. `fetch_one()` requires a caller to provide a `WatchTarget`, but no scheduler or caller exists. |
| `FetchRequest` → Adapter | ✅ **IMPLEMENTED** | `AdapterRegistry.resolve(target)` → `select_adapter()` → returns `SourceAdapter` |
| Adapter → `FetchResult` | ✅ **IMPLEMENTED** | `GitHubRepositoryAdapter.fetch_repository()` returns `FetchResult` |
| `FetchResult` → Persistence | ✅ **IMPLEMENTED** | `FetchService._apply_result()` writes `FetchState`, `Entity`, `Signal` via `Repository` |
| Persistence → `Signal` | ✅ **IMPLEMENTED** | `FetchService` creates `Signal` with `content_change` type when content_hash changes |
| `Signal` → `Event` | ✅ **IMPLEMENTED** | `EventCorrelator.correlate(signal)` merges into existing open Event or creates new one |
| `Event` → `PolicyDecision` | ✅ **IMPLEMENTED** | `PolicyEngine.evaluate(event)` returns `PolicyDecision` |
| `PolicyDecision` → `AIContext` | ✅ **IMPLEMENTED** | `AIContext(event, policy_decision, ...)` constructor |
| `AIContext` → `AIJudge` | ✅ **IMPLEMENTED** | `AIJudge.judge(context)` builds prompt, invokes provider |
| `AIJudge` → `AIProvider` | ✅ **IMPLEMENTED** | `self._provider.invoke(prompt, headers)` |
| `AIProvider` → `ProviderResponse` | ✅ **IMPLEMENTED** | `SenseNovaLLMProvider.invoke()` returns `ProviderResponse` |
| `ProviderResponse` → `AIJudgment` | ✅ **IMPLEMENTED** | `AIJudge` parses JSON, validates, returns `AIJudgment` |
| `AIJudgment` → downstream consumer | ❌ **NOT IMPLEMENTED** | No consumer exists. `AIJudgment` has no reader in the codebase. No notification, investigation, storage, or display code consumes it. |

### Gap Summary

**The pipeline is complete from Source → AIJudgment, but broken at two points:**

1. **No entry point:** Nothing constructs the pipeline. `main.py` is a stub. No scheduler, CLI, or orchestration calls `FetchService.fetch_one()`.

2. **Adapter not registered:** `GitHubRepositoryAdapter` exists but is never registered with `AdapterRegistry`. `FetchService` creates an empty registry by default. A real `fetch_one()` call would fail with `LookupError`.

3. **No AIJudgment consumer:** The pipeline produces `AIJudgment` but nothing reads it. No storage, no notification, no investigation.

---

## PART 3 — Policy Inspection

### Q1: What determines Importance?

**`Event.event_type`** string comparison only.
```
event_type == "critical"    → CRITICAL
event_type == "important"   → IMPORTANT
event_type == "interesting" → INTERESTING
anything else               → IGNORE
```

### Q2: What determines Action?

A static `Importance → Action` dictionary:
```
IGNORE    → DISCARD
INTERESTING → SUMMARIZE
IMPORTANT   → NOTIFY
CRITICAL    → INVESTIGATE_AND_NOTIFY
```

### Q3: Does Policy read Event.importance?

**NO.** Policy does not read `Event.importance` at all. It reads only `event_type`.

### Q4: Does Policy mutate Event?

**NO.** `PolicyEngine.evaluate()` is a pure function. It returns a new `PolicyDecision`; it never modifies the input `Event`.

### Q5: Can unknown Event types safely enter the system?

**YES.** Unknown event types are mapped to `IGNORE → DISCARD`, which is the safest possible default. No error is raised, no exception, no side effect.

### Q6: Does Policy call AI?

**NO.** Policy has zero imports of any AI-related module. It uses only `dataclasses`, `enum`, and `.models.Event`.

### Q7: Does Policy perform IO?

**NO.** Policy has zero filesystem, network, or database access.

---

## PART 4 — Event.importance Analysis

### Who writes it?

| Writer | Location | Value |
|--------|----------|-------|
| `Repository.create_event()` | `repository.py` | Default `"medium"` (hardcoded) |
| `Repository.update_event()` | `repository.py` | Caller-supplied `importance` |
| No other writer exists in current code | — | — |

`EventCorrelator` creates events via `Repository.create_event(importance=self._config.default_importance)` where `default_importance="medium"`.

### Who reads it?

| Reader | Location | How |
|--------|----------|-----|
| **No code reads it for decision-making** | — | — |
| `AIJudge._build_prompt()` | `ai_contract.py` | Included in prompt text as `event_importance='...'` — informational context only, not used for logic |
| `Repository.get_event()` | `repository.py` | Reads from DB and returns as part of `Event` dataclass — passive data transfer |
| `Repository.update_event()` | `repository.py` | Reads existing value to preserve it when updating other fields |

### Does Policy ignore it?

**YES.** Policy reads only `event_type`, never `event.importance`.

### Does AIContext contain it?

**YES.** `AIContext` contains the full `Event` object, which includes `importance`. `AIJudge` includes it in the prompt text.

### Does AIJudgment replace it?

**NO.** `AIJudgment` has its own `importance: Importance` field (the enum), which is a **semantic judgment**. `Event.importance` remains the raw domain field (a `str` like `"medium"`). The Phase 10A contract explicitly states: `Event.importance` remains the raw domain field — its semantics are not changed by this phase.

### Migration risk assessment

| Scenario | Risk |
|----------|------|
| Remove `Event.importance` field | **HIGH** — schema migration required, `Repository.create_event()` and `update_event()` signatures change, all tests referencing it break, 7 existing test files would need updates |
| Rename `Event.importance` to `raw_importance` | **MEDIUM** — same DB migration, model signature change, test updates |
| Leave unchanged | **NONE** — field exists, is harmless, and Phase 10A contract explicitly preserves it |
| Make Policy read `Event.importance` | **LOW-MEDIUM** — would change Phase 9 semantics; current design intentionally uses `event_type` only |

### Recommendation

**KEEP**

Rationale:
1. `Event.importance` is a harmless raw domain field written by `Repository` and preserved through the stack.
2. No code reads it for decision-making — it is already effectively inert.
3. Phase 10A contract explicitly preserves its semantics.
4. Removing it now would require DB schema migration, model changes, test updates, and violates the Phase 10A boundary guarantee.
5. If needed in the future, it can be re-purposed to carry the AI judgment importance back into the domain layer — but that is a Phase 10C+ decision, not a Phase 2–10 concern.

---

## PART 5 — AI Contract Review

### AIJudgment Field Analysis

| Field | Current Purpose | Current Downstream Consumer | Future Intended Role | Sufficient? |
|-------|----------------|----------------------------|---------------------|-------------|
| `relevance: float` | Quantifies how relevant the event is to the user's interests, on [0.0, 1.0] | **None** — no code reads this field | Drive priority ranking in future notification/investigation workflows | ✅ Yes — float with range validation is appropriate |
| `importance: Importance` | Semantic judgment of event importance (reuses Phase 9 enum) | **None** — no code reads this field | Could override or refine `PolicyDecision.importance` in a `FinalDecision` layer | ✅ Yes — reuses existing enum, no duplication |
| `worth_notifying: bool` | Whether the user should be notified about this event | **None** — no code reads this field | Trigger notification workflow (Telegram, etc.) | ✅ Yes — boolean with validation |
| `investigate: bool` | Whether the event warrants deeper investigation | **None** — no code reads this field | Trigger investigation workflow (research, analysis) | ✅ Yes — boolean with validation |
| `reason: str` | Human-readable explanation for the judgment | **None** — no code reads this field | Shown to user in notifications, stored for audit | ✅ Yes — non-empty string with validation |
| `summary: str` | Concise summary of the event | **None** — no code reads this field | Shown to user, stored as event annotation | ✅ Yes — non-empty string with validation |

### Key Observations

1. **Zero consumers exist.** Every `AIJudgment` field is written but never read. The pipeline produces judgments into a void.

2. **`relevance` has no consumer.** Without a ranking or prioritization layer, the float value is dead data. Future phases need a `FinalDecision` that combines `PolicyDecision.importance` and `AIJudgment.relevance` into an actionable priority.

3. **`importance` duplicates `PolicyDecision.importance`.** Both carry the `Importance` enum. The Phase 10A design treats `PolicyDecision` as authoritative and `AIJudgment` as semantic refinement. A `FinalDecision` layer would need to resolve conflicts between the two.

4. **`worth_notifying` and `investigate` are dead code.** These are the two most actionable fields, but no code acts on them. The `Action` enum has `NOTIFY` and `INVESTIGATE_AND_NOTIFY` values, but no notification or investigation system exists to consume them.

5. **`reason` and `summary` are dead data.** Without a display or storage layer, these human-readable fields serve no purpose in the current architecture.

6. **Validation is strict and correct.** Every field is type-validated in `__post_init__` with typed errors (`SchemaValidationError`, `UnsupportedValueError`). No coercion, no silent fallbacks.

### Structural Assessment

The AI contract is **well-designed as a boundary layer** but **incomplete as a system**. The contract cleanly separates:
- **Phase 9** (deterministic `PolicyDecision`) from **Phase 10** (semantic `AIJudgment`)
- **Provider** (raw bytes) from **Judge** (typed validation)
- **Errors** (typed hierarchy) from **Success** (immutable dataclass)

The contract is insufficient because there is no downstream consumer. The fields are defined but unreachable. The architecture needs:
1. A `FinalDecision` layer (Phase 10C) that combines `PolicyDecision` + `AIJudgment`
2. A notification system that consumes `worth_notifying`
3. An investigation system that consumes `investigate`
4. Storage/persistence of `AIJudgment` for auditability
5. A scheduler/orchestrator that actually runs the pipeline

---

## PART 6 — Critical Findings

### F1: `FetchService` Has No Working Adapter

`GitHubRepositoryAdapter` exists and is fully implemented, but `FetchService` instantiates `AdapterRegistry(adapters=())` by default. No code registers any adapter. Any call to `fetch_one()` would raise `LookupError`.

**Severity:** High — the entire fetch pipeline is unreachable.

### F2: No Pipeline Entry Point

`main.py` prints "Web Watcher foundation OK" and exits. No code constructs the pipeline: no scheduler, no CLI, no background worker. The architecture is designed but never invoked.

**Severity:** High — the system does nothing.

### F3: AIJudgment Has Zero Consumers

The pipeline terminates at `AIJudgment` with nothing reading the output. `relevance`, `importance`, `worth_notifying`, `investigate`, `reason`, `summary` are all dead data.

**Severity:** Medium — the AI layer produces results that disappear.

### F4: `Event.importance` vs `Importance` Enum Confusion

`Event.importance` is a `str` (default `"medium"`), while `Importance` is an enum (`IGNORE`, `INTERESTING`, `IMPORTANT`, `CRITICAL`). Policy uses `event_type` → `Importance`, never reading `Event.importance`. A future phase could conflate these two distinct concepts.

**Severity:** Low (informational) — currently harmless but a potential future footgun.

### F5: `EventCorrelator._derive_event_type` Is a No-Op

`content_change` → `content_change` (one-to-one identity mapping). This is intentional for extensibility but currently provides no intelligence. The `Event.event_type` will always be `"content_change"` for all signals.

**Severity:** Low — by design for Phase 8, but limits Policy's ability to distinguish event severity.

### F6: `PolicyEngine` Cannot Distinguish Severity

Since all events get `event_type="content_change"`, `PolicyEngine` maps every event to `IGNORE → DISCARD`. The deterministic policy is effectively disabled by the correlation layer.

**Severity:** Medium — the policy layer exists but is unreachable in practice.

---

## PART 7 — Phase Completeness Assessment

| Phase | Status | Key Gap |
|-------|--------|---------|
| Phase 2 (Foundation) | ✅ Complete | Models, schema, storage all implemented |
| Phase 3 (Repository) | ✅ Complete | All CRUD operations implemented |
| Phase 4 (Targets/Config) | ✅ Complete | WatchTarget + config loader working |
| Phase 5 (Fetch Contracts) | ✅ Complete | Protocols defined |
| Phase 6 (GitHub Adapter) | ✅ Complete | Full implementation |
| Phase 7 (Fetch Persistence) | ✅ Complete | FetchService with 304/content-change logic |
| Phase 8 (Event Correlation) | ✅ Complete | Deterministic correlation working |
| Phase 9 (Policy Engine) | ✅ Complete | Deterministic policy working |
| Phase 10A (AI Contract) | ✅ Complete | Contract + validation + MockProvider |
| Phase 10B (Real Provider) | ✅ Complete | SenseNovaLLMProvider implemented |
| **Pipeline Orchestration** | ❌ **MISSING** | No code connects phases 2–10B into a runnable system |
| **AIJudgment Consumer** | ❌ **MISSING** | No Phase 10C, no notification, no investigation |

---

## PART 8 — Recommendations (Read-Only, No Implementation)

1. **Register GitHubRepositoryAdapter.** The adapter exists; add it to `AdapterRegistry` in `FetchService.__init__`.

2. **Create a pipeline orchestrator.** A minimal `run_watcher()` function that loads config, iterates targets, fetches, correlates, evaluates policy, and judges.

3. **Persist AIJudgment.** Store judgments alongside Events for auditability and future Phase 10C consumption.

4. **Resolve Event.importance ambiguity.** Either (a) keep the raw str field and document its relationship to the Importance enum, or (b) migrate to the enum type.

5. **Make EventCorrelator produce richer event_types.** Instead of identity mapping `content_change → content_change`, consider deriving severity from signal metadata (e.g., star count change, release, fork event).

---

*END OF REPORT — No files modified. Read-only audit complete.*
