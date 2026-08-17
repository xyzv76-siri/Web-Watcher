# PHASE 11-A — K.6 Architecture Review

Status: REVISED — FINDINGS RESOLVED — APPROVED
Phase: K.6 Investigation Engine
Reviewer: AI (automated contract-consistency + cross-layer audit)
Date: 2026-08-17
Review Target: `PHASE11A_K6_ARCHITECTURE.md` (676 lines, 21 sections)

---

## Gate Check (Pre-Review)

| Check | Result |
|---|---|
| K.6 Python files do not exist | PASS |
| K.1–K.5 protected files zero diff | PASS |
| Phase 10 protected files zero diff | PASS |
| `git diff --check` PASS | PASS |
| Tests 763/763 PASS | PASS |
| compileall no errors | PASS |
| Only 1 new file (architecture doc) | PASS |

---

## Final Architecture Review — Mandatory Corrections Verification

### A1 — Engine Input Boundary

**Requirement**: K.6 must not receive a Planner. It must receive `InvestigationPlan` directly.

**Verification**:
- §3 Dependency Boundary: K.6 depends on K.5: `PlanStep, InvestigationPlan, PlannerError` — no `Planner`
- §3 "must NOT depend on": `- K.5 Planner class` — explicitly forbidden
- §6 Engine Input Boundary: dedicated section with correct/incorrect call patterns
- §18 Minimum Engine Contract: `Engine.__init__(policy)` + `Engine.execute(plan, context)` — no Planner
- `grep 'Planner'` output confirms: only mentions in §2 data flow (producer), §3 forbidden list, §6 boundary docs, §12.1 PlannerError handling — no dependency

**Status**: RESOLVED

---

### A2 — Finding Construction Boundary

**Requirement**: K.6 must NOT construct InvestigationFinding. Must use `findings=()`.

**Verification**:
- §8 Finding Construction Boundary: dedicated section explaining evidence ≠ finding
- §4 Responsibilities: "constructing InvestigationResult with findings=()"
- §8: `findings = ()` — explicit declaration
- §12.1 PlannerError: `findings=()`
- §12.2 Tool execution error: `findings=()`
- §12.3 ToolResult success=False: `findings=()`
- §12.4 Timeout: `findings=()`
- §20 Construction Rules: `findings: ()`
- `grep 'findings'` output: 11 occurrences, all `findings=()` or `findings: ()`

**Status**: RESOLVED

---

### A3 — INCONCLUSIVE Boundary

**Requirement**: K.6 must NOT infer INCONCLUSIVE.

**Verification**:
- §10.1 Possible Statuses: SUCCESS, FAILED, TIMEOUT, BUDGET_EXCEEDED — INCONCLUSIVE absent
- §10.3 Multiple Conditions: INCONCLUSIVE absent from precedence
- §11 INCONCLUSIVE Boundary: dedicated section explaining why INCONCLUSIVE is not produced
- §20 Construction Rules: "INCONCLUSIVE is not produced by K.6"
- `grep 'INCONCLUSIVE'` output: 10 occurrences, all explanatory/negative references, none as a produced status

**Status**: RESOLVED

---

### Timeout Semantics — Classification vs. Cancellation

**Requirement**: K.6 must distinguish timeout classification from hard cancellation.

**Verification**:
- §10.4: "Timeout classification / deadline enforcement: guaranteed by §17"
- §10.4: "Hard interruption / cancellation: NOT guaranteed"
- §10.4: "Engine does not attempt to cancel a blocking synchronous call"
- §19: "K.6 provides timeout classification and deadline enforcement. It does NOT provide hard interruption or cancellation."
- §19: Explicit list of what K.6 does NOT do (sleep, polling, threading, signals)

**Status**: RESOLVED

---

### max_pages — No Clamping

**Requirement**: pages_checked must accumulate actual ToolResult.pages_fetched. No clamping.

**Verification**:
- §14.2: "No clamping, no rewriting of actual values."
- §14.2: Example showing 8 + 5 = 13 > 10 → BUDGET_EXCEEDED
- §20: "pages_checked: sum of actual ToolResult.pages_fetched (no clamping)"
- `grep 'clamp'` output: only "no clamping" mentions

**Status**: RESOLVED

---

### max_steps — Correct Accounting

**Requirement**: steps_used = number of PlanSteps for which Tool.execute() was entered. If budget prevents, Tool.execute() is NOT called and steps_used unchanged.

**Verification**:
- §14.1: "steps_used = number of PlanSteps for which Tool.execute() was actually entered"
- §14.1: Pre-execution budget check: if budget exceeded, "Tool.execute() is NOT called, steps_used remains unchanged"
- §14.1: "If Tool.execute() is entered: steps_used = steps_used + 1. Regardless of whether Tool.execute() succeeds or raises"

**Status**: RESOLVED

---

### Status Precedence

**Requirement**: Deterministic precedence documented.

**Verification**:
- §10.2: Four rules in order: TIMEOUT (Rule 1) → BUDGET_EXCEEDED (Rule 2) → FAILED (Rule 3) → SUCCESS (Rule 4)
- §10.3: "The rule with the lowest rule number wins"

**Status**: RESOLVED

---

### evidence_refs — Positional Index

**Requirement**: Evidence has no unique ID. evidence_refs uses zero-based positional string indices.

**Verification**:
- §9.1: "Evidence indices are zero-based and assigned in collection order"
- §9.3: Example showing `("0", "1")` referencing `Evidence A` and `Evidence B`
- §9.3: "K.6 does not assign evidence_refs because it does not construct findings"
- §20: "zero-based positional order preserved"

**Status**: RESOLVED

---

### No Retry / No Replanning

**Requirement**: K.6 must not retry Tool.execute() or re-plan.

**Verification**:
- §5: "The Engine does not: retry Tool.execute() on failure, construct alternative plans"
- §4: "dynamic replanning" listed as NOT responsible
- §18: "The Engine does NOT: ... store a Planner reference, call Planner.plan()"

**Status**: RESOLVED

---

### No AI

**Requirement**: K.6 must not import Phase 10 or AI modules.

**Verification**:
- §3 "must NOT depend on": "Phase 10 AI modules (ai_contract, decide, final_decision, etc.)"
- §3 "must NOT depend on": "K.5 Planner class"
- `grep 'ai_contract\|decide\|final_decision\|AI'` on architecture doc: only in "must NOT depend on" sections

**Status**: RESOLVED

---

## Post-Revision Gate Check

| Check | Result |
|---|---|
| 8/8 mandatory corrections resolved | PASS |
| 0 new findings | PASS |
| K.6 Python files do not exist | PASS |
| K.1–K.5 protected files zero diff | PASS |
| Phase 10 protected files zero diff | PASS |
| `git diff --check` PASS | PASS |
| Tests 763/763 PASS | PASS |

---

## Section Structure

| # | Section | Status |
|---|---|---|
| 1 | Purpose | REVISED |
| 2 | Architectural Position | REVISED |
| 3 | Dependency Boundary | REVISED |
| 4 | Responsibilities | REVISED |
| 5 | Execution Model | REVISED |
| 6 | Engine Input Boundary | REVISED (NEW — A1) |
| 7 | Context Forwarding | REVISED |
| 8 | Finding Construction Boundary | REVISED (NEW — A2) |
| 9 | Evidence Collection and Indexing | REVISED |
| 10 | Status Determination | REVISED |
| 11 | INCONCLUSIVE Boundary | REVISED (NEW — A3) |
| 12 | Failure Handling | REVISED |
| 13 | Engine Error | REVISED |
| 14 | Budget Enforcement | REVISED |
| 15 | State Model | REVISED |
| 16 | Determinism | REVISED |
| 17 | Public API Scope | REVISED |
| 18 | Minimum Engine Contract | REVISED |
| 19 | Timeout Implementation Detail | REVISED |
| 20 | InvestigationResult Construction Rules | REVISED |
| 21 | Architecture Freeze | UNCHANGED |

---

## Verdict

**APPROVED** — All mandatory corrections applied, architecture internally consistent,
all boundaries correctly enforced, no open findings.

K.6 implementation authorization may be granted upon human sign-off.
Implementation target: `src/web_watcher/investigation_engine.py` and
`tests/test_investigation_engine.py` only.