# PHASE 11-A — K.5 Investigation Planner Architecture

Status: ARCHITECTURE FREEZE
Phase: K.5 Planner
Date: 2026-08-17

## 1. Purpose

K.5 is the Investigation Planner layer.

K.5 converts an InvestigationTask into a deterministic InvestigationPlan
containing exactly one Tool action for the current Phase 11-A architecture.
Multi-step planning is deferred to a future architecture revision.

K.5 does not execute tools and does not produce InvestigationResult.

## 2. Architectural Position

K.1 Investigation Contract
        ↓
K.2 Investigation Evidence
        ↓
K.3 Investigation Result
        ↓
K.4 Investigation Tool Layer
        ↓
K.5 Investigation Planner
        ↓
K.6 Investigation Engine

## 3. Responsibilities

K.5 is responsible for:

- determining the InvestigationTask when required
- optionally consuming ToolProvider task suggestions
- validating provider-supplied tasks
- mapping InvestigationTask to allowed ToolCapability values
- matching tasks against available Tools
- detecting zero-candidate and ambiguous tool matches
- constructing an immutable InvestigationPlan
- validating the static max_steps policy constraint

K.5 is NOT responsible for:

- network access
- LLM execution
- Tool execution
- Tool chaining
- runtime budget accounting
- runtime pages budget enforcement
- Engine state
- evidence aggregation
- InvestigationResult construction
- InvestigationFinding construction
- dynamic runtime replanning

## 4. ToolProvider Contract

ToolProvider is optional.

When an InvestigationTask is explicitly supplied, the Planner uses it
directly.

When no InvestigationTask is supplied, a ToolProvider is required to
suggest one through:

    suggest_task(context: Mapping[str, str]) -> InvestigationTask

The Planner must not invent a task by interpreting arbitrary context keys.

The Planner must defensively copy the context before passing it to a
ToolProvider.

A Provider-supplied invalid task causes PlannerError.

A valid task with no compatible Tool also causes PlannerError.

The ToolProvider is a task suggestion mechanism only. It does not select
Tools and does not construct plans.

## 5. Determinism

The Planner core must be deterministic.

Tool collection ordering must never determine the selected Tool.

For a given task:

- zero compatible Tools -> planning failure
- exactly one compatible Tool -> valid PlanStep
- more than one compatible Tool -> deterministic ambiguity failure

The Planner must not use:

- random selection
- object identity
- insertion order
- arbitrary first-match selection
- network state
- LLM output as an implicit execution decision

An external ToolProvider may be non-deterministic; the Planner remains
responsible for validating its output and producing deterministic behavior
for all subsequent planning operations.

## 6. Task → Capability Mapping

The Task → ToolCapability mapping is owned by K.5.

K.1 defines the Task and Capability contracts but does not define Planner
strategy.

K.4 defines Tool capabilities but does not define investigation strategy.

The mapping is immutable and conceptually:

    VERIFY_SOURCE
        → WEB_FETCH

    FETCH_RELATED_SOURCE
        → WEB_SEARCH

    COMPARE_WITH_HISTORY
        → HISTORICAL_LOOKUP

    EXTRACT_EVIDENCE
        → PAGE_PARSE

    CROSS_CHECK
        → WEB_SEARCH

A task is compatible with a Tool when the Tool declares at least one of
the allowed capabilities for that task.

K.5 must use the public Tool.capabilities() contract.

K.5 must not depend on MockTool implementation details such as
SUPPORTED_TASKS.

## 7. PlanStep

PlanStep is an immutable planning action.

Conceptual fields:

    task
    tool

The PlanStep stores a Tool reference.

K.5 does not introduce:

- tool IDs
- tool names
- tool registries
- Tool lifecycle management

K.6 consumes the Tool reference when executing the plan.

## 8. InvestigationPlan

InvestigationPlan is immutable.

Conceptual field:

    steps: tuple[PlanStep, ...]

For the current Phase 11-A architecture, `steps` must contain exactly one
PlanStep. Multi-step plans are not part of the current implementation
contract and require a future architecture revision.

The Plan contains no metadata unless a future architecture revision
explicitly requires it.

The Plan does not contain:

- plan IDs
- timestamps
- planner versions
- confidence
- strategy metadata
- runtime state
- execution results

## 9. Policy Boundary

K.5 performs static plan validation.

The number of planned steps must not exceed:

    InvestigationPolicy.max_steps

Because the current K.5 plan contains exactly one step and K.1 guarantees
max_steps >= 1, this check is currently a defensive invariant rather than
a source of multi-step planning behavior.

K.6 independently performs runtime enforcement.

Therefore:

    K.5:
        planned steps <= max_steps

    K.6:
        actual executed steps <= max_steps

K.5 does not enforce runtime pages consumption.

K.4 provides pages_fetched for each ToolResult.

K.6 aggregates pages_fetched and enforces max_pages.

## 10. Failure Model

K.5 uses a single Planner-specific exception:

    PlannerError

PlannerError covers planning failures including:

- invalid provider task
- unsupported task
- no compatible Tool
- ambiguous Tool selection
- invalid plan
- max_steps violation

K.5 does not return InvestigationResult for planning failures.

InvestigationResult remains a K.6 responsibility.

## 11. Dependency Boundary

K.5 may depend on:

- K.1 investigation contracts
- K.4 Tool / ToolResult contracts as required by planning

K.5 must not depend on:

- K.2 Evidence implementation
- K.3 InvestigationResult implementation
- K.6 Engine
- Phase 10 AI decision modules

K.5 must not modify K.1, K.2, K.3, or K.4.

## 12. State Model

K.5 is stateless.

The Planner must not retain mutable investigation state between planning
operations.

It must not retain:

- previous Plans
- previous ToolResults
- evidence
- page counters
- execution counters
- Engine state

## 13. Execution Boundary

K.5 produces a plan only.

It must never call:

    Tool.execute(...)

Execution belongs exclusively to K.6.

Therefore:

    K.5 → Plan
    K.6 → Tool.execute()
    K.4 → ToolResult

## 14. Replanning

K.5 Phase 11-A is a one-shot planner.

Runtime replanning is explicitly out of scope.

If future architecture requires:

    K.6 → K.5 → new Plan

that must be introduced through a future architecture revision.

K.5 must not implement an execution loop or hidden replanning mechanism.

## 15. Public API Scope

The minimum public conceptual API is:

    PlanStep
    InvestigationPlan
    Planner
    PlannerError

No additional public abstractions are required at Architecture Freeze.

The following are explicitly not part of the K.5 public API:

- PlanningContext
- PlannerState
- PlannerResult
- ToolRegistry
- ToolId
- Strategy objects
- public TaskMatcher
- public PlanValidator
- PlanMetadata

Internal helpers may be introduced during implementation only when they
do not expand the public architecture or violate frozen boundaries.

## 16. Minimum Planner Contract

The minimum public Planner contract is:

    class Planner:
        def __init__(
            self,
            tools: Sequence[Tool],
            policy: InvestigationPolicy,
            tool_provider: ToolProvider | None = None,
        ) -> None:
            ...

        def plan(
            self,
            task: InvestigationTask | None = None,
            context: Mapping[str, str] = ...,
        ) -> InvestigationPlan:
            ...

The Planner owns the configured Tool collection, policy, and optional
ToolProvider.

If `task` is provided, the Planner uses that task directly.

If `task` is omitted, the Planner requires a configured ToolProvider and
obtains the task through `suggest_task(context)`.

If both `task` and the ToolProvider are absent, PlannerError is raised.

The context passed to ToolProvider must be defensively copied before the
provider call.

The public contract does not require a PlanningContext abstraction.

## 17. ToolProvider Optionality

The Planner accepts:

    tool_provider: ToolProvider | None

ToolProvider is optional at Planner construction time.

The following cases are therefore explicit:

    task is provided
        → ToolProvider is not required

    task is absent and ToolProvider exists
        → ToolProvider.suggest_task(context)

    task is absent and ToolProvider is absent
        → PlannerError

The Planner never infers an InvestigationTask from arbitrary context keys.

## 18. Implementation Constraints


Implementation must:

- preserve K.1/K.2/K.3/K.4 behavior
- avoid modifying protected contracts
- remain deterministic
- remain stateless
- use immutable Plan structures
- avoid network access
- avoid LLM calls
- avoid persistence
- avoid Tool execution
- avoid runtime budget enforcement
- avoid K.6 implementation

## 19. Architecture Freeze

This document represents the approved K.5 Architecture Freeze.

Implementation may begin only after explicit Human Approval of this
Architecture Freeze.

Implementation must then proceed with:

    Implementation
        ↓
    Tests
        ↓
    Scope Audit
        ↓
    Final Review
        ↓
    Commit
        ↓
    Push

K.6 remains out of scope until K.5 is fully completed and reviewed.