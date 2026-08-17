# Phase 10C-A.1 — AI Failure Semantics Report

## Baseline

| Check | Status |
|-------|--------|
| Phase 9 PolicyEngine | PASS (unchanged) |
| Phase 10A AI Contract | PASS (unchanged) |
| Phase 10B AI Provider | PASS (unchanged) |
| Phase 10C-A Decision Layer | PASS (modified) |
| Test suite before changes | 424 passed |
| Test suite after changes | **448 passed** (24 new tests) |

## Changed Files

### Modified (production code)

1. **`src/web_watcher/final_decision.py`** — Core change
   - Added `DecisionStatus` enum with `RESOLVED` and `AI_UNAVAILABLE`
   - Added `status` field to `FinalDecision` dataclass
   - Refactored `resolve()` to auto-determine status from `ai_judgment`:
     - `ai_judgment is not None` → `RESOLVED` (existing behavior preserved)
     - `ai_judgment is None` → `AI_UNAVAILABLE` (new explicit failure semantics)
   - New `_build_failure_reason()` helper for explicit AI-unavailable messaging
   - CRITICAL + AI_UNAVAILABLE: `notify_allowed=False`, `investigate_requested=False` (no exception)

2. **`src/web_watcher/decide.py`** — Minimal change
   - Removed explicit `DecisionStatus` parameter (now auto-determined by `resolve()`)
   - `decide_event()` catches `AIError` → calls `resolve(policy, None)` → produces `AI_UNAVAILABLE`
   - No AI judge injected → same path → `AI_UNAVAILABLE`
   - Unrelated non-AIError exceptions propagate naturally (not caught)

3. **`src/web_watcher/ai_contract.py`** — Test utility only
   - Added `"unsupported_value"` scenario to `MockProvider` (returns JSON with out-of-domain importance string)
   - Updated docstring
   - No contract/interface changes

### Modified (tests)

4. **`tests/test_final_decision.py`** — Added `TestDecisionStatus` class (10 new tests)
5. **`tests/test_decide.py`** — Added 4 new test classes (14 new tests)

## Failure Semantics

### DecisionStatus Enum

```python
class DecisionStatus(str, Enum):
    RESOLVED = "resolved"
    AI_UNAVAILABLE = "ai_unavailable"
```

### Successful AI Judgment Path

```
Event
  -> PolicyDecision (PolicyEngine.evaluate)
  -> AIContext (constructor)
  -> AIJudgment (AIJudge.judge)
  -> FinalDecision (resolve)
     status = RESOLVED
```

Behavior unchanged from Phase 10C-A:
- CRITICAL → notify_allowed=True, investigate_requested=True
- AI can elevate importance (never suppress)
- notify_allowed / investigate_requested follow existing rules

### AI Failure Path

```
Event
  -> PolicyDecision (PolicyEngine.evaluate)
  -> AIContext (constructor)
  -> AIError raised (ProviderError, ProviderTimeoutError, InvalidJSONError,
     SchemaValidationError, UnsupportedValueError)
  -> FinalDecision (resolve with ai_judgment=None)
     status = AI_UNAVAILABLE
```

AI_UNAVAILABLE rules (all enforced):
- `final_importance` = PolicyDecision importance (no AI-derived elevation/suppression)
- `final_action` = PolicyDecision action
- `notify_allowed` = False (always)
- `investigate_requested` = False (always)
- `ai_judgment` = None
- `ai_overrode` = False
- `reason` explicitly states "AI judgment was unavailable"
- `summary` = "" (empty, no AI-produced summary)
- CRITICAL is NOT an exception

### Error Type Coverage

| AIError subtype | MockProvider scenario | Status |
|-----------------|----------------------|--------|
| `ProviderError` | `"provider_error"` | AI_UNAVAILABLE |
| `ProviderTimeoutError` | `"timeout"` | AI_UNAVAILABLE |
| `InvalidJSONError` | `"invalid_json"` | AI_UNAVAILABLE |
| `SchemaValidationError` | `"invalid_schema"` | AI_UNAVAILABLE |
| `UnsupportedValueError` | `"unsupported_value"` | AI_UNAVAILABLE |

### Unrelated Exceptions

Non-AIError exceptions are NOT caught by `decide_event()`. Tests verify:
- `RuntimeError` from a judge propagates (not converted to AI_UNAVAILABLE)
- `ValueError` from a policy engine propagates

## Successful-Path Preservation

Verified unchanged by existing + new tests:

| Scenario | final_importance | final_action | notify_allowed | investigate_requested | status |
|----------|-----------------|-------------|----------------|---------------------|--------|
| IGNORE + AI→IMPORTANT | IMPORTANT | NOTIFY | True | False | RESOLVED |
| IMPORTANT + AI→IMPORTANT | IMPORTANT | NOTIFY | True | False | RESOLVED |
| CRITICAL + AI→IGNORE | CRITICAL | INVESTIGATE_AND_NOTIFY | True | True | RESOLVED |
| IGNORE + AI→INTERESTING | INTERESTING | SUMMARIZE | True | False | RESOLVED |
| INTERESTING + AI→CRITICAL | CRITICAL | INVESTIGATE_AND_NOTIFY | True | True | RESOLVED |

## Tests

### New Tests Added (24 total)

**`test_final_decision.py` — `TestDecisionStatus` (10 tests):**
1. `test_success_ai_resolved_status` — AI judgment present → RESOLVED
2. `test_no_ai_ai_unavailable_status` — ai_judgment=None → AI_UNAVAILABLE
3. `test_no_ai_critical_ai_unavailable_status` — CRITICAL + None → AI_UNAVAILABLE, notify=False, investigate=False
4. `test_no_ai_notify_allowed_false_for_all_importances` — All 4 importances → notify_allowed=False
5. `test_no_ai_investigate_requested_false_for_all_importances` — All 4 importances → investigate_requested=False
6. `test_no_ai_reason_explicit` — Reason contains "AI judgment was unavailable"
7. `test_no_ai_preserves_policy_importance` — final_importance == policy importance
8. `test_no_ai_preserves_policy_action` — final_action == policy action
9. `test_no_ai_summary_is_empty` — summary == ""
10. `test_no_ai_ai_judgment_is_none` — ai_judgment is None

**`test_decide.py` — `TestAiUnavailableStatus` (7 tests):**
1. `test_provider_error_ai_unavailable_status`
2. `test_provider_timeout_ai_unavailable_status`
3. `test_invalid_json_ai_unavailable_status`
4. `test_invalid_schema_ai_unavailable_status`
5. `test_unsupported_value_ai_unavailable_status` — UnsupportedValueError → AI_UNAVAILABLE
6. `test_ai_unavailable_reason_explicit`
7. `test_success_ai_resolved_status`

**`test_decide.py` — `TestUnrelatedExceptionsNotSwallowed` (2 tests):**
1. `test_non_ai_error_from_judge_propagates` — RuntimeError from judge raises
2. `test_non_ai_error_from_policy_engine_propagates` — ValueError from policy engine raises

**`test_decide.py` — `TestAiUnavailableCriticalSemantics` (3 tests):**
1. `test_critical_provider_error_no_notification`
2. `test_critical_timeout_no_notification`
3. `test_critical_no_judge_no_notification`

**`test_decide.py` — `TestSuccessfulPathPreservation` (2 tests):**
1. `test_successful_ai_elevation_unchanged`
2. `test_critical_with_valid_ai_allows_notification`

### Existing Tests Updated (1 test)

`test_no_judge_critical_event` — Updated assertions to expect `notify_allowed=False` and `investigate_requested=False` for AI_UNAVAILABLE CRITICAL semantics.

### Full Regression Suite

448/448 passed (was 424/424 before changes).

## Security Audit

| Constraint | Verification | Result |
|-----------|-------------|--------|
| Zero network | `decide.py` and `final_decision.py` have no network imports | ✅ PASS |
| Zero subprocess | No `subprocess`, `os.system`, `os.popen` in modified files | ✅ PASS |
| Zero shell/eval/exec | No `eval()`, `exec()`, shell invocation | ✅ PASS |
| Zero secrets | No API keys, tokens, or credentials introduced | ✅ PASS |
| Zero QwenPaw invocation | No `qwenpaw` CLI calls in code or tests | ✅ PASS |

Pre-existing `urllib` imports in `llm_provider.py` (Phase 10B) are untouched and outside this phase's scope.

## Production Isolation

- No database migration
- No configuration change
- No environment variable change
- No deployment artifacts
- No API endpoint change
- `FinalDecision` remains a frozen dataclass (backward-compatible field addition)
- `resolve()` signature unchanged (same parameters, same return type)
- `decide_event()` signature unchanged
- All existing tests pass without modification (except one assertion update for CRITICAL AI_UNAVAILABLE)

## Git Status

```
On branch master
Untracked files:
  src/web_watcher/ai_config.py
  src/web_watcher/ai_contract.py
  src/web_watcher/ai_errors.py
  src/web_watcher/ai_provider.py
  src/web_watcher/decide.py
  src/web_watcher/final_decision.py
  src/web_watcher/llm_provider.py
  tests/test_ai_contract.py
  tests/test_decide.py
  tests/test_final_decision.py
  tests/test_llm_provider.py
```

All files are untracked — no commits made. Phase 10C-A.1 is NOT committed.

## Residual Risks

1. **Backward compatibility of `resolve()`**: The function no longer accepts a `status` parameter (it auto-determines from `ai_judgment`). Any external caller that was passing `status=` explicitly would break. However, `resolve()` is an internal function with no known external callers.

2. **"No judge" vs "AI failure" indistinguishable at FinalDecision level**: Both `judge=None` and `judge raised AIError` produce `AI_UNAVAILABLE`. The `reason` field doesn't distinguish the cause. If future code needs to know *why* AI was unavailable, an additional field or enum value would be needed — but the spec explicitly says not to add speculative statuses.

3. **MockProvider modification**: The `"unsupported_value"` scenario was added to `MockProvider` in `ai_contract.py`. This is a test utility change, not a contract change, but it does modify the Phase 10A file.

4. **No integration tests**: The AI_UNAVAILABLE semantics were tested at the `resolve()` and `decide_event()` levels. End-to-end integration tests with a real event pipeline were not added, as they would require infrastructure outside the scope of this phase.

5. **DecisionStatus enum extensibility**: The enum currently has two values. Adding new statuses in the future would require coordinated changes across all consumers. The spec explicitly restricts to these two values.
