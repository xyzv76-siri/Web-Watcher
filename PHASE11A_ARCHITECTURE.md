# Phase 11-A — Architecture Freeze

**Date:** 2026-08-17
**Status:** ARCHITECTURE FREEZE — PENDING HUMAN REVIEW
**Baseline:** Web-Watcher, `master` at `1266c9e`
**Git Remote:** `origin git@github.com:xyzv76-siri/Web-Watcher.git`

---

## Preface — What This Document Is

This document freezes the Phase 11-A architecture **before** K.3 implementation begins.
It is grounded exclusively in:

- The **current live codebase** (Phase 2 through 10C-A.2, all committed to `1266c9e`)
- The **actual implementation** of K.1 (`investigation_contract.py`) and K.2 (`investigation_evidence.py`)
- The **Phase 10C-A.2 Decision Authority** contract (FROZEN / ACCEPTED)
- The **Architecture Review v2** report (read-only audit)

It does **not** assume K.3 is approved. It does **not** invent interfaces that do not exist.
Sections that describe future modules (K.3+) are explicitly labelled **"Candidate Design — Pending Approval"**.

---

## 1. Phase 11-A Ultimate Goal

Phase 11-A delivers a **bounded, auditable, non-autonomous investigation layer** for Web-Watcher.

When `FinalDecision.final_action == INVESTIGATE_AND_NOTIFY` and `investigate_requested == True`,
the system may collect evidence to support or refute the claims implied by the decision,
within strict budget limits.

The investigation produces:

- Immutable `Evidence` records (K.2 — delivered)
- An `InvestigationResult` (K.3 — **candidate design, not yet implemented**)
- A bounded number of tool invocations (K.4 — **candidate design, not yet implemented**)

Phase 11-A does **not** make the system autonomous. It does **not** allow investigation to
modify Policy, final_action, or the decision itself. Investigation failure leaves the
FinalDecision untouched.

---

## 2. What Problem Does Investigation Solve?

`FinalDecision` identifies that an event warrants investigation (via Policy action
`INVESTIGATE_AND_NOTIFY`). It does **not** perform investigation — `investigate_requested`
is a boolean permission flag, not an execution trigger.

Phase 11-A provides the execution layer:

> When the decision says "investigate," what does that mean concretely,
> what evidence can be collected, how is it bounded, and how is it recorded?

Without Phase 11-A, the investigation branch of `FinalDecision` is unreachable:
the system knows an investigation is requested but has no mechanism to perform one.

---

## 3. Phase 10 Decision Layer ↔ Phase 11-A Investigation Layer Boundary

The boundary is defined by the Phase 10C-A.2 FROZEN contract:

| Boundary Property | Phase 10 (Decision) | Phase 11-A (Investigation) |
|---|---|---|
| **Owns** | `Action` authority, `Importance` refinement rules, `FinalDecision` | Evidence collection, result production |
| **Consumes** | `Event`, `AIJudgment` | `FinalDecision`, `Event` |
| **Produces** | `FinalDecision` (immutable) | `InvestigationResult` (candidate — immutable) |
| **May modify** | Nothing (pure resolution) | Nothing (no mutations allowed) |
| **May NOT modify** | `Event`, `PolicyDecision`, `AIJudgment` | `FinalDecision`, `policy_decision`, `final_importance`, `final_action`, `notify_allowed`, `investigate_requested` |

**CRITICAL INVARIANT:** Investigation failure must **never** rewrite `FinalDecision`.
The Phase 10 authority model is not negotiable from the investigation layer.

### Trigger condition

Investigation **may** run only when ALL of:

1. `final_action == Action.INVESTIGATE_AND_NOTIFY`
2. `investigate_requested == True`
3. `FinalDecision.status == DecisionStatus.RESOLVED` (not `AI_UNAVAILABLE`)

When `status == AI_UNAVAILABLE`, investigation is blocked regardless of action — this
is the Phase 10C-A.1 invariant that even CRITICAL events with no AI do not authorize
investigation or notification.

---

## 4. K.1 — What Has Been Delivered

**File:** `src/web_watcher/investigation_contract.py` (118 lines)

### Types delivered

| Type | Kind | Purpose |
|---|---|---|
| `InvestigationTask` | `str, Enum` | 5 allowed task categories |
| `ToolCapability` | `str, Enum` | 4 capability identifiers |
| `InvestigationPolicy` | `@dataclass(frozen=True)` | Budget constraints (max_steps, max_pages, timeout_seconds) |
| `PolicyValidationError` | Exception | Validation error for InvestigationPolicy |
| `ToolProvider` | `Protocol` | Investigation-specific AI planning protocol |

### Key design decisions in K.1

1. **`ToolProvider` is a separate Protocol** — it does not reuse `AIProvider` from `ai_contract.py`. The Investigation planning AI is a different concern from the Decision AI.

2. **`ToolProvider.suggest_task()` returns `InvestigationTask`** — the return type is tightly constrained. The provider cannot create new tasks; it can only choose from the closed enum.

3. **`InvestigationPolicy` is validated at construction** — `max_steps >= 1`, `max_pages >= 0`, `timeout_seconds > 0`. Invalid values raise `PolicyValidationError`.

4. **`InvestigationPolicy` defaults** — `max_steps=5`, `max_pages=10`, `timeout_seconds=60.0`. These are policy/configuration values, not magic numbers scattered through the Engine.

5. **K.1 does NOT import Phase 10 modules** — zero imports of `policy.py`, `decide.py`, `final_decision.py`, `ai_contract.py`. This maintains clean separation.

6. **`ToolProvider` context immutability is a semantic contract (finding F1)** — `context: Mapping[str, str]` provides read-only access in the type system only. At runtime, a `dict` passed as `context` could be mutated by a provider implementation. The Planner (K.5) MUST defensively copy any mutable context before passing it to a `ToolProvider`. This is a K.5 implementation requirement, not a K.1 change. The frozen nature of `InvestigationTask` (the return type) is the only structural guard; the planner is responsible for all runtime guarantees.

### What K.1 does NOT deliver

- No execution logic
- No tool implementations
- No planner
- No evidence
- No result
- No Engine

K.1 is **purely a contract layer**.

### Tests delivered

`tests/test_investigation_contract.py` — **43 tests**, all passing.

---

## 5. K.2 — What Has Been Delivered

**File:** `src/web_watcher/investigation_evidence.py` (85 lines)

### Types delivered

| Type | Kind | Purpose |
|---|---|---|
| `EvidenceType` | `str, Enum` | 4 evidence classifications |
| `Evidence` | `@dataclass(frozen=True)` | Immutable evidence record |

### `Evidence` fields

| Field | Type | Validation |
|---|---|---|
| `source` | `str` | Non-empty |
| `url` | `str` | Non-empty |
| `retrieved_at` | `datetime` | Must be `datetime` instance |
| `claim` | `str` | Non-empty |
| `supporting_text` | `str` | Non-empty |
| `evidence_type` | `EvidenceType` | Must be `EvidenceType` member |

### Key design decisions in K.2

1. **`retrieved_at` is caller-provided** — no `datetime.now()` default, no timezone conversion. The caller must supply the timestamp. This preserves determinism and prevents hidden side effects.

2. **No URL validation beyond non-empty string** — no `urllib`, no `requests`, no network reachability check. Evidence is a data record, not a fetcher.

3. **`Evidence` is fully immutable** — all six fields are frozen; equality and hashing work by default via dataclass generation.

4. **K.2 depends only on K.1** — `EvidenceType` is defined in `investigation_evidence.py`, not in `investigation_contract.py`. The dependency direction is:

```
investigation_contract.py
        ↑
investigation_evidence.py
```

5. **`Evidence` has no unique identifier (finding F2)** — `Evidence` is identified only by its 6 field values. Two evidence records with identical field values are indistinguishable. This is an **accepted limitation** of the K.2 contract while K.2 remains frozen. See §13 (Evidence Traceability) for the mitigation: `InvestigationFinding.evidence_refs` must use positional indices into the `InvestigationResult.evidence` tuple, not a unique ID.

### What K.2 does NOT deliver

- No evidence collection
- No evidence storage
- No evidence deduplication
- No fetcher, parser, or tool
- No result or status

K.2 is **purely a data model layer**.

### Tests delivered

`tests/test_investigation_evidence.py` — **44 tests**, all passing.

---

## 6. K.1 ↔ K.2 Data Relationship

```
investigation_contract.py                        investigation_evidence.py
┌─────────────────────┐                           ┌─────────────────────────┐
│ InvestigationTask   │                           │ EvidenceType (enum)     │
│   VERIFY_SOURCE     │                           │   PRIMARY               │
│   FETCH_RELATED_SOURCE │                       │   SECONDARY             │
│   COMPARE_WITH_HISTORY │                       │   HISTORICAL            │
│   EXTRACT_EVIDENCE  │                          │   DERIVED               │
│   CROSS_CHECK       │                           │                         │
├─────────────────────┤                           ├─────────────────────────┤
│ ToolCapability      │                           │ Evidence (dataclass)    │
│   WEB_FETCH         │                           │   source: str           │
│   WEB_SEARCH        │                           │   url: str              │
│   PAGE_PARSE        │                           │   retrieved_at: datetime│
│   HISTORICAL_LOOKUP │                           │   claim: str            │
├─────────────────────┤                           │   supporting_text: str  │
│ InvestigationPolicy │                            │   evidence_type:       │
│   max_steps: 5      │                            │     EvidenceType       │
│   max_pages: 10     │                            └─────────────────────────┘
│   timeout: 60.0     │
├─────────────────────┤
│ ToolProvider (Proto)│
│   suggest_task() →  │
│     InvestigationTask│
└─────────────────────┘
```

**No data flows between these two modules currently.** They are independent sibling
layers at the same architectural tier. Their relationship will be mediated by future
modules (K.3+) that consume both:

- A Tool (K.4) would declare `ToolCapability` and return `Evidence` records
- A Planner (K.5) would use `ToolProvider` to select `InvestigationTask` values
- An Engine (K.6) would enforce `InvestigationPolicy` budget constraints

---

## 7. K.3 — What It Should Solve (Candidate Design)

**Status: CANDIDATE DESIGN — PENDING APPROVAL**
**No files created. No implementation started.**

### Proposed responsibility

K.3 introduces the **`InvestigationResult`** data model and its associated status enum.

### Proposed types

| Type | Kind | Purpose |
|---|---|---|
| `InvestigationStatus` | `str, Enum` | 5 status values: SUCCESS, INCONCLUSIVE, FAILED, TIMEOUT, BUDGET_EXCEEDED |
| `InvestigationFinding` | `@dataclass(frozen=True)` | A single finding with claim, status, evidence references |
| `InvestigationResult` | `@dataclass(frozen=True)` | Aggregate result: status, summary, findings, evidence, confidence, steps_used, pages_checked, failure_reason |

### Evidence reference model (finding F2 mitigation)

`InvestigationFinding.evidence_refs` is defined as `tuple[str, ...]` but `Evidence` has no unique identifier (accepted K.2 limitation, §5 design decision 5). The reference model is **positional indices**: each element in `evidence_refs` is a string representation of the zero-based index into `InvestigationResult.evidence`.

Example:

```python
result = InvestigationResult(
    status=InvestigationStatus.SUCCESS,
    summary="source verified",
    findings=(
        InvestigationFinding(claim="X", status="supported", evidence_refs=("0", "2")),
    ),
    evidence=(
        Evidence(source="A", ...),   # index 0
        Evidence(source="B", ...),   # index 1
        Evidence(source="A", ...),   # index 2 (same source, different context)
    ),
    ...
)
```

This model:
- Requires no modification to K.2 `Evidence` (stays frozen)
- Is stable within a single `InvestigationResult` (tuple indices do not change)
- Does not support cross-result evidence referencing (that requires `evidence_id`, a future concern)
- Must be documented in K.3's own implementation as the canonical reference model

### Proposed dependencies

- `Evidence` (from K.2) — `InvestigationResult.evidence: tuple[Evidence, ...]`
- `InvestigationStatus` is a new enum defined in K.3

### Proposed non-dependencies

- No dependency on `FinalDecision` (the Result does not need to know about the decision)
- No dependency on any AI module
- No dependency on Tool or Planner modules

### Acceptance criteria (proposed, not yet approved)

1. `InvestigationResult` is immutable (`frozen=True`)
2. `InvestigationStatus` has exactly 5 values
3. `failure_reason` is empty string when status is SUCCESS or INCONCLUSIVE
4. `confidence` is a float in [0.0, 1.0]
5. `steps_used` and `pages_checked` are non-negative ints
6. `evidence` is a tuple of `Evidence` (K.2)
7. All existing 535 tests remain passing
8. Targeted tests for K.3 pass (proposed: ~25 tests)

### Investigation State Transition Table (finding F5)

The following table defines the complete state machine for the investigation loop.
**All phases MUST respect this table.** The Engine MUST NOT enter an undefined state.

| Current State | Event | Next State | Action |
|---|---|---|---|
| **READY** (precondition check passed) | `PlannerDecision.execute == True` | **RUNNING** | Execute step 1 |
| **READY** | `PlannerDecision.execute == False` (no tasks proposed) | **INCONCLUSIVE** | Return result, `evidence == ()` |
| **RUNNING** | `PlannerDecision.execute == True` and `steps_used < max_steps` | **RUNNING** | Execute next step |
| **RUNNING** | `PlannerDecision.execute == True` and `steps_used >= max_steps` | **BUDGET_EXCEEDED** | Stop, report "max_steps exhausted" |
| **RUNNING** | `PlannerDecision.execute == True` and `pages_checked >= max_pages` | **BUDGET_EXCEEDED** | Stop, report "max_pages exhausted" |
| **RUNNING** | `PlannerDecision.execute == True` and `elapsed_seconds >= timeout_seconds` | **TIMEOUT** | Stop, report "timeout_seconds exceeded" |
| **RUNNING** | `PlannerDecision.execute == True` and tool raises exception | **FAILED** | Stop, report error |
| **RUNNING** | `PlannerDecision.execute == False` and `evidence == ()` | **INCONCLUSIVE** | Return result with `evidence == ()` |
| **RUNNING** | `PlannerDecision.execute == False` and `evidence != ()` | **SUCCESS** | Return result with collected evidence |
| **SUCCESS** | — | (terminal) | No further action |
| **INCONCLUSIVE** | — | (terminal) | No further action |
| **FAILED** | — | (terminal) | No further action |
| **TIMEOUT** | — | (terminal) | No further action |
| **BUDGET_EXCEEDED** | — | (terminal) | No further action |

### Hard loop guard

The `max_steps` limit is a **hard upper bound** on total Planner evaluations. The Engine MUST NOT call `Planner.plan()` more than `max_steps` times total, regardless of what the Planner returns. This guarantees:

1. The Engine terminates within a known number of iterations
2. A Planner returning `execute == True` forever still cannot exceed `max_steps`
3. `BUDGET_EXCEEDED` is the fallback when the Planner exhausts the step budget without returning `execute == False`

### Budget check order (two-sided enforcement, finding F3)

Budget is checked **before** AND **after** each step execution:

| Check | When | Guards against | Breach result |
|---|---|---|---|
| **Pre-step timeout check** | Before `Tool.execute()` | Tool taking too long to start | `TIMEOUT` |
| **Pre-step budget check** | Before `Tool.execute()` | Step count or page count already exhausted | `BUDGET_EXCEEDED` |
| **Post-step elapsed check** | After `Tool.execute()` | Tool execution exceeded remaining budget | `TIMEOUT` |
| **Post-step page count check** | After `Tool.execute()` | `pages_checked` exceeds `max_pages` | `BUDGET_EXCEEDED` |

The tool's own execution must be bounded by `remaining_budget - safety_margin` seconds. If a tool execution exceeds this limit, the post-step elapsed check catches it and returns `TIMEOUT`.

---

## 8. K.4+ — Candidate Phase Map

All phases beyond K.2 are **CANDIDATE DESIGN — PENDING APPROVAL**.
They are listed here to clarify the expected architecture, but they must not be
implemented until explicitly approved.

### K.4 — Tool Layer (Candidate Design)

**Proposed responsibility:** Define the `Tool` Protocol and provide Mock/Fake implementations.

**Proposed types:**

| Type | Purpose |
|---|---|
| `ToolResult` | Immutable result of a single tool execution |
| `Tool` Protocol | Interface: `capabilities()` and `execute(task, context)` |
| 4 Mock tools | `MockWebFetchTool`, `MockWebSearchTool`, `MockPageParseTool`, `MockHistoricalLookupTool` |

**Proposed dependency:** `ToolCapability` (K.1), `Evidence` (K.2), `InvestigationTask` (K.1)

**Proposed non-dependencies:** No real network access. No `requests`, `httpx`, `urllib`.

### K.5 — Planner Layer (Candidate Design)

**Proposed responsibility:** `InvestigationPlanner` validates proposed tasks before execution.

**Validation rules:**

1. Task is an allowed `InvestigationTask` enum value
2. Required `ToolCapability` exists among registered Tools
3. Budget (steps, pages, time) has not been exhausted
4. Tool is authorized (no arbitrary command execution)
5. Task is valid for the current investigation state

**Proposed dependency:** `ToolProvider` (K.1), `Tool` (K.4), `InvestigationPolicy` (K.1)

### K.6 — Engine Layer (Candidate Design)

**Proposed responsibility:** `InvestigationEngine` orchestrates the investigation loop.

**Responsibilities:**

- Check preconditions (FinalDecision trigger condition)
- Run the step-by-step loop bounded by `InvestigationPolicy`
- Collect `Evidence` from Tool executions
- Return an immutable `InvestigationResult`
- Never modify `FinalDecision` or any Phase 10 state

**Proposed dependency:** All K.1 through K.5 types, plus `FinalDecision` (Phase 10C-A) and `Event` (Phase 2)

### Phase map summary

| Phase | Status | Module | Tests |
|---|---|---|---|
| K.1 | ✅ DONE | `investigation_contract.py` | 43 |
| K.2 | ✅ DONE | `investigation_evidence.py` | 44 |
| K.3 | ⬜ CANDIDATE | `investigation_result.py` (proposed) | ~25 (proposed) |
| K.4 | ⬜ CANDIDATE | `investigation_tools.py` (proposed) | ~35 (proposed) |
| K.5 | ⬜ CANDIDATE | `investigation_planner.py` (proposed) | ~30 (proposed) |
| K.6 | ⬜ CANDIDATE | `investigation.py` (proposed) | ~35 (proposed) |

**No dates, no commitments, no implementation plans** are established for K.3+.
Each phase requires its own Architecture Review and Approval before implementation begins.

---

## 9. Complete Data Flow (Current and Proposed)

### 9.1 Current (implemented as of K.2)

```
Event
  ↓ (Phase 9 — Policy)
PolicyEngine.evaluate(event)
  ↓
PolicyDecision
  ↓ (Phase 10A — AI)
AIContext(event, policy_decision)
  ↓
AIJudge.judge(context)
  ↓
AIJudgment  (or AIError → None)
  ↓ (Phase 10C-A.1/2 — Resolution)
resolve(policy_decision, ai_judgment)
  ↓
FinalDecision
  └─ investigate_requested == True
  └─ final_action == INVESTIGATE_AND_NOTIFY
  └─ status == RESOLVED
     ↓
  [INVESTIGATION NEEDED — but no implementation exists yet]
```

### 9.2 Proposed (Phase 11-A, candidate)

```
FinalDecision  +  Event
  ↓ (Precondition check: investigate_requested, action, status)
InvestigationEngine.run(decision, event)
  ↓
InvestigationPlanner.plan(context)
  ↓ (if execute=True)
Tool.execute(task, context)
  ↓
ToolResult  →  Evidence records
  ↓ (loop: bounded by InvestigationPolicy)
Evidence collection complete
  ↓
InvestigationResult(status, summary, findings, evidence, ...)
  ↓
Back to Decision Layer (read-only — no mutation of FinalDecision)
```

### Key architectural guarantees in this flow

- **No backward data flow** — investigation results do not flow back into `FinalDecision`
- **No mutation** — all dataclasses are frozen; all data flows through new objects
- **No autonomous loop** — the Engine runs once; it does not self-schedule or self-modify
- **No network** — all Tool implementations in Phase 11-A are Mock/Fake

---

## 10. Tool / Provider Boundary

### Tool boundary

| Property | Value |
|---|---|
| **Depends on** | `ToolCapability` (K.1), `Evidence` (K.2), `InvestigationTask` (K.1) |
| **May declare** | Which `ToolCapability` values it supports |
| **May return** | `ToolResult` containing zero or more `Evidence` records |
| **May NOT do** | Real network access, shell execution, AI calls, database writes |
| **Phase 11-A scope** | Mock/Fake implementations only |

### Provider boundary (`ToolProvider` Protocol, K.1)

| Property | Value |
|---|---|
| **Depends on** | `InvestigationTask` (K.1) |
| **May do** | Suggest one `InvestigationTask` given context |
| **May NOT do** | Execute tasks, construct Tools, modify state, expand permissions |
| **Context immutability** | The provider MUST NOT mutate `context`. `Mapping[str, str]` is read-only in the type system only; the Planner (K.5) is responsible for passing an immutable copy. Any mutation is undefined behavior and a contract violation. |
| **Caller responsibility** | The Planner validates all 5 rules before executing a suggested task |

### Separation from Phase 10 AI

| Aspect | Phase 10 AI (`AIProvider`) | Phase 11-A AI (`ToolProvider`) |
|---|---|---|
| **Purpose** | Refine event importance | Suggest investigation task |
| **Protocol** | `ai_contract.AIProvider` | `investigation_contract.ToolProvider` |
| **Output** | `ProviderResponse` (JSON) | `InvestigationTask` (enum) |
| **Validation** | AIJudge validates schema | Planner validates 5 rules |
| **May reuse?** | No — separate concerns |

---

## 11. `InvestigationPolicy` Responsibilities

`InvestigationPolicy` is a **budget contract**, not an execution contract.

| Field | Default | Meaning | Enforcement point |
|---|---|---|---|
| `max_steps` | 5 | Maximum tool invocations per investigation | Engine loop |
| `max_pages` | 10 | Maximum total pages fetched per investigation | Engine loop (requires K.4 `ToolResult.pages_fetched`) |
| `timeout_seconds` | 60.0 | Total wall-clock time limit | Engine loop (two-sided check, see §12) |

### Budget enforcement requirements by phase

| Policy field | Contract defined in | Enforcement requires |
|---|---|---|
| `max_steps` | K.1 | K.6 Engine — counts `Planner.plan()` calls |
| `max_pages` | K.1 | K.4 `ToolResult.pages_fetched` + K.6 Engine aggregation |
| `timeout_seconds` | K.1 | K.6 Engine — wall-clock timer with pre/post checks |

`max_pages` enforcement depends on `ToolResult.pages_fetched`, a field that does not exist until K.4 (candidate, not yet implemented). K.1 validates `max_pages` at construction but cannot enforce it at runtime. This is an accepted architectural gap documented in finding F4.

### Non-responsibilities of `InvestigationPolicy`

- It does not choose which tasks to run
- It does not select Tools
- It does not collect Evidence
- It does not produce Results
- It does not authorize investigation (that is `FinalDecision`'s authority)

`InvestigationPolicy` is a **bound**, not a **behavior**.

---

## 12. Budget Enforcement

Budget limits are enforced **two-sided** (finding F3): a pre-step check guards against starting work with no remaining budget; a post-step check catches cases where a single tool call exceeds the remaining budget.

### Check order per iteration

| # | Check | When | Guards against | Breach result |
|---|---|---|---|---|
| 1 | Pre-step elapsed time | Before `Tool.execute()` | Tool execution exceeds remaining `timeout_seconds` | `TIMEOUT` |
| 2 | Pre-step step count | Before `Tool.execute()` | `steps_used >= max_steps` | `BUDGET_EXCEEDED` |
| 3 | Pre-step page count | Before `Tool.execute()` | `pages_checked >= max_pages` | `BUDGET_EXCEEDED` |
| 4 | Post-step elapsed time | After `Tool.execute()` | Tool execution exceeded `remaining_budget` | `TIMEOUT` |
| 5 | Post-step page count | After `Tool.execute()` | `pages_checked >= max_pages` (from `ToolResult.pages_fetched`) | `BUDGET_EXCEEDED` |

### `max_pages` enforcement (finding F4)

`max_pages` is validated at `InvestigationPolicy` construction (K.1) but enforced at runtime by the Engine (K.6) using `ToolResult.pages_fetched` (K.4, candidate).

The counting model:
- Each `ToolResult` reports `pages_fetched: int` (K.4 implementation requirement)
- The Engine maintains a running `pages_checked` counter
- After each step, `pages_checked += tool_result.pages_fetched`
- If `pages_checked >= max_pages`, return `BUDGET_EXCEEDED`

This creates a **documented dependency chain**:
`InvestigationPolicy.max_pages` (K.1) → `ToolResult.pages_fetched` (K.4) → Engine aggregation (K.6)

If K.4 does not deliver `ToolResult.pages_fetched`, `max_pages` cannot be enforced. This dependency is not yet approved (K.4 is candidate).

### Precedence

If multiple limits are breached simultaneously, the first detected check wins:

1. `TIMEOUT` (post-step elapsed — most severe)
2. `BUDGET_EXCEEDED` (pre-step or post-step)
3. Tool-level errors → `FAILED`

### Termination behavior

- The Engine stops executing further steps immediately after a breach is detected
- An `InvestigationResult` with the appropriate status (`TIMEOUT` or `BUDGET_EXCEEDED`) is returned
- `failure_reason` describes which budget was exceeded and the values involved
- `steps_used` and `pages_checked` reflect the count at the point of termination
- **`FinalDecision` is not modified**

### Hard upper bound

`max_steps` is the **absolute maximum number of Planner evaluations**. The Engine MUST NOT call `Planner.plan()` more than `max_steps` times, regardless of the Planner's responses. This is the final safeguard against infinite loops.

---

## 13. Evidence Traceability

`Evidence` provides traceability through these fields:

| Field | Traceability purpose |
|---|---|
| `source` | Where the evidence came from (domain identifier) |
| `url` | Where the evidence was retrieved from |
| `retrieved_at` | When the evidence was retrieved (immutable, caller-provided) |
| `claim` | What assertion the evidence supports or refutes |
| `supporting_text` | The actual evidence content |
| `evidence_type` | How the evidence was classified (PRIMARY/SECONDARY/HISTORICAL/DERIVED) |

### Immutability guarantees

- `Evidence` is a `frozen=True` dataclass — no field can be changed after construction
- `evidence_type` is an enum — no arbitrary strings can be injected
- `retrieved_at` is provided by the caller — no auto-mutation, no timezone conversion
- `Evidence` equality and hashing work by default (all fields are hashable)

### What Evidence does NOT provide

- No cryptographic hash of the evidence content
- No source verification
- No freshness guarantees (beyond `retrieved_at`)
- No link to a specific `InvestigationTask` (that linkage would be established at the Engine level, not in the Evidence data model)
- No unique identifier — two `Evidence` records with identical field values are indistinguishable (accepted K.2 limitation, finding F2)

### Evidence reference model (finding F2)

`InvestigationFinding.evidence_refs` uses **positional indices** into `InvestigationResult.evidence`:

```python
result.evidence = (ev0, ev1, ev2)        # indices 0, 1, 2
finding.evidence_refs = ("0", "2")       # references ev0 and ev2
```

This model is the **canonical reference model** for K.3. It requires:
- No modification to K.2 `Evidence` (stays frozen and immutable)
- The `evidence_refs` tuple to be a tuple of `str` representations of zero-based indices
- A validation rule: every ref in `evidence_refs` must be a valid index into `InvestigationResult.evidence`

Cross-result evidence referencing (referencing evidence from one investigation in another) is NOT supported. That requires `evidence_id`, a future concern outside Phase 11-A.

---

## 14. Preventing Investigation from Becoming Autonomous

Phase 11-A is explicitly designed to be **non-autonomous**. The following constraints ensure this:

| Constraint | Mechanism | Enforced in |
|---|---|---|
| **No self-scheduling** | `InvestigationEngine.run()` runs exactly once; no background loop | Engine |
| **No self-modification** | All dataclasses are frozen; no config files are written | Contract |
| **No permission expansion** | `InvestigationTask` is a closed enum; AI cannot create new tasks | Contract + Planner |
| **No Tool creation** | Tools are injected at construction; the Engine cannot create new Tools at runtime | Engine |
| **No network access** | All Tool implementations are Mock/Fake; no `requests`, `urllib`, or `socket` imports | All K.3+ modules |
| **No database writes** | Evidence and Result are in-memory dataclasses; no persistence in Phase 11-A | All K.3+ modules |
| **No FinalDecision modification** | Investigation never touches Phase 10 state | Engine invariant |
| **Budget limits** | max_steps, max_pages, timeout_seconds — investigation is time-bounded | Engine |
| **Task validation** | All 5 Planner validation rules must pass before any execution | Planner |
| **Context immutability** | `ToolProvider.suggest_task(context)` must not mutate `context`. Planner must defensively copy context before passing it. Mutation is undefined behavior and a contract violation. | Planner (K.5) |

---

## 15. Explicitly Out of Scope

### Modules that must NOT be created in Phase 11-A

| Module | Reason |
|---|---|
| `telegram_*.py` | Telegram integration is a separate phase |
| `web_fetch_real.py` or any real HTTP client | No real network access approved |
| `browser_*.py` | Browser automation is a separate phase |
| `investigation_storage.py` or any DB layer | No database approved for investigation |
| `scheduler.py` or any autonomous loop | No self-scheduling approved |

### Files that must NOT be modified

| File | Reason |
|---|---|
| `src/web_watcher/policy.py` | Phase 10 FROZEN |
| `src/web_watcher/decide.py` | Phase 10 FROZEN |
| `src/web_watcher/final_decision.py` | Phase 10 FROZEN |
| `src/web_watcher/ai_contract.py` | Phase 10 FROZEN |
| `src/web_watcher/ai_errors.py` | Phase 10 FROZEN |
| `src/web_watcher/ai_config.py` | Phase 10 FROZEN |
| `src/web_watcher/ai_provider.py` | Phase 10 FROZEN |
| `src/web_watcher/llm_provider.py` | Phase 10 FROZEN |
| `src/web_watcher/investigation_contract.py` | K.1 — complete, frozen |
| `src/web_watcher/investigation_evidence.py` | K.2 — complete, frozen |
| `tests/test_investigation_contract.py` | K.1 — complete, frozen |
| `tests/test_investigation_evidence.py` | K.2 — complete, frozen |

### External capabilities NOT approved for Phase 11-A

| Capability | Status |
|---|---|
| Network access (HTTP, HTTPS) | ❌ Not approved |
| Real browser automation (Playwright, Selenium) | ❌ Not approved |
| Shell execution (`subprocess`, `eval`, `exec`) | ❌ Not approved |
| Database operations | ❌ Not approved |
| Telegram Bot API | ❌ Not approved |
| Real LLM API calls (SenseNova or any provider) | ❌ Not approved |
| External API access | ❌ Not approved |
| Autonomous loops / self-scheduling | ❌ Not approved |
| Tool Capability additions beyond the 4 defined in K.1 | ❌ Not approved |

### Explicitly allowed in Phase 11-A

| Capability | Status |
|---|---|
| Python stdlib only | ✅ Allowed |
| `dataclasses`, `enum`, `typing` | ✅ Allowed |
| Frozen dataclasses | ✅ Required pattern |
| Mock/Fake test implementations | ✅ Required pattern |
| In-memory data only | ✅ Required constraint |
| Unit tests (deterministic, no randomness) | ✅ Allowed |

---

## 16. Module Dependency Map

### Allowed dependencies

```
investigation_contract.py        (K.1)
  ↑                              ↑
investigation_evidence.py        (K.2 — depends on nothing external)
  ↑
investigation_result.py          (K.3 — CANDIDATE; depends on Evidence)
  ↑
investigation_tools.py           (K.4 — CANDIDATE; depends on ToolCapability, Evidence, Task)
  ↑
investigation_planner.py         (K.5 — CANDIDATE; depends on ToolProvider, Tool, Policy)
  ↑
investigation.py                 (K.6 — CANDIDATE; depends on all K.1–K.5 + FinalDecision + Event)
```

### Forbidden dependencies

| Module | Forbidden import |
|---|---|
| Any Phase 11-A module | `requests`, `httpx`, `urllib`, `socket`, `playwright`, `selenium` |
| Any Phase 11-A module | `sqlalchemy`, `sqlite3`, `pymongo`, `psycopg2` |
| Any Phase 11-A module | `telegram`, `pytelegrambotapi` |
| Any Phase 11-A module | `subprocess`, `os.system`, `eval`, `exec` |
| Any Phase 11-A module | `ai_contract.py` (Phase 10 AI contract) |
| Any Phase 11-A module | `decide.py` (Phase 10 decision path) |
| Any Phase 11-A module | `llm_provider.py` (Phase 10 LLM provider) |

---

## 17. Architecture Freeze Acceptance Criteria

Before K.3 implementation may begin, ALL of the following must be true:

| # | Criterion | Status |
|---|---|---|
| AC-1 | K.1 (`investigation_contract.py`) responsibility is clear | ✅ Confirmed |
| AC-2 | K.2 (`investigation_evidence.py`) responsibility is clear | ✅ Confirmed |
| AC-3 | K.3 boundary is defined as Candidate Design | ✅ Defined in §7 |
| AC-4 | K.4, K.5, K.6 are labelled Candidate Design, not approved | ✅ Labelled in §8 |
| AC-5 | Module dependency map is explicit (allowed and forbidden) | ✅ Defined in §17 |
| AC-6 | Phase 10 protection scope is explicit (8 files) | ✅ Defined in §15 |
| AC-7 | Complete data flow is documented (current + proposed) | ✅ Defined in §9 |
| AC-8 | Tool / Provider boundary is explicit | ✅ Defined in §10 |
| AC-9 | Evidence / Result / Decision boundaries are explicit | ✅ Defined in §10, §13, §17 |
| AC-10 | Out-of-Scope is explicit | ✅ Defined in §15 |
| AC-11 | Each phase has Acceptance Criteria | ⬜ AC for K.3 defined; AC for K.4–K.6 to be defined in their own freeze documents |
| AC-12 | No key interface requires guessing | ✅ All K.3 interfaces derived from K.1/K.2 + Phase 10C-A.2 contract |
| AC-13 | Budget enforcement model is defined (two-sided timeout check, max_pages K.4 dependency documented) | ✅ Revised in §12 |
| AC-14 | Non-autonomy constraints are explicit (including context immutability) | ✅ Revised in §14 |
| AC-15 | Human review approval is obtained | ⬜ **PENDING HUMAN FINAL APPROVAL** |

### Summary of AC status

- **AC-1 through AC-12: SATISFIED**
- **AC-11: PARTIALLY SATISFIED** (K.3 AC defined; K.4–K.6 AC deferred to future freeze documents)
- **AC-13: SATISFIED** (revised with two-sided timeout, `max_pages` K.4 dependency)
- **AC-14: SATISFIED** (revised with context immutability constraint)
- **AC-15: PENDING HUMAN FINAL APPROVAL** (this document has been revised per Architecture Review findings; awaiting sign-off)

---

## 18. Architecture Freeze Decision

**Architecture Freeze status: REVISION COMPLETE — PENDING HUMAN FINAL APPROVAL**

This document has been revised to address all 5 Architecture Review findings (F1–F5).
The revision is a document-only change. No Python files, tests, or K.3+ modules were created or modified.

### What is frozen (unaffected by revision)

1. K.1 (Contract) — DONE, tested, protected (zero diffs)
2. K.2 (Evidence) — DONE, tested, protected (zero diffs)
3. Phase 10 authority boundary — FROZEN (8 files, zero diffs)
4. Module dependency direction — established
5. Out-of-Scope list — established
6. Non-autonomy constraints — established

### What this revision changed in the document

| Finding | Severity | Sections revised |
|---|---|---|
| F1: `Mapping[str,str]` runtime mutability | MEDIUM | §4 (K.1 design decision 6), §10 (Provider boundary table), §14 (context immutability constraint) |
| F2: Evidence lacks unique identifier | LOW-MEDIUM | §5 (K.2 design decision 5), §7 (evidence_refs positional model), §13 (evidence_refs model) |
| F3: Timeout check-before-only | MEDIUM | §7 (state transition table + budget check order), §12 (two-sided budget enforcement) |
| F4: `max_pages` not grounded | MEDIUM | §11 (budget enforcement requirements table), §12 (`max_pages` enforcement section) |
| F5: Undefined SUCCESS termination | MEDIUM | §7 (state transition table + hard loop guard), §12 (precedence + termination behavior) |

### What is NOT frozen (candidate, pending approval)

1. K.3 (Result) — interface proposed, not implemented
2. K.4 (Tools) — interface proposed, not implemented
3. K.5 (Planner) — interface proposed, not implemented
4. K.6 (Engine) — interface proposed, not implemented

### Recommendation

**This Architecture Freeze document is ready for human final approval.**

K.3 implementation should not begin until:

1. A human reviewer reads and approves this revised document
2. K.3's own architecture freeze document is completed (with the state transition table from §7)
3. The `max_pages` / `ToolResult.pages_fetched` dependency (finding F4) is explicitly acknowledged as a K.4 prerequisite

---

*End of Architecture Freeze document (revised 2026-08-17).*
*Revision addresses Architecture Review findings F1–F5.*
*No files outside this document were created, modified, staged, or committed during revision.*