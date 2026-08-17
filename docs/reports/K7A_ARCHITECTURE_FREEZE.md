# K.7-A Architecture Freeze

## Status

**FROZEN**

Phase 11-B / K.7-A

Date: 2026-08-18

---

## 1. Purpose

K.7-A freezes the Signal / Event / Importance vocabulary and its
architectural boundaries before implementation.

This freeze defines semantic contracts only.

It does not implement runtime pipeline integration.

---

## 2. Frozen Processing Model

Raw GitHub Changes

↓

SignalType

↓

EventType

↓

Importance

↓

Action

↓

FinalDecision

↓

InvestigationTask

↓

Planner

↓

Engine

↓

InvestigationResult

---

## 3. Frozen Signal Vocabulary

Signal vocabulary is represented as an independent domain layer.

Initial vocabulary includes the signals identified during K.7-A discovery.

The following concepts are recognized:

- content_change
- stars_changed
- release_published

Additional signal types identified during discovery may be introduced
only through an explicit architecture change.

### Scope Rule

K.7-A freezes vocabulary semantics.

It does not require every defined signal to have a production generator.

---

## 4. Frozen Event Vocabulary

Events are derived semantic representations of Signals.

Event vocabulary is an independent domain layer and must not be treated
as a raw transport copy of GitHub changes.

Event types must represent stable domain meaning rather than source-specific
implementation details.

---

## 5. Frozen Importance Vocabulary

Policy-facing importance is a separate semantic vocabulary.

The initial importance levels are:

- critical
- important
- interesting
- ignore

Importance is not equivalent to SignalType or EventType.

A Signal must not be directly substituted for Importance.

---

## 6. Frozen Action Boundary

Action is derived from policy evaluation.

The architecture must preserve the distinction between:

Signal
→ Event
→ Importance
→ Policy
→ Action

In particular, `content_change` must not be permanently or implicitly
mapped directly to `critical`.

No temporary Signal → Importance shortcut is frozen.

---

## 7. stars_changed Thresholds

Initial `stars_changed` thresholds are frozen as:

- 100 stars: significant change threshold
- 500 stars: major change threshold

These thresholds are initially hard-coded.

Configuration of these thresholds is explicitly deferred to a later
architecture/configuration decision.

---

## 8. release_published Scope

`release_published` is part of the frozen vocabulary.

K.7-A does not implement production generation of this signal.

Source adapter / FetchService changes required to generate the signal
are outside the K.7-A implementation boundary.

---

## 9. Enum / Module Boundary

Vocabulary definitions are kept separate from the central data models.

Planned domain modules:

- `signal_types.py`
- `event_types.py`
- `event_status.py`

The exact import integration is part of K.7-A implementation.

The architecture does not require vocabulary definitions to be embedded
directly inside `models.py`.

---

## 10. Event.importance Contract

The `Event.importance` model field must converge to the frozen
Importance vocabulary during K.7-A implementation.

The current implementation is not considered the final vocabulary
contract until that migration is complete.

This is an implementation consequence of the architecture freeze.

---

## 11. Explicit Non-Goals

K.7-A does NOT implement:

- FetchService changes
- GitHub source adapter changes
- Signal generation pipeline
- Event correlation runtime integration
- Policy runtime integration
- FinalDecision consumer
- InvestigationTask derivation
- Planner integration
- Engine integration
- InvestigationResult runtime flow
- K.8 pipeline integration

---

## 12. Architectural Invariants

The following invariants are frozen:

1. Signal and Event are distinct concepts.
2. SignalType is not EventType.
3. Importance is not SignalType.
4. Importance is not EventType.
5. Action is derived through policy evaluation.
6. `content_change` is not inherently `critical`.
7. `release_published` may be defined without being produced in K.7-A.
8. `stars_changed` thresholds start as 100 / 500.
9. Vocabulary definitions remain separated from central models.
10. K.7-A does not connect the Investigation runtime pipeline.
11. K.8 is not part of this freeze.
12. Any semantic change to this vocabulary after freeze requires an
    explicit architecture review.

---

## 13. Approved Human Decisions

The following four decisions were explicitly approved:

### Decision 1 — Vocabulary module boundary

**APPROVED**

Use independent vocabulary modules rather than embedding the enums
directly into `models.py`.

### Decision 2 — stars_changed thresholds

**APPROVED**

Use 100 / 500 as the initial hard-coded thresholds.

### Decision 3 — release_published

**APPROVED**

Define the vocabulary but do not implement production signal generation
during K.7-A.

### Decision 4 — Event.importance

**APPROVED**

Converge `Event.importance` to the frozen Importance vocabulary during
K.7-A implementation.

---

## 14. Phase Boundary

K.7-A Freeze ends at vocabulary and architectural contract definition.

Next phase:

**K.7-B Implementation**

K.7-B must implement the frozen contract without silently redefining
its semantics.

K.8 remains a separate pipeline integration phase.

---

## 15. Freeze Approval

Human review completed.

The four unresolved architecture decisions were reviewed and approved.

**K.7-A Architecture Freeze: APPROVED**

---

## 16. Git Safety

This freeze artifact does not authorize modification of protected
Phase 10 / K.1-K.6 implementation code.

Implementation changes require a separate K.7-B scope.
