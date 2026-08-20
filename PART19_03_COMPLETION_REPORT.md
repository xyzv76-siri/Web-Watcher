# Phase 19 — Part 19-03 Completion Report

## Dynamic Noise / Selector Failure / False Positive Protection

---

## 1. Dynamic Noise Strategy

### Patterns Normalized

The system now normalizes the following dynamic noise patterns before
fingerprinting and diffing:

- ISO 8601 timestamps (with or without timezone)
- RFC 1123 HTTP dates
- Unix timestamps (10/13 digits)
- Relative time phrases (`2h ago`, `just now`, etc.)
- UUID / GUIDs
- Tracking / analytics query parameters (`utm_*`, `_ga`, `_gid`, etc.)
- Long hex tokens (32+ chars)
- Base64-ish tokens (20+ chars)
- JWT / JWS tokens (`header.payload.signature`)
- Opaque session / cookie tokens (`session=...`, `csrf=...`, etc.)

### Implementation

- **Module**: `src/web_watcher/dynamic_noise.py`
- **Class**: `DynamicNoiseFilter`
- **Method**: `filter(text) -> str` replaces dynamic noise with empty string
- **Method**: `filter_normalize(text) -> str` combines noise removal with whitespace normalization
- **Configurable**: custom patterns and placeholder can be passed at construction time

### Design Principles

- **Deterministic**: same input always produces same output
- **Configurable**: patterns are injectable
- **Bounded**: does not remove arbitrary content; only well-known dynamic patterns
- **Explainable**: each pattern is documented and regex is explicit

---

## 2. Selector Failure Semantics

### Core Rules

- `selector missing` → `extraction warning/failure` → **NO deletion inference** → **NO content change signal**
- Must preserve: `target`, `selector`, `timestamp`, `failure reason` for investigation

### Implementation

- `ObservationStatus.EXTRACTION_FAILURE` is set when all extractors fail
- No content-change signal is emitted
- Evidence chain preserves:
  - `extractor_results[].status` = `"not_found"`
  - `extractor_results[].selector_type`
  - `extractor_results[].selector`
  - `extractor_results[].failure_reason` (implicit in status)

### State Machine Boundary

- Selector missing does **NOT** affect `TargetStatus`
- `TargetStatus` remains whatever `FetchPolicy` determined (e.g., `NORMAL`)
- The failure is captured at the **observation layer**, not the **policy layer**

---

## 3. False Positive Guard

### Implementation

- **Class**: `FalsePositiveGuard`
- **Location**: `src/web_watcher/dynamic_noise.py`
- **Integration**: `GenericWebTarget.__init__` accepts optional `false_positive_guard`
- **Default**: `FalsePositiveGuard()` is created automatically if not provided

### Guard Rules

1. **First observation** never produces a signal (baseline establishment)
2. **Extraction failure** never produces a content-change signal
3. **HTTP failures** (4xx/5xx) never produce content-change signals
4. **Dynamic noise suppression**: if after filtering dynamic noise the before/after values are identical, suppress the signal
5. **Mixed content**: if semantic content changed alongside noise, keep the signal

### Evidence Preservation

When a signal IS emitted, the payload includes:
- `diffs[].noise_filtered_before`
- `diffs[].noise_filtered_after`
- `false_positive_guard.dynamic_noise_threshold`

When a signal is suppressed, the observation status is `UNCHANGED` and the
`reason` field explains the suppression.

---

## 4. Evidence Semantics

### Real Change Evidence

- `before` / `after` (from `DiffResult`)
- `diff.summary` / `diff.regions`
- `fingerprint` (per extractor)
- `timestamp`
- `target`
- `selector_type` / `selector`
- `noise_filtered_before` / `noise_filtered_current`
- `dynamic_noise_ratio_previous` / `dynamic_noise_ratio_current`

### Extraction Failure Evidence

- `selector`
- `failure reason` (encoded in `extractor_results[].status` = `"not_found"`)
- `timestamp`
- `target`
- `selector_type` / `selector`

### No Fake Change Evidence

When extraction fails, no artificial "content deleted" evidence is created.

---

## 5. HTTP vs Extraction Failure Separation

Four distinct semantics are maintained:

| Outcome | Observation Status | Notes |
|---------|-------------------|-------|
| HTTP 200, no change | `UNCHANGED` | Normal operation |
| HTTP 200, real change | `CHANGED` | Signal emitted |
| HTTP failure (403/429/timeout/etc.) | `UNCHANGED` | Policy handles backoff/cooldown |
| Extraction failure | `EXTRACTION_FAILURE` | Selector/content issue, not deletion |
| First observation | `FIRST_OBSERVATION` | Baseline only |

---

## 6. Test Coverage

### New Tests Added

**Module**: `tests/test_dynamic_noise.py`

- **DynamicNoiseFilter** (12 tests):
  - ISO timestamp removal
  - RFC 1123 date removal
  - Unix timestamp removal
  - Relative time removal
  - UUID removal
  - Tracking parameter removal
  - Hex token removal
  - Opaque session removal
  - Semantic content preservation
  - Whitespace normalization combined with noise removal
  - Custom patterns
  - Custom placeholder

- **FalsePositiveGuard** (8 tests):
  - First observation suppression
  - All extractors failed suppression
  - HTTP 403 suppression
  - HTTP 429 suppression
  - Timestamp-only change suppression
  - Session token change suppression
  - Actual content change NOT suppressed
  - Whitespace-only change (no suppression needed - already normalized)
  - Mixed change kept when semantic part changes

- **GenericWebTargetFalsePositive** (12 tests):
  - Timestamp-only change suppressed
  - Dynamic token change suppressed
  - Actual content change emits signal
  - Selector missing is extraction failure, not deletion
  - Selector returns empty is not deletion
  - HTTP 403 is not deletion
  - HTTP 429 is not deletion
  - Timeout is not deletion
  - Repeated extraction failure stays extraction failure
  - Malformed HTML does not crash
  - Extraction failure evidence preserves selector and reason
  - 304 short-circuit remains unchanged

### Counter-Examples Verified

| Counter-Example | Test |
|-----------------|------|
| `selector missing != deletion` | `test_selector_missing_is_extraction_failure_not_deletion` |
| `403 != deletion` | `test_http_403_is_not_deletion` |
| `429 != deletion` | `test_http_429_is_not_deletion` |
| `timeout != deletion` | `test_timeout_is_not_deletion` |
| `empty extraction != deletion` | `test_selector_returns_empty_is_not_deletion` |
| `first observation != change` | `test_first_observation_suppressed` |
| `304 != change` | Existing 304 short-circuit test |

### Full Pytest Result

```
1235 passed in 13.57s
```

---

## 7. Known Limitations

1. **Dynamic noise patterns are regex-based** and may miss edge cases or
   over-match in domain-specific content. The pattern list is configurable
   but must be maintained manually.

2. **No semantic understanding**: the system cannot distinguish between
   "meaningful timestamp" and "meaningful timestamp". All timestamps are
   treated as noise.

3. **False negative risk**: overly aggressive noise filtering could hide
   real changes. The guard only suppresses when before/after are identical
   AFTER noise filtering, minimizing this risk.

4. **No Notification layer changes**: per spec, notification architecture
   is not modified. Suppression happens at the observation/policy layer.

5. **No GitHub API**: per spec, not implemented.

---

## 8. Files Modified

- `src/web_watcher/dynamic_noise.py` — **new** (dynamic noise filter + false positive guard)
- `src/web_watcher/generic_web_target.py` — **modified** (integrated FalsePositiveGuard, enhanced evidence chain)
- `tests/test_dynamic_noise.py` — **new** (32 tests)

---

## 9. Not Implemented (Per Spec)

- GitHub API integration
- Notification architecture changes
- Dynamic noise policy final rules (this is the foundation, not the final rules)
- Selector missing final semantics (the foundation is laid, final rules are future work)
- WAF bypass / browser stealth (future parts)
