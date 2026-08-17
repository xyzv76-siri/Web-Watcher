# Phase 10C-A.2 — Final Decision Authority Refinement

**Status:** FROZEN / ACCEPTED
**Date:** 2026-08-17

## 1. Objective

Phase 10C-A.2 corrects the Decision Resolution authority model.

AI may refine Importance, but Policy retains authoritative control over Action.

This prevents AI from indirectly changing Action by changing Importance.

## 2. Frozen Decision Model

PolicyDecision contains two separate authorities:

- `importance` may be refined by AI
- `action` remains authoritative from Policy

Therefore:

`final_action = policy_decision.action`

`final_importance` may differ from the original Policy Importance.

## 3. Importance Resolution

Supported behavior:

- AI Upgrade: INTERESTING -> IMPORTANT
- AI Downgrade: IMPORTANT -> INTERESTING
- Same Importance: unchanged
- CRITICAL: cannot be downgraded by AI

## 4. Action Authority

Policy owns Action authority.

Examples:

Policy: IGNORE + DISCARD, AI: IMPORTANT -> Final: IMPORTANT + DISCARD
Policy: INTERESTING + SUMMARIZE, AI: CRITICAL -> Final: CRITICAL + SUMMARIZE

AI cannot change Action through Importance refinement.

## 5. Notification Authority

`notify_allowed` is derived exclusively from Action semantics:

- DISCARD -> False
- SUMMARIZE -> False
- NOTIFY -> True
- INVESTIGATE_AND_NOTIFY -> True

AI notification-related fields cannot override this authority.

## 6. Investigation Authority

`investigate_requested` is derived exclusively from:

`final_action == INVESTIGATE_AND_NOTIFY`

AI cannot request an investigation when Policy Action does not authorize it.

## 7. ai_overrode Semantics

`ai_overrode` means that AI changed the Policy Importance:

- Upgrade -> True
- Downgrade -> True
- Same -> False
- No AI -> False
- CRITICAL protected -> False

## 8. Implementation

Primary file: `src/web_watcher/final_decision.py`

Key changes:

1. AI Importance comparison now supports both upgrade and downgrade
2. Final Action is Policy-authoritative
3. `_ACTION_BY_IMPORTANCE` was removed
4. Notification resolution is Action-authoritative
5. Investigation resolution is Action-authoritative
6. Reason generation distinguishes elevation and downgrade

## 9. Tests

Affected test files:

- `tests/test_final_decision.py`
- `tests/test_decide.py`

Coverage includes:

- AI upgrade
- AI downgrade
- AI same importance
- CRITICAL protection
- `ai_overrode`
- final Action authority
- notification authority
- investigation authority
- full decision chain
- AI failure / fallback
- no-AI behavior

## 10. Verification

Full regression: 448 passed, 0 failed, 0 skipped
Phase 10C-A.2 targeted: 93 / 93 PASS
Legacy Action mapping: `_ACTION_BY_IMPORTANCE` -> 0 occurrences

## 11. Scope

Intentionally unchanged:

- `policy.py`
- `decide.py`
- Phase 10A AI Contract
- external integrations, Telegram, notification, investigation systems

Phase 10C-A.2 only modifies the Decision Resolution layer and its Contract tests.

## 12. Temporary Artifacts

Implementation-time backup files removed during Release Closure:

- `tests/test_decide.py.phase10c_a2.bak`
- `tests/test_final_decision.py.phase10c_a2.bak`

## 13. Git State

- Branch: master
- HEAD: 73df419
- Tracked files: 41
- Untracked Phase 10 implementation files: 12 (7 src + 4 tests + 1 report)

No Git commit or push was performed during this closure step.

## 14. Acceptance

Phase 10C-A.2 is **FROZEN / ACCEPTED**.

No further implementation should modify this Contract without an explicit new Phase and Contract review.

## 15. Recommended Next Sequence

Phase 10C-A.2 -> Release Closure -> Git Inventory -> Formal Commit -> Project Observation / Review -> Next Phase Contract -> Contract Freeze -> Implementation

---

End of Phase 10C-A.2 Report
