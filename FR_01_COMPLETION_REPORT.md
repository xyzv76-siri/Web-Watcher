# FR_01 — Host-Level Rate Limit Authority: Completion Report

## Summary
Implemented shared per-host rate-limit authority and removed internal retry/sleep from GitHubAdapter.

## Changes

### New Files
- `src/web_watcher/host_rate_limiter.py` — `HostRateLimiter` tracks per-host `next_allowed_at`

### Modified Files
- `src/web_watcher/fetch_policy.py` — `FetchPolicy` now accepts `host_rate_limiter` and checks it before target-level allow
- `src/web_watcher/github_repository_adapter.py` — Removed `max_retries`, `_sleep`, and internal retry loops; single-shot adapter now defers retry/backoff to `FetchPolicy`
- `tests/test_fetch_policy.py` — Added 5 host-rate-limit tests
- `tests/test_github_repository_adapter.py` — Replaced retry logic tests with no-internal-retry tests; added Retry-After metadata exposure test

## Evidence

### Test Results
- `tests/test_fetch_policy.py`: 36 passed
- `tests/test_github_repository_adapter.py`: 40 passed
- Full pytest: **1337 passed**

### Host Rate Limiter Behavior
1. `HostRateLimiter.prepare_request(host, now)` blocks subsequent requests to the same host until the stored `next_allowed_at` expires
2. `HostRateLimiter.update_after_response(host, next_allowed_at)` records the most restrictive (max) next-allowed time for the host
3. `FetchPolicy.prepare_request()` checks host limiter **after** target-level check, so the most restrictive limit wins
4. `FetchPolicy.evaluate_response()` updates the host limiter for every evaluated response path (200, 304, 403, 404, 429, transport failure, 5xx)

### GitHubAdapter Contract Change
- Constructor: `GitHubRepositoryAdapter(timeout=15.0)` — no `max_retries`, no `sleep`
- `_request()` performs a single `urlopen()` call
- All HTTP errors (429, 403, 404, 5xx) and network errors are returned directly as `FetchResult` with status `HTTP_ERROR` / `NETWORK_ERROR`
- Retry-After headers from error responses are preserved in `metadata["retry_after"]` for downstream policy use

## Verification
- Full test suite passes: **1337 passed**
- No regressions in existing Generic Web, GitHub, Scheduler, Repository, or pipeline tests
- Shared-host collision test confirms: when Target A and Target B share `example.com` and Target A enters cooldown, Target B is also blocked by host limiter
- Isolated-host test confirms: targets on different hosts remain independent
