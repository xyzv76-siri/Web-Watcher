# PHASE 11-A — K.4 Investigation Tool Layer
# Architecture Freeze — Draft

Status: DRAFT — NOT APPROVED FOR IMPLEMENTATION
Date: 2026-08-17

---

## 1. Purpose

K.4 defines the passive Tool Layer for the Investigation Architecture.

K.4 provides deterministic tool contracts and mock tool implementations
that can later be orchestrated by K.5 Planner and executed/aggregated by
K.6 Engine.

K.4 MUST NOT contain investigation strategy, planning, budget enforcement,
LLM decision-making, network access, persistence, or engine state.

Architecture:

K.1 Investigation Contract
        ↓
K.2 Investigation Evidence
        ↓
K.3 Investigation Result
        ↓
K.4 Tool Layer
        ↓
K.5 Planner
        ↓
K.6 Engine

---

## 2. Scope

K.4 consists of:

1. ToolResult
2. ToolCapability
3. Tool Protocol
4. MockWebFetchTool
5. MockWebSearchTool
6. MockPageParseTool
7. MockHistoricalLookupTool

Target implementation files:

- src/web_watcher/investigation_tools.py
- tests/test_investigation_tools.py

No other project files are part of the K.4 implementation scope.

---

## 3. Dependency Boundary

K.4 may depend only on:

- Python standard library
- K.1 investigation contract
- K.2 investigation evidence

K.4 MUST NOT import:

- ai_contract.py
- decide.py
- final_decision.py
- llm_provider.py

K.4 MUST NOT import or use:

- requests
- httpx
- urllib
- urllib3
- socket
- subprocess

K.4 MUST NOT use:

- os.system
- eval
- exec

K.4 MUST NOT access external services.

---

## 4. ToolResult

K.4 defines ToolResult as the result of one Tool execution.

ToolResult MUST be implemented as an immutable `@dataclass(frozen=True)`.

Required fields:

- success
- evidence
- pages_fetched
- message

ToolResult MUST validate its invariants during construction via
`__post_init__`.

Validation requirements:

- `success` MUST be a bool.
- `evidence` MUST use an immutable collection representation.
- `pages_fetched` MUST be an int.
- `pages_fetched` MUST be >= 0.
- `message` MUST be a str.

Invalid values MUST cause deterministic construction failure.

ToolResult MUST NOT retain mutable internal state.

### 4.1 success

Type:

bool

Meaning:

- True: the Tool execution completed successfully.
- False: the Tool execution explicitly failed.

### 4.2 evidence

Contains zero or more K.2 InvestigationEvidence objects.

K.4 MUST reuse the K.2 evidence model.

K.4 MUST NOT introduce a second evidence model.

K.4 MUST NOT introduce evidence_id.

K.3 positional evidence references remain unchanged.

### 4.3 pages_fetched

Type:

int

Meaning:

The number of pages fetched by this single Tool execution.

Rules:

- MUST be an integer.
- MUST be non-negative.
- Represents this Tool execution only.
- MUST NOT represent global investigation progress.
- MUST NOT enforce the investigation-wide max_pages budget.

The value is later aggregated by K.6 Engine.

Budget flow:

InvestigationPolicy.max_pages
        ↓
ToolResult.pages_fetched
        ↓
K.6 aggregation
        ↓
InvestigationResult.pages_checked
        ↓
max_pages enforcement

K.4 therefore reports execution facts but does not own investigation-wide
budget enforcement.

### 4.4 message

Type:

str

Meaning:

A deterministic human-readable description of the Tool execution result.

Messages MUST NOT contain timestamps, random identifiers, environment-dependent
values, or other nondeterministic information.

---

## 5. ToolCapability

`ToolCapability` is defined exclusively by K.1.

K.4 MUST import and reuse the K.1 `ToolCapability` type.

K.4 MUST NOT define, duplicate, subclass, or recreate `ToolCapability`.

Required capabilities supplied by K.1:

- WEB_FETCH
- WEB_SEARCH
- PAGE_PARSE
- HISTORICAL_LOOKUP

The K.1 definition is the single authoritative capability vocabulary for
K.4, K.5, and K.6.

Capabilities describe what a Tool can perform.

Capabilities MUST be deterministic.

A Tool MUST return the same capability set for every invocation.

Capabilities MUST NOT depend on:

- time
- environment
- network state
- mutable execution state
- random values

---

## 6. Tool Protocol

The Tool abstraction is:

class Tool(Protocol):
    def capabilities(self) -> frozenset[ToolCapability]:
        ...

    def execute(
        self,
        task: InvestigationTask,
        context: Mapping[str, str],
    ) -> ToolResult:
        ...

The protocol is intentionally passive.

### 6.1 capabilities()

Requirements:

- deterministic
- side-effect free
- returns frozenset[ToolCapability]
- does not mutate internal state
- does not inspect external state

### 6.2 execute()

Requirements:

- accepts an InvestigationTask
- accepts read-only Mapping[str, str] context
- returns ToolResult
- does not mutate context
- does not retain mutable context
- does not create another Tool
- does not call another Tool
- does not call an LLM
- does not access the network
- does not persist data
- does not modify investigation policy
- does not modify Engine state

Unsupported tasks MUST return an explicit failed ToolResult.

Unsupported tasks MUST NOT silently succeed.

The failure response MUST have the exact shape:

    ToolResult(
        success=False,
        evidence=(),
        pages_fetched=0,
        message="unsupported task: <task_value>",
    )

The `message` MUST include the unsupported `InvestigationTask` value
to aid debugging.

---

## 7. Context Semantics

The context parameter is input-only.

Tools MUST treat context as read-only.

Tools MUST NOT:

- mutate the supplied Mapping
- store the supplied Mapping for later use
- rely on object identity
- add hidden state to context
- use context as a cross-tool communication channel

The Tool Layer does not own investigation state.

---

## 8. InvestigationTask Mapping

K.4 consumes the InvestigationTask contract defined by K.1.

Supported task-to-capability mapping:

### VERIFY_SOURCE

Primary capability:

- WEB_FETCH

### FETCH_RELATED_SOURCE

Supported capabilities:

- WEB_SEARCH
- WEB_FETCH

### COMPARE_WITH_HISTORY

Primary capability:

- HISTORICAL_LOOKUP

### EXTRACT_EVIDENCE

Primary capability:

- PAGE_PARSE

### CROSS_CHECK

Supported capabilities:

- WEB_SEARCH
- WEB_FETCH

A Tool MUST explicitly reject tasks it does not support.

K.4 does not decide which Tool should be selected.

Tool selection belongs to K.5 Planner.

---

## 9. MockWebFetchTool

Construction:

- `MockWebFetchTool`() MUST take no constructor arguments.
- The Tool MUST have no external configuration.
- The Tool MUST NOT read environment variables or external state.

Capability:

- WEB_FETCH

Purpose:

Provide deterministic fake source-fetch behavior for tests and future
architecture integration.

Requirements:

- no real HTTP requests
- no DNS
- no socket access
- no external API access
- no filesystem-based network fixtures
- deterministic output
- no time dependency
- no randomness

The Tool returns synthetic K.2 evidence representing a fetched source.

`pages_fetched` MUST be exactly `1` for every successful
`MockWebFetchTool.execute()` call.

This represents one fetched page in the mock model.

It MUST NOT enforce InvestigationPolicy.max_pages.

---

## 10. MockWebSearchTool

Construction:

- `MockWebSearchTool`() MUST take no constructor arguments.
- The Tool MUST have no external configuration.
- The Tool MUST NOT read environment variables or external state.

Capability:

- WEB_SEARCH

Purpose:

Provide deterministic fake search behavior.

Requirements:

- no real search engine access
- no HTTP
- no DNS
- no socket
- no external API
- deterministic output
- no time dependency
- no randomness

The Tool returns synthetic K.2 evidence representing search results.

`pages_fetched` MUST be exactly `0`.

It MUST NOT select another Tool.

It MUST NOT perform recursive investigation.

It MUST NOT enforce global investigation budgets.

---

## 11. MockPageParseTool

Construction:

- `MockPageParseTool`() MUST take no constructor arguments.
- The Tool MUST have no external configuration.
- The Tool MUST NOT read environment variables or external state.

Capability:

- PAGE_PARSE

Purpose:

Provide deterministic fake page parsing behavior.

Requirements:

- no browser
- no network
- no external parser service
- deterministic output
- no randomness
- no time dependency

The Tool returns synthetic K.2 evidence representing extracted page evidence.

`pages_fetched` MUST be exactly `0`.

It does not fetch pages.

It only represents parsing behavior supplied by the mock task/context model.

---

## 12. MockHistoricalLookupTool

Construction:

- `MockHistoricalLookupTool`() MUST take no constructor arguments.
- The Tool MUST have no external configuration.
- The Tool MUST NOT read environment variables or external state.

Capability:

- HISTORICAL_LOOKUP

Purpose:

Provide deterministic fake historical lookup behavior.

Requirements:

- no external database
- no network
- no API
- no filesystem persistence
- deterministic output
- no randomness
- no time dependency

The Tool returns synthetic K.2 evidence representing historical information.

`pages_fetched` MUST be exactly `0`.

It MUST NOT implement a real historical data source.

---

## 13. Determinism

All K.4 Tools MUST be deterministic.

For identical:

- Tool instance configuration
- InvestigationTask
- context contents

the Tool MUST return equivalent ToolResult values.

Output MUST NOT depend on:

- current time
- random number generation
- process ID
- hostname
- environment variables
- network state
- filesystem state
- external services

Determinism is a core architectural invariant.

---

## 14. Immutability and Side Effects

K.4 Tool execution is observationally pure from the perspective of the
investigation architecture.

Tools MUST NOT:

- mutate task objects
- mutate context
- mutate K.1 policy
- mutate K.2 evidence objects
- mutate K.3 result objects
- mutate Engine state
- persist execution state
- create hidden global state

Any returned collection/model MUST NOT expose mutable internal Tool state.

---

## 15. Non-Autonomy Boundary

K.4 is not an agent.

K.4 MUST NOT:

- choose investigation strategy
- decompose investigation goals
- select the next task
- select another Tool
- retry another Tool
- call another Tool
- modify policy
- enforce global budgets
- aggregate investigation-wide counters
- produce the final investigation result
- make AI/LLM decisions

Responsibilities:

K.4:
    execute one passive Tool operation

K.5:
    choose investigation actions and Tool selection

K.6:
    execute the investigation loop and enforce global budgets

---

## 16. Page Accounting Boundary

K.4 owns only per-execution page accounting.

ToolResult.pages_fetched represents pages fetched by one Tool execution.

K.6 owns:

- cumulative pages_checked
- cumulative Tool execution accounting
- InvestigationPolicy.max_pages enforcement
- investigation termination when budgets are exceeded

K.4 MUST NOT maintain a global page counter.

K.4 MUST NOT reject an execution solely because a global page budget would
be exceeded.

---

## 17. Failure Semantics

A Tool failure is represented by:

ToolResult.success = False

The result MUST remain deterministic.

Failure MUST include a deterministic message.

Unsupported task is an explicit Tool failure.

K.4 does not convert Tool failures into InvestigationResult statuses.

Investigation-level status interpretation belongs to K.6.

---

## 18. Test Architecture

K.4 tests MUST verify at minimum:

1. ToolResult construction
2. ToolResult field validation
3. pages_fetched non-negative invariant
4. ToolCapability values
5. deterministic capabilities()
6. Tool protocol compatibility
7. each Mock Tool capability
8. supported task execution
9. unsupported task failure
10. deterministic execution
11. context immutability
12. no Tool chaining
13. no network access
14. no persistence
15. no LLM dependency
16. forbidden dependency absence
17. evidence compatibility with K.2
18. pages_fetched semantics
19. ToolResult determinism
20. baseline regression protection

Tests MUST NOT require external services.

Tests MUST remain deterministic and runnable offline.
