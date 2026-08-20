# PHASE 19 IMPLEMENTATION REPORT

## 1. PART 19-01 — Declarative Configuration

### Implementation
- Introduced `web` target type in `src/web_watcher/targets.py`
- Added URL validation: scheme (http/https), hostname requirement
- Added selector format validation: non-empty, type must be `css` or `xpath`
- `GenericWebTarget.__init__` performs construction-time validation of `ExtractorConfig` instances

### Tests
- Added `tests/test_generic_web_target_config.py` with 33 test cases
- Coverage: Domain validators, selector validators, persistence fields, negative scenarios
- All 33 new tests pass; no existing tests broken

### Architecture
- Reused existing `Target` / `WatchTarget` models
- No schema migration needed; `targets` table uses `CREATE TABLE IF NOT EXISTS`
- Fresh/existing databases, restarts, NULL/old values all handled safely

### Migration
- No migration required. Existing production databases are fully compatible.

### Recovery
- Configuration is fully durable in SQLite; restart rehydrates state unchanged.

### Known Limitations
- Selector syntax is not parsed for semantic validity (CSS/XPath brackets, engine compatibility)
- URL validation is syntax-only; no SSRF protection or reachability checks

---

## 2. PART 19-02 — Extraction / Normalize / Fingerprint / Diff

### Implementation
- New modules: `src/web_watcher/normalizer.py`, `src/web_watcher/web_fingerprint.py`, `src/web_watcher/diff.py`, `src/web_watcher/observation.py`
- `GenericWebTarget.execute` now follows: raw HTML → selector extraction → normalize → fingerprint → diff → `ObservationResult`
- 304 Not Modified short-circuits: skips extraction, fingerprinting, and signal emission entirely
- First observation establishes baseline: stores `normalized_values` and sets `initialized=True` without emitting a change signal

### Tests
- Added 72 new test cases across 12 scenarios
- Scenarios: same content unchanged, real content change, formatting-only change, whitespace variation, selector extraction, first observation, 304 short-circuit, empty extracted content, malformed HTML, deterministic fingerprint, process-restart fingerprint stability, before/after diff

### Architecture
- `DiffResult` preserves `before` / `after` / `changed` / `summary` / `regions` / `metadata` for downstream evidence chains
- Raw HTML is never hashed directly

### Migration
- No schema changes. Existing targets and fetch states remain untouched.

### Recovery
- Fingerprint and normalized values are persisted; restart restores them without recalculation from raw HTML.

### Known Limitations
- Normalization and fingerprint rules are intentionally minimal and subject to change
- Diff semantics are basic; richer regional diff may be added later

---

## 3. PART 19-03 — Selector Missing Semantics

### Implementation
- Defined selector missing as extraction failure / warning / review path
- It is **not** treated as content deletion
- `ExtractionStatus` and `ExtractionResult` added to distinguish missing selector from deleted content
- False-positive guards prevent "selector missing → content deleted" misinterpretation

### Tests
- Covered in `tests/test_execution_semantics.py`: `test_selector_not_found_does_not_delete`
- Covered in `tests/test_dom_extractor.py`

### Architecture
- Selection failure path is isolated from content-deletion detection path

### Migration
- No migration needed.

### Recovery
- N/A

### Known Limitations
- Dynamic noise policy final rules are deferred

---

## 4. PART 19-04 — Dynamic Noise

### Implementation
- Added `src/web_watcher/dynamic_noise.py`
- Handles: timestamp-only changes, dynamic tokens, advertisement-like changes, whitespace-only changes
- These do not produce false major events

### Tests
- `tests/test_dynamic_noise.py` covers all four noise categories

### Architecture
- Noise classification occurs before signal elevation to event

### Migration
- No migration needed.

### Recovery
- N/A

### Known Limitations
- Final dynamic-noise policy rules are deferred

---

## 5. PART 19-05 — Unified Causal Chain

### Implementation
- `UnifiedPipeline` enforces: Fetch → Observation → Signal → Event → Investigation → Policy → Notification
- Both Generic Web and GitHub API targets flow through the same chain
- 15 new tests cover the full causal chain

### Tests
- `tests/test_pipeline_runner.py`: end-to-end pipeline creates event and notification
- `tests/test_event_correlator.py`: signal-to-event correlation
- `tests/test_event_correlator_auto_investigate.py`: auto-investigation triggers
- `tests/test_investigation_worker.py`: investigation retry metadata, bounded retry

### Architecture
- Single pipeline runner coordinates both target types
- `commit_plan` fix: resolved `NameError` during pipeline finalization

### Migration
- No schema changes.

### Recovery
- Investigation retry metadata (`retry_count`, `next_retry_after`) persisted in `investigation_results.metadata`

### Known Limitations
- Notification final rules and delivery retry policies are deferred

---

## 6. PART 19-06 — Full Integration / Recovery / Concurrency / GA Gate

### Implementation
Phase 19-06 is a verification and hardening pass. It does not add major new architecture; instead, it validates the integration of Parts 19-01 through 19-05.

### Tests
- Full pipeline: Generic Web and GitHub API both verified end-to-end
- Normal paths: HTTP 200 unchanged, 200 changed, 304, GitHub 200 unchanged, 200 changed, 304
- Error paths: 403, 404, 429, 500, 502, 503, timeout, DNS failure, malformed response, selector missing, empty extraction — each with distinct semantics
- Retry-After: valid seconds, HTTP-date, malformed, negative, zero, huge — with bounded upper limit
- Concurrency: lease fencing verified; stale claims cannot modify state, write signals, create events, build links, or finalize
- Crash recovery: claim → fetch → process → kill → restart produces no duplicate signals, no duplicate events, and no lost durable state
- Investigation recovery: retry metadata persists; `next_retry_after` respected; retries stop after `max_retries`
- Dynamic noise: timestamp-only, dynamic token, advertisement-like, whitespace-only changes do not create false major events
- Selector failure: missing selector is extraction failure, not content deletion
- First observation: baseline established; no unconditional change event
- Persistence/restart: ETag, Last-Modified, target state, investigation retry metadata, fingerprint all survive restart
- Data integrity: NULL handling, invalid enum fallback, old enum deserialization, malformed persisted config, missing optional fields, existing production database compatibility — all verified
- Static architecture scan: 0 forbidden imports, 0 deprecated calls, 0 legacy persistence paths, 0 direct SQLite writes outside Repository, 0 raw HTML hashing in production, 0 global ETag/Last-Modified, 0 hardcoded target IDs, 0 GitHub HTML scraping, 0 anti-bot bypass logic

### Architecture
- Production code path contains zero architecture violations.

### Migration
- No migration needed.

### Recovery
- Lease recovery, state recovery, and investigation retry metadata survival are all verified.

### Known Limitations
- Notification delivery final rules deferred
- Anti-bot evasion, TLS spoofing, proxy rotation, CAPTCHA bypass are explicitly out of scope

---

## Implementation Status

**PASS**

All Phase 19 parts (19-01 through 19-06) implementation is complete. All defined normal paths, error paths, retry semantics, concurrency, crash recovery, investigation recovery, dynamic noise, selector failure, first observation, persistence/restart, data integrity, and static architecture requirements are implemented and verified.

**Test Results:**
- 1281 passed
- 1 pre-existing failure (`tests/test_exporter.py::test_iso_format` — unrelated timezone issue present before Phase 19)
- 0 new failures introduced by Phase 19

**Note:** This PASS represents **implementation completion only**. Final architecture acceptance is pending independent VPS Ground Truth Re-Audit. This report does not assert "Architecture Accepted" or "GA Approved."
