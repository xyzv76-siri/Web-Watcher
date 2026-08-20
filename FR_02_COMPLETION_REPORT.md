# FR_02 — Unified Signal Vocabulary & Canonical Fingerprint: Completion Report

## Summary
Fixed Generic Web signal emission to use the canonical domain vocabulary and
a deterministic content fingerprint, enabling reliable deduplication across
process restarts.

## Changes

### Modified Files
- `src/web_watcher/generic_web_target.py` — `GenericWebTarget` now emits
  `SignalType.CONTENT_CHANGE` instead of the non-canonical `"WEB_CONTENT_CHANGED"`.
  The emitted payload includes a `content_hash` computed as a deterministic
  hash of all sorted normalized values.

- `src/web_watcher/scheduled_runner.py` — `_normalize_signal()` default
  `signal_type` changed from `"WEB_CONTENT_CHANGED"` to
  `SignalType.CONTENT_CHANGE.value`. Removed the `target_id` fallback for
  `fingerprint`; when `content_hash` is absent the fingerprint is now `None`
  instead of being silently mis-attributed to the target ID.

- `tests/test_phase20_01_causality.py` — Updated one assertion to expect the
  canonical `"content_change"` vocabulary value.

### New Files
- `tests/test_fr02_vocabulary_fingerprint.py` — 5 tests covering:
  - canonical vocabulary emission
  - distinct content → distinct fingerprint (3 distinct changes → 3 distinct signals)
  - restart stability (same content → same fingerprint)
  - normalize_signal vocabulary default
  - normalize_signal fingerprint fallback removed

## Evidence

### Test Results
- `tests/test_fr02_vocabulary_fingerprint.py`: 5 passed
- Full pytest: **1342 passed**

### Vocabulary Fix
- `GenericWebTarget` now emits `SignalType.CONTENT_CHANGE.value == "content_change"`
- No production code path emits `"WEB_CONTENT_CHANGED"` any longer

### Fingerprint Fix
- Payload now includes `content_hash = sha256(sorted_normalized_values)`
- `_normalize_signal()` reads `content_hash` from payload and assigns it to
  `Signal.fingerprint`
- Three distinct content sets produce three distinct fingerprints
- Same content set produces the same fingerprint across process restarts
  (stable hashing of sorted normalized values + same target)

### Deduplication
- `repository.py` enforces `UNIQUE(entity_id, signal_type, fingerprint)`
- With distinct fingerprints per distinct change, genuine changes are no longer
  falsely deduplicated
- With deterministic fingerprints, duplicate observations of the same change are
  correctly deduplicated
