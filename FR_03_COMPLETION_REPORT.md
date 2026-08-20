# FR_03 — Deterministic Jitter & Strict Retry-After Semantics: Completion Report

## Summary
Replaced non-deterministic random jitter with stable hash-derived jitter and
made Retry-After the effective floor, preventing local cooldown from shortening
server-requested waits.

## Changes

### Modified Files
- `src/web_watcher/fetch_policy.py`:
  - Added `_deterministic_jitter()` which derives jitter from a SHA-256 hash of
    `(target_id, failure_count, status_code, base_backoff)`, producing the
    same jitter for the same inputs on every invocation.
  - Replaced all `random.uniform(-self.jitter_ratio, self.jitter_ratio)`
    calls with `_deterministic_jitter()`.
  - Restructured 429 handling: removed the early COOLDOWN/RECOVERING
    escalation that bypassed `Retry-After`. Local cooldown now uses
    `max(local_delay, retry_after_sec)` so the server's delay is the effective
    floor.

- `tests/test_fetch_policy.py`:
  - Updated `test_backoff_contains_bounded_jitter` to assert deterministic
    behavior (all delays identical) instead of random variety.
  - Added 3 new tests:
    - `test_deterministic_jitter_same_target_same_state`
    - `test_deterministic_jitter_different_targets_differ`
    - `test_retry_after_is_effective_floor`

## Evidence

### Test Results
- `tests/test_fetch_policy.py`: 39 passed
- Full pytest: **1345 passed**

### Deterministic Jitter
- Same target (`id="jitter-target"`, `consecutive_failures=1`, `status_code=500`)
  produces identical backoff delay across 10 invocations.
- Different targets (`t_a` vs `t_b`) produce different jitter at the same
  failure count, preserving distributional spread.

### Strict Retry-After
- When `target.status == COOLDOWN` and `Retry-After: 3600` is received:
  - Previous behavior: local cooldown ladder[0] = 1800s (shortens server wait)
  - New behavior: `max(1800, 3600) = 3600s` (server delay is effective floor)

### Backoff Bounds
- Raw backoff remains `min(base_backoff * 2^(failures-1), max_backoff)`.
- Jitter remains bounded by `±jitter_ratio * raw_backoff`.
- Combined delay remains clamped to `max(1.0, raw_backoff + jitter)`.
