# PART 19-04 — GitHub API Adapter Completion Report

## API Endpoints

| Purpose | Endpoint |
|---------|----------|
| Repository metadata (stars, forks, etc.) | `GET https://api.github.com/repos/{owner}/{repo}` |
| Latest release | `GET https://api.github.com/repos/{owner}/{repo}/releases/latest` |

No HTML scraping is used. All data comes from the official GitHub REST API.

## Observation Schema

GitHub signals share the same upper-layer `ObservationContract` as Generic Web targets. The adapter returns `GitHubTargetExecutionResult` containing:

- `signals_emitted`: list of signals (not persisted directly)
- `updated_metadata`: per-target durable state for the scheduler to commit
- `outcome`: `ExecutionOutcome` enum
- `transition`: state transition metadata
- `is_304`: boolean flag for unchanged semantics
- `consecutive_failures`, `next_allowed_at`, `last_fetched_at`

### Stars Signal Payload

```json
{
  "owner": "pallets",
  "repo": "flask",
  "old_stars": 3499,
  "new_stars": 3500,
  "delta": 1,
  "source": "https://api.github.com/repos/pallets/flask",
  "fetched_at": "2026-08-20T..."
}
```

### Release Signal Payload

```json
{
  "owner": "pallets",
  "repo": "flask",
  "tag_name": "v2.1.0",
  "release_name": "Flask 2.1.0 Released",
  "html_url": "https://github.com/pallets/flask/releases/tag/v2.1.0",
  "published_at": "2026-08-18T10:00:00Z",
  "body": "Changelog details...",
  "source": "https://api.github.com/repos/pallets/flask/releases/latest",
  "fetched_at": "2026-08-20T..."
}
```

## ETag Strategy

- Per-resource ETags: `release_etag` for `/releases/latest`, `repo_etag` for `/repos/{owner}/{repo}`
- ETags are passed via `If-None-Match` on subsequent requests
- 304 responses preserve the original ETag in the result
- ETags survive restart through `target.metadata`

## Rate-Limit Strategy

- Respects GitHub `Retry-After` header on 429 responses
- Parses both seconds and HTTP-date formats
- Bounded upper limit: `_MAX_RETRY_AFTER_SECONDS = 3600` (1 hour)
- Falls back to exponential backoff (`2^attempt`) when `Retry-After` is absent
- `X-RateLimit-*` headers are exposed in fetch metadata for monitoring

## Stars Semantics

- Computes `delta = new_stars - old_stars`
- Emits `SignalType.STARS_CHANGED` only when `abs(delta) >= star_delta_threshold`
- First observation establishes baseline (`last_stars`) without emitting a signal
- Duplicate signals are prevented via per-signal fingerprinting (`_compute_signal_fingerprint`)

## Release Semantics

- Emits `SignalType.RELEASE_PUBLISHED` when `tag_name` changes
- First observation establishes baseline (`last_release_tag`) without emitting a signal
- Missing `tag_name` is treated as `SUCCESS_UNCHANGED` (no false change event)
- Malformed JSON is treated as `TRANSFORM_ERROR`

## Tests

### Adapter Tests (`test_github_repository_adapter.py`)

| Scenario | Status |
|----------|--------|
| Successful fetch | PASS |
| Correct URL construction | PASS |
| ETag header sent | PASS |
| Last-Modified header sent | PASS |
| 304 Not Modified | PASS |
| 404 / 403 error handling | PASS |
| Network error handling | PASS |
| Retry on 429 | PASS |
| Retry on 500/503 | PASS |
| Exponential backoff | PASS |
| No retry on 404 | PASS |
| Retry on URL error | PASS |
| Configurable timeout | PASS |
| Injectable sleep | PASS |
| **Retry-After seconds** | PASS |
| **Retry-After HTTP-date** | PASS |
| **Retry-After malformed** | PASS |
| **Retry-After huge (bounded)** | PASS |
| **Retry-After zero/negative** | PASS |
| **429 respects Retry-After** | PASS |
| **429 without Retry-After falls back** | PASS |
| **Malformed JSON** | PASS |
| **Empty body** | PASS |
| **Missing name/full_name** | PASS |
| **Missing stargazers_count** | PASS |
| **Per-target URL isolation** | PASS |
| **Rate limit headers in metadata** | PASS |

### Target Tests (`test_github_target.py`)

| Scenario | Status |
|----------|--------|
| Parse GitHub repo | PASS |
| Release published signal | PASS |
| Release 304 unchanged | PASS |
| Star delta signal | PASS |
| Star delta below threshold | PASS |
| Cooldown skips | PASS |
| Adapter does not persist directly | PASS |
| Mixed watch types emit both | PASS |
| Token in headers | PASS |
| **Release source URL in payload** | PASS |
| **Stars source URL in payload** | PASS |
| **Release first observation baseline** | PASS |
| **Stars first observation baseline** | PASS |
| **Missing tag_name no signal** | PASS |
| **Malformed JSON transform_error** | PASS |
| **Per-target ETag isolation** | PASS |
| **Restart ETag/last_stars/last_release_tag stable** | PASS |
| **Authentication isolation** | PASS |

## Test Count

- `test_github_repository_adapter.py`: 43 tests
- `test_github_target.py`: 18 tests
- **Total GitHub-specific tests: 61**

## Remaining Risks

1. **Real-world Retry-After formats**: GitHub may send `Retry-After` as integer seconds or HTTP-date. Both are handled, but edge cases (whitespace, mixed formats) rely on robust parsing.
2. **Rate-limit reset time**: `X-RateLimit-Reset` is exposed as raw header value. Downstream consumers must parse it.
3. **GraphQL API**: This adapter only covers the REST API endpoints specified in the task. GraphQL rate limiting is a separate concern.
4. **Token leakage**: Token is passed via `Authorization: Bearer` header. It is not logged in the adapter. The `SmartFetcher` should ensure it does not log custom headers.
5. **Snapshot defaults**: Missing `name`/`full_name`/`html_url` default to empty string to avoid `KeyError`. Downstream consumers should handle empty values gracefully.
