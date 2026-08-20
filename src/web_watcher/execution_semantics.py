"""Execution outcome taxonomy and terminal state transition model.

This module defines:
- ExecutionOutcome: the terminal outcome of a single adapter execution.
- StateTransition: the durable state changes to apply to a Target.
- transition_for(outcome, ...): maps an outcome to a StateTransition.

Design rules:
1. 304 is NOT a failure.
2. Policy blocked is NOT an immediate release-and-reclaim loop.
3. Network errors produce retry/backoff semantics.
4. Selector failure must NOT be interpreted as business deletion.
5. Transform failure must NOT overwrite previously durable good state.
6. Adapter exception must produce a defined execution failure transition.
7. Every claimed execution reaches either committed finalization or explicit safe failure finalization.
8. No outcome may silently fall through.
9. Do not let Adapter persist state.
10. Do not invent a second state machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from web_watcher.models import Target, TargetStatus
from web_watcher.fetch import FetchStatus


class ExecutionOutcome(str, Enum):
    """Terminal outcome of a single adapter execution."""
    SUCCESS_CHANGED = "success_changed"
    SUCCESS_UNCHANGED = "success_unchanged"
    NOT_MODIFIED = "not_modified"
    POLICY_BLOCKED = "policy_blocked"
    POLICY_COOLDOWN = "policy_cooldown"
    FETCH_FAILED = "fetch_failed"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    SELECTOR_NOT_FOUND = "selector_not_found"
    EMPTY_AFTER_TRANSFORM = "empty_after_transform"
    MULTIPLE_MATCH = "multiple_match"
    TRANSFORM_ERROR = "transform_error"
    ADAPTER_ERROR = "adapter_error"
    STALE_CLAIM = "stale_claim"


@dataclass(frozen=True)
class StateTransition:
    """Durable state changes to apply to a Target after execution."""
    status: TargetStatus
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_hash: Optional[str] = None
    metadata: Optional[dict] = None
    consecutive_failures: int = 0
    next_allowed_at: Optional[datetime] = None
    last_fetched_at: Optional[datetime] = None
    emit_signal: bool = False
    signal_payload: Optional[dict] = None
    reason: str = ""


def _default_interval(target: Target) -> float:
    from web_watcher.fetch_policy import parse_interval_seconds
    return parse_interval_seconds(target.interval)


def transition_for(
    outcome: ExecutionOutcome,
    *,
    target: Target,
    now: datetime,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    content_hash: Optional[str] = None,
    metadata: Optional[dict] = None,
    consecutive_failures: int = 0,
    next_allowed_at: Optional[datetime] = None,
    last_fetched_at: Optional[datetime] = None,
    emit_signal: bool = False,
    signal_payload: Optional[dict] = None,
    reason: str = "",
) -> StateTransition:
    """Map an ExecutionOutcome to a StateTransition.

    This is the single source of truth for how outcomes affect durable Target state.
    """

    interval_sec = _default_interval(target)

    if outcome == ExecutionOutcome.POLICY_BLOCKED:
        return StateTransition(
            status=target.status,
            etag=target.etag,
            last_modified=target.last_modified,
            content_hash=target.content_hash,
            metadata=target.metadata,
            consecutive_failures=target.consecutive_failures,
            next_allowed_at=now + timedelta(seconds=interval_sec),
            last_fetched_at=now,
            emit_signal=False,
            reason=reason or "Policy blocked",
        )

    if outcome == ExecutionOutcome.NOT_MODIFIED:
        return StateTransition(
            status=TargetStatus.NORMAL,
            etag=etag if etag is not None else target.etag,
            last_modified=last_modified if last_modified is not None else target.last_modified,
            content_hash=target.content_hash,
            metadata=target.metadata,
            consecutive_failures=0,
            next_allowed_at=now + timedelta(seconds=interval_sec),
            last_fetched_at=now,
            emit_signal=False,
            reason=reason or "304 Not Modified",
        )

    if outcome == ExecutionOutcome.SUCCESS_CHANGED:
        return StateTransition(
            status=TargetStatus.NORMAL,
            etag=etag if etag is not None else target.etag,
            last_modified=last_modified if last_modified is not None else target.last_modified,
            content_hash=content_hash if content_hash is not None else target.content_hash,
            metadata=metadata if metadata is not None else target.metadata,
            consecutive_failures=0,
            next_allowed_at=now + timedelta(seconds=interval_sec),
            last_fetched_at=now,
            emit_signal=emit_signal,
            signal_payload=signal_payload,
            reason=reason or "Success with content change",
        )

    if outcome == ExecutionOutcome.SUCCESS_UNCHANGED:
        return StateTransition(
            status=TargetStatus.NORMAL,
            etag=etag if etag is not None else target.etag,
            last_modified=last_modified if last_modified is not None else target.last_modified,
            content_hash=target.content_hash,
            metadata=metadata if metadata is not None else target.metadata,
            consecutive_failures=0,
            next_allowed_at=now + timedelta(seconds=interval_sec),
            last_fetched_at=now,
            emit_signal=emit_signal,
            signal_payload=signal_payload,
            reason=reason or "Success but content unchanged",
        )

    if outcome == ExecutionOutcome.POLICY_COOLDOWN:
        failures = consecutive_failures if consecutive_failures > 0 else target.consecutive_failures
        return StateTransition(
            status=TargetStatus.COOLDOWN,
            etag=target.etag,
            last_modified=target.last_modified,
            content_hash=target.content_hash,
            metadata=metadata if metadata is not None else target.metadata,
            consecutive_failures=failures,
            next_allowed_at=next_allowed_at if next_allowed_at is not None else now + timedelta(seconds=interval_sec),
            last_fetched_at=now,
            emit_signal=False,
            reason=reason or "Policy cooldown",
        )

    if outcome in (ExecutionOutcome.FETCH_FAILED, ExecutionOutcome.NETWORK_ERROR,
                   ExecutionOutcome.TIMEOUT, ExecutionOutcome.ADAPTER_ERROR):
        failures = consecutive_failures if consecutive_failures > 0 else target.consecutive_failures + 1
        effective_next = next_allowed_at if next_allowed_at is not None else now + timedelta(seconds=interval_sec)
        # COOLDOWN or RECOVERING probe that failed returns to/keeps COOLDOWN
        if target.status in (TargetStatus.COOLDOWN, TargetStatus.RECOVERING):
            return StateTransition(
                status=TargetStatus.COOLDOWN,
                etag=target.etag,
                last_modified=target.last_modified,
                content_hash=target.content_hash,
                metadata=metadata if metadata is not None else target.metadata,
                consecutive_failures=failures,
                next_allowed_at=effective_next,
                last_fetched_at=now,
                emit_signal=False,
                reason=reason or "Probe failure during cooldown/recovery",
            )
        return StateTransition(
            status=TargetStatus.BACKOFF,
            etag=target.etag,
            last_modified=target.last_modified,
            content_hash=target.content_hash,
            metadata=metadata if metadata is not None else target.metadata,
            consecutive_failures=failures,
            next_allowed_at=effective_next,
            last_fetched_at=now,
            emit_signal=False,
            reason=reason or "Temporary failure",
        )

    if outcome == ExecutionOutcome.SELECTOR_NOT_FOUND:
        return StateTransition(
            status=TargetStatus.NORMAL,
            etag=etag if etag is not None else target.etag,
            last_modified=last_modified if last_modified is not None else target.last_modified,
            content_hash=content_hash if content_hash is not None else target.content_hash,
            metadata=metadata if metadata is not None else target.metadata,
            consecutive_failures=0,
            next_allowed_at=now + timedelta(seconds=interval_sec),
            last_fetched_at=now,
            emit_signal=False,
            reason=reason or "Selector not found",
        )

    if outcome == ExecutionOutcome.EMPTY_AFTER_TRANSFORM:
        return StateTransition(
            status=TargetStatus.NORMAL,
            etag=etag if etag is not None else target.etag,
            last_modified=last_modified if last_modified is not None else target.last_modified,
            content_hash=content_hash if content_hash is not None else target.content_hash,
            metadata=metadata if metadata is not None else target.metadata,
            consecutive_failures=0,
            next_allowed_at=now + timedelta(seconds=interval_sec),
            last_fetched_at=now,
            emit_signal=False,
            reason=reason or "Empty after transform",
        )

    if outcome == ExecutionOutcome.MULTIPLE_MATCH:
        return StateTransition(
            status=TargetStatus.NORMAL,
            etag=etag if etag is not None else target.etag,
            last_modified=last_modified if last_modified is not None else target.last_modified,
            content_hash=content_hash if content_hash is not None else target.content_hash,
            metadata=metadata if metadata is not None else target.metadata,
            consecutive_failures=0,
            next_allowed_at=now + timedelta(seconds=interval_sec),
            last_fetched_at=now,
            emit_signal=False,
            reason=reason or "Multiple matches; no signal emitted",
        )

    if outcome == ExecutionOutcome.TRANSFORM_ERROR:
        return StateTransition(
            status=TargetStatus.NORMAL,
            etag=etag if etag is not None else target.etag,
            last_modified=last_modified if last_modified is not None else target.last_modified,
            content_hash=target.content_hash,
            metadata=target.metadata,
            consecutive_failures=0,
            next_allowed_at=now + timedelta(seconds=interval_sec),
            last_fetched_at=now,
            emit_signal=False,
            reason=reason or "Transform error; preserving previous durable state",
        )

    if outcome == ExecutionOutcome.ADAPTER_ERROR:
        failures = target.consecutive_failures + 1
        return StateTransition(
            status=TargetStatus.BACKOFF,
            etag=target.etag,
            last_modified=target.last_modified,
            content_hash=target.content_hash,
            metadata=target.metadata,
            consecutive_failures=failures,
            next_allowed_at=next_allowed_at if next_allowed_at is not None else now + timedelta(seconds=interval_sec),
            last_fetched_at=now,
            emit_signal=False,
            reason=reason or "Adapter error",
        )

    if outcome == ExecutionOutcome.STALE_CLAIM:
        return StateTransition(
            status=target.status,
            etag=target.etag,
            last_modified=target.last_modified,
            content_hash=target.content_hash,
            metadata=target.metadata,
            consecutive_failures=target.consecutive_failures,
            next_allowed_at=target.next_allowed_at,
            last_fetched_at=target.last_fetched_at,
            emit_signal=False,
            reason=reason or "Stale claim; state unchanged",
        )

    raise ValueError(f"Unhandled ExecutionOutcome: {outcome}")
