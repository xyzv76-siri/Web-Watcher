"""Phase 10A — AI judgment contract.

Defines the boundary between deterministic policy (Phase 9) and
semantic AI judgment (Phase 10+).  This module is stdlib-only,
network-free, and contains no executable Action logic.

Key invariants
--------------
* ``PolicyDecision`` is the authoritative deterministic assessment.
* ``AIJudgment`` is a semantic refinement, never a replacement for
  ``PolicyDecision``.
* ``Event.importance`` remains the raw domain field — its semantics
  are not changed by this phase.
* ``AIContext`` and ``AIJudgment`` are immutable after construction.
* AI errors propagate as explicit ``AIError`` subtypes; a failure
  never silently becomes a judgment.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .ai_errors import (
    AIError,
    InvalidJSONError,
    InvalidResponseError,
    ProviderError,
    ProviderTimeoutError,
    SchemaValidationError,
    UnsupportedValueError,
)
from .models import Entity, Event, Signal
from .importance import Importance
from .policy import PolicyDecision


# ---------------------------------------------------------------------------
# Validation helpers — used both inside __post_init__ and by AIJudge
# ---------------------------------------------------------------------------


def _validate_relevance(value: Any) -> None:
    if not isinstance(value, float):
        raise SchemaValidationError(
            f"relevance must be float, got {type(value).__name__}"
        )
    if value < 0.0 or value > 1.0:
        raise UnsupportedValueError(
            f"relevance {value!r} outside allowed range [0.0, 1.0]"
        )


def _validate_importance(value: Any) -> None:
    if not isinstance(value, Importance):
        raise SchemaValidationError(
            f"importance must be Importance enum, got {type(value).__name__}"
        )


def _validate_bool(name: str, value: Any) -> None:
    if not isinstance(value, bool):
        raise SchemaValidationError(
            f"{name} must be bool, got {type(value).__name__}"
        )


def _validate_non_empty_string(name: str, value: Any) -> None:
    if not isinstance(value, str):
        raise SchemaValidationError(
            f"{name} must be str, got {type(value).__name__}"
        )
    if not value.strip():
        raise SchemaValidationError(f"{name} must be non-empty")


# ---------------------------------------------------------------------------
# AIContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AIContext:
    """Immutable context passed to the AI judge.

    Combines the existing Event, the deterministic PolicyDecision,
    and optional surrounding domain objects without redefining any
    Phase 2-9 model.
    """

    event: Event
    policy_decision: PolicyDecision
    entity: Entity | None = None
    signals: tuple[Signal, ...] = field(default_factory=tuple)
    evidence: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# AIJudgment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AIJudgment:
    """Immutable semantic judgment returned by the AI judge.

    Invariants enforced in __post_init__:
        * relevance is a float in [0.0, 1.0]
        * importance is a valid Importance enum member
        * worth_notifying and investigate are bools
        * reason and summary are non-empty strings
    """

    relevance: float
    importance: Importance
    worth_notifying: bool
    investigate: bool
    reason: str
    summary: str

    def __post_init__(self) -> None:
        _validate_relevance(self.relevance)
        _validate_importance(self.importance)
        _validate_bool("worth_notifying", self.worth_notifying)
        _validate_bool("investigate", self.investigate)
        _validate_non_empty_string("reason", self.reason)
        _validate_non_empty_string("summary", self.summary)


# ---------------------------------------------------------------------------
# ProviderResponse
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderResponse:
    """Small transport object representing a raw provider reply.

    Contains the response body and optional metadata.  Must never
    contain or expose secrets (API keys, auth headers, tokens).
    """

    content: str
    metadata: Mapping[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# AIProvider protocol
# ---------------------------------------------------------------------------


class AIProvider(Protocol):
    """Minimal provider interface for external AI judgment.

    The provider returns a raw/structured ``ProviderResponse`` — NOT
    an already trusted ``AIJudgment``.  Business policy lives in
    ``PolicyEngine`` (Phase 9), not here.
    """

    def invoke(
        self,
        prompt: str,
        context: Mapping[str, str],
    ) -> ProviderResponse:
        ...


# ---------------------------------------------------------------------------
# JSON parsing helpers (also used by tests)
# ---------------------------------------------------------------------------


def _parse_provider_json(raw: str) -> dict[str, Any]:
    """Parse raw provider JSON into a dict, raising typed errors."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidJSONError(
            f"provider returned invalid JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise InvalidResponseError(
            f"provider JSON root must be an object, got {type(data).__name__}"
        )
    return data


def _parse_provider_json_to_judgment(data: dict[str, Any]) -> AIJudgment:
    """Strictly validate and convert parsed JSON into an AIJudgment.

    Every required field is checked for existence and type before
    construction.  No coercion, no regex guessing, no fallback
    interpretation.
    """
    # relevance
    relevance_raw = data.get("relevance")
    if relevance_raw is None:
        raise SchemaValidationError("missing required field: relevance")
    if not isinstance(relevance_raw, float):
        raise SchemaValidationError(
            f"relevance must be float, got {type(relevance_raw).__name__}"
        )
    if relevance_raw < 0.0 or relevance_raw > 1.0:
        raise UnsupportedValueError(
            f"relevance {relevance_raw!r} outside [0.0, 1.0]"
        )

    # importance
    importance_raw = data.get("importance")
    if importance_raw is None:
        raise SchemaValidationError("missing required field: importance")
    if not isinstance(importance_raw, str):
        raise SchemaValidationError(
            f"importance must be str, got {type(importance_raw).__name__}"
        )
    try:
        importance = Importance(importance_raw)
    except ValueError:
        raise UnsupportedValueError(
            f"unsupported importance value: {importance_raw!r}"
        )

    # worth_notifying
    worth_notifying_raw = data.get("worth_notifying")
    if worth_notifying_raw is None:
        raise SchemaValidationError("missing required field: worth_notifying")
    if not isinstance(worth_notifying_raw, bool):
        raise SchemaValidationError(
            f"worth_notifying must be bool, got {type(worth_notifying_raw).__name__}"
        )

    # investigate
    investigate_raw = data.get("investigate")
    if investigate_raw is None:
        raise SchemaValidationError("missing required field: investigate")
    if not isinstance(investigate_raw, bool):
        raise SchemaValidationError(
            f"investigate must be bool, got {type(investigate_raw).__name__}"
        )

    # reason
    reason = data.get("reason")
    if reason is None:
        raise SchemaValidationError("missing required field: reason")
    if not isinstance(reason, str):
        raise SchemaValidationError(
            f"reason must be str, got {type(reason).__name__}"
        )
    if not reason.strip():
        raise SchemaValidationError("reason must be non-empty")

    # summary
    summary = data.get("summary")
    if summary is None:
        raise SchemaValidationError("missing required field: summary")
    if not isinstance(summary, str):
        raise SchemaValidationError(
            f"summary must be str, got {type(summary).__name__}"
        )
    if not summary.strip():
        raise SchemaValidationError("summary must be non-empty")

    return AIJudgment(
        relevance=relevance_raw,
        importance=importance,
        worth_notifying=worth_notifying_raw,
        investigate=investigate_raw,
        reason=reason,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# AIJudge
# ---------------------------------------------------------------------------


class AIJudge:
    """Orchestrates an AI judgment from context through a provider.

    * Accepts an ``AIProvider`` via dependency injection.
    * Builds a text prompt from ``AIContext``.
    * Invokes the provider.
    * Parses and strictly validates the provider response.
    * Returns an ``AIJudgment`` on success.
    * Raises an explicit ``AIError`` subtype on any failure.

    Never mutates Event, PolicyDecision, AIContext, or other domain state.
    """

    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    def judge(self, context: AIContext) -> AIJudgment:
        prompt = _build_prompt(context)
        headers = _build_context_headers(context)

        try:
            response = self._provider.invoke(prompt, headers)
        except (ProviderError, ProviderTimeoutError):
            raise
        except AIError:
            raise
        except Exception as exc:
            raise ProviderError(f"provider raised unhandled exception: {exc!r}") from exc

        data = _parse_provider_json(response.content)
        return _parse_provider_json_to_judgment(data)


def _build_prompt(context: AIContext) -> str:
    parts: list[str] = [
        f"event_type={context.event.event_type!r}",
        f"event_importance={context.event.importance!r}",
        f"policy_importance={context.policy_decision.importance.value}",
        f"policy_action={context.policy_decision.action.value}",
    ]
    if context.entity is not None:
        parts.append(
            f"entity={context.entity.canonical_key!r} "
            f"type={context.entity.entity_type!r}"
        )
    if context.signals:
        parts.append(
            "signals="
            + ",".join(s.signal_type for s in context.signals)
        )
    for idx, evidence in enumerate(context.evidence, start=1):
        parts.append(f"evidence_{idx}={evidence}")
    return "\n".join(parts)


def _build_context_headers(context: AIContext) -> dict[str, str]:
    """Return an empty context mapping.

    Phase 10A deliberately transmits no secrets, keys, or
    sensitive metadata to the provider.  Future phases may
    populate this mapping with non-sensitive metadata.
    """
    return {}
