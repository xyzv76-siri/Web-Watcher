# FINAL_REMEDIATION_PLAN — Post-Audit GA Blocker Remediation

## Source of Truth
`FINAL_REMEDIATION_TODO.md` defines the confirmed blockers.
This plan splits them into ~6 executable parts. No production code changes until this plan is approved.

## Design Principle
- Each Part has a single primary architectural goal.
- Parts are sequenced: earlier Parts reduce risk for later Parts.
- Every Part includes: code change, tests, and an evidence artifact.
- Final acceptance requires VPS Ground Truth + independent re-audit.

---

## Part FR-01 — Host-Level Rate Limit Authority

**Goal:** Enforce per-host concurrency/rate control across all targets sharing the same host.

**Scope:**
- Introduce a persistent `HostRateLimit` authority keyed by `host` (not `target_id`).
- FetchPolicy / ScheduledRunner must consult host authority before issuing a request.
- GitHubAdapter must NOT own its own retry/sleep (moved to unified path).

**Deliverables:**
- `src/web_watcher/host_rate_limiter.py`
- `src/web_watcher/fetch_policy.py` updated to consult host limiter
- `src/web_watcher/github_repository_adapter.py` stripped of internal retry/sleep
- Tests for shared-host collision, isolated-host independence, restart stability

**Evidence:**
- Test report: host collision serializes, cross-host remains parallel
- Source diff: no adapter-private retry/sleep remains

---

## Part FR-02 — Unified Signal Vocabulary & Canonical Fingerprint

**Goal:** Eliminate production-boundary vocabulary pollution and fix Generic Web signal idempotency.

**Scope:**
- GenericWebTarget must emit domain-canonical `SignalType` values only.
- ScheduledRunner `_normalize_signal()` must be reduced to a no-op or removed.
- Generic Web signals must carry a canonical content fingerprint derived from normalized extracted text (not `target_id`).
- Repository `UNIQUE(entity_id, signal_type, fingerprint)` must continue to allow distinct signals for distinct content changes.

**Deliverables:**
- `src/web_watcher/generic_web_target.py` vocabulary + fingerprint fix
- `src/web_watcher/scheduled_runner.py` normalization cleanup
- Tests: vocabulary audit, distinct-change distinct-fingerprint, restart stability

**Evidence:**
- Vocabulary scan: no `WEB_CONTENT_CHANGED` or other non-canonical values in production path
- Fingerprint test: 3 distinct changes → 3 distinct signals

---

## Part FR-03 — Deterministic Jitter & Strict Retry-After Semantics

**Goal:** Make timing behavior deterministic and respect server instructions without local override.

**Scope:**
- Replace `random.uniform` jitter with deterministic jitter derived from stable input (target id + attempt + timestamp bucket).
- Retry-After delay must be the effective floor: `effective_delay = max(local_policy_delay, bounded_server_delay)`.
- Cooldown escalation may increase delay, but must never decrease a valid server-requested delay.

**Deliverables:**
- `src/web_watcher/fetch_policy.py` deterministic jitter
- `src/web_watcher/fetch_policy.py` Retry-After precedence fix
- Tests: same inputs → same next_allowed_at across restarts; Retry-After 3600 with escalation still ≥ 3600

**Evidence:**
- Determinism test matrix
- Retry-After precedence test cases

---

## Part FR-04 — Notification Delivery Idempotency & Claim Fencing

**Goal:** Prevent duplicate external side effects when multiple workers race.

**Scope:**
- NotificationDispatcher must claim pending notifications with a delivery lease before calling external systems.
- Duplicate claim must be prevented by fencing on `notification_id` + `claim_token` + `delivery_state`.
- InvestigationWorker already has claim/fencing; align NotificationDispatcher to the same pattern.

**Deliverables:**
- `src/web_watcher/notification_dispatcher.py` claim/delivery lease
- Repository queries updated for atomic claim
- Tests: two workers racing → only one external call; stale claim rejected

**Evidence:**
- Concurrency test: duplicate side effect count = 0
- Fencing test: stale claim cannot update state

---

## Part FR-05 — Schema Versioning, Migration & Explicit Redirect Policy

**Goal:** Replace dynamic table creation with versioned migrations; make redirect semantics explicit.

**Scope:**
- Add `schema_version` table / metadata.
- Replace ad-hoc `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` in Repository with explicit migration functions keyed by version.
- Introduce `RedirectPolicy` in FetchPolicy / Fetcher to distinguish 301/302 from 200/304/4xx/5xx explicitly.
- `allow_redirects=True` remains, but redirect hops must be recorded and policy-visible.

**Deliverables:**
- `src/web_watcher/schema.py` version + migrations
- `src/web_watcher/fetch_policy.py` redirect policy
- Tests: fresh DB versioned correctly; old DB upgraded; redirect recorded as explicit outcome

**Evidence:**
- Migration test: old DB → new schema without data loss
- Redirect test: 301 → explicit policy state, not hidden in client

---

## Part FR-06 — Production Pipeline Completeness & Final Re-Audit

**Goal:** Prove default Docker entrypoint runs the complete causal chain; produce GA-grade evidence.

**Scope:**
- Docker entrypoint / `ScheduledRunner` default must enable auto-delivery of Investigation → Notification.
- InvestigationWorker must be started by the production daemon path when enabled.
- Generate new GA evidence package: source diff, test counts by category, DB schema audit, VPS Ground Truth, and final report.
- Explicitly discard `PHASE20_06_FINAL_GA_REPORT.md` as evidence; replace with `FINAL_GA_REMEDIATION_REPORT.md`.

**Deliverables:**
- `entrypoint.sh` / `docker_run.py` full pipeline defaults
- `src/web_watcher/scheduled_runner.py` auto_deliver default review
- `FINAL_GA_REMEDIATION_REPORT.md` with category-pass/fail/blocked

**Evidence:**
- End-to-end daemon run: claim → fetch → signal → event → investigation → evidence → notification
- Test matrix by category
- Schema + migration audit
- Host rate-limit audit
- Vocabulary audit

---

## Execution Order
1. FR-01 (host limit + GitHub retry)
2. FR-02 (vocabulary + fingerprint)
3. FR-03 (jitter + Retry-After)
4. FR-04 (notification idempotency)
5. FR-05 (schema + redirect)
6. FR-06 (pipeline completeness + re-audit)

## Non-Negotiables
- No feature creep beyond these 6 Parts.
- No changes to dynamic-noise final rules, notification final rules, WAF/browser stealth, GitHub API expansion.
- Every Part must be test-backed before merge.
- Final GA verdict must be derived from source + DB + runtime evidence, not self-reported pass.
