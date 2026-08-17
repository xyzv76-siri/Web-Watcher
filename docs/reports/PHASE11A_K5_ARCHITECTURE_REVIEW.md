# PHASE 11-A — K.5 Architecture Review

Status: REVISED — FINDINGS RESOLVED — APPROVED
Phase: K.5 Investigation Planner
Reviewer: AI (automated contract-consistency + cross-layer audit)
Date: 2026-08-17
Review Target: `PHASE11A_K5_ARCHITECTURE.md` (415 lines, 19 sections)

---

## Gate Check (Pre-Review)

| Check | Result |
|---|---|
| K.5/K.6 Python files do not exist | PASS |
| K.1–K.4 protected files zero diff | PASS |
| Phase 10 protected files zero diff | PASS |
| Tests 695/695 PASS | PASS |
| compileall no errors | PASS |
| Only 1 new file (architecture doc) | PASS |

---

## First Review — 5 Findings

### F1 — HIGH: Task→Capability mapping causes permanent ambiguity

**Section**: §6 Task → Capability Mapping

**Finding**: The original mapping assigned `FETCH_RELATED_SOURCE → WEB_SEARCH / WEB_FETCH`
and `CROSS_CHECK → WEB_SEARCH / WEB_FETCH`. With the standard 4-MockTool set (each tool
declaring exactly one capability), this causes both tasks to match two tools simultaneously,
violating §5's "exactly one compatible Tool" rule. 2 of 5 tasks (40%) would permanently
fail with an ambiguity error.

**Resolution**: Mapping narrowed to single-capability per task:
- `FETCH_RELATED_SOURCE → WEB_SEARCH`
- `CROSS_CHECK → WEB_SEARCH`

All 5 tasks now have exactly one matching tool in the standard MockTool set:
| Task | Capability | Matching Tool | Result |
|---|---|---|---|
| VERIFY_SOURCE | WEB_FETCH | MockWebFetchTool | ✅ Unique |
| FETCH_RELATED_SOURCE | WEB_SEARCH | MockWebSearchTool | ✅ Unique |
| COMPARE_WITH_HISTORY | HISTORICAL_LOOKUP | MockHistoricalLookupTool | ✅ Unique |
| EXTRACT_EVIDENCE | PAGE_PARSE | MockPageParseTool | ✅ Unique |
| CROSS_CHECK | WEB_SEARCH | MockWebSearchTool | ✅ Unique |

**Status**: RESOLVED

---

### F2 — MEDIUM: max_steps static check is vacuous in single-step model

**Section**: §9 Policy Boundary

**Finding**: With K.5 producing exactly one PlanStep and K.1 guaranteeing `max_steps >= 1`,
the static check `planned steps <= max_steps` is always satisfied and never rejects.

**Resolution**: §9 revised to explicitly document that this check is a "defensive invariant"
rather than an active constraint. The multi-step planning behavior is deferred to a future
architecture revision.

**Status**: RESOLVED

---

### F3 — MEDIUM: Planner method signature unspecified

**Section**: §15 (was underspecified)

**Finding**: The public `Planner` class was listed but its constructor and `plan()` method
signatures were not defined, leaving ambiguity about required parameters and their semantics.

**Resolution**: §16 "Minimum Planner Contract" added with full signatures:
```
class Planner:
    def __init__(
        self,
        tools: Sequence[Tool],
        policy: InvestigationPolicy,
        tool_provider: ToolProvider | None = None,
    ) -> None: ...

    def plan(
        self,
        task: InvestigationTask | None = None,
        context: Mapping[str, str] = ...,
    ) -> InvestigationPlan: ...
```

**Status**: RESOLVED

---

### F4 — LOW-MEDIUM: ToolProvider optionality ambiguity

**Section**: §4 ToolProvider Contract

**Finding**: "ToolProvider is optional" at the top of §4 followed by "a ToolProvider is
required" under the `task=None` case could be misread.

**Resolution**: §17 "ToolProvider Optionality" added with an explicit decision table:
- task provided → ToolProvider not required
- task absent, ToolProvider exists → `suggest_task(context)`
- task absent, ToolProvider absent → `PlannerError`

**Status**: RESOLVED

---

### F5 — LOW-MEDIUM: `tuple[PlanStep, ...]` implies multi-step

**Section**: §8 InvestigationPlan

**Finding**: The `...` variadic form of `tuple[PlanStep, ...]` implied multi-step support
contradicting §1's single-task → single-step model.

**Resolution**: §1 revised to explicitly state "exactly one Tool action" and "Multi-step
planning is deferred." §8 annotated with: "For the current Phase 11-A architecture,
`steps` must contain exactly one PlanStep."

**Status**: RESOLVED

---

## Revised Architecture

**File**: `PHASE11A_K5_ARCHITECTURE.md`
**Changes**: 19 sections (was 17), 415 lines (was 348)

### Section structure

| # | Section | Status |
|---|---|---|
| 1 | Purpose | REVISED (F1/F5) |
| 2 | Architectural Position | UNCHANGED |
| 3 | Responsibilities | UNCHANGED |
| 4 | ToolProvider Contract | UNCHANGED |
| 5 | Determinism | UNCHANGED |
| 6 | Task → Capability Mapping | REVISED (F1) |
| 7 | PlanStep | UNCHANGED |
| 8 | InvestigationPlan | REVISED (F5) |
| 9 | Policy Boundary | REVISED (F2) |
| 10 | Failure Model | UNCHANGED |
| 11 | Dependency Boundary | UNCHANGED |
| 12 | State Model | UNCHANGED |
| 13 | Execution Boundary | UNCHANGED |
| 14 | Replanning | UNCHANGED |
| 15 | Public API Scope | UNCHANGED |
| 16 | Minimum Planner Contract | NEW (F3) |
| 17 | ToolProvider Optionality | NEW (F4) |
| 18 | Implementation Constraints | RENumbered (was 16) |
| 19 | Architecture Freeze | RENumbered (was 17) |

---

## Post-Revision Gate Check

| Check | Result |
|---|---|
| 5/5 findings resolved | PASS |
| 0 new findings | PASS |
| K.5/K.6 Python files do not exist | PASS |
| K.1–K.4 protected files zero diff | PASS |
| Phase 10 protected files zero diff | PASS |
| Tests 695/695 PASS | PASS |
| compileall no errors | PASS |
| All 19 sections numbered and consistent | PASS |
| No cross-section contradictions | PASS |

---

## Verdict

**APPROVED** — All 5 findings resolved, architecture internally consistent,
dependencies correctly bounded, single-step model explicitly documented.

K.5 implementation authorization may be granted upon human sign-off.
Implementation target: `src/web_watcher/investigation_planner.py` and
`tests/test_investigation_planner.py` only.

K.6 remains out of scope until K.5 is fully implemented, tested,
audited, and committed.