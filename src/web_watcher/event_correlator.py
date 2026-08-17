"""Deterministic event correlation — Phase 8.

Converts Signals into coherent Events.

Correlation Rule V1:
    Signals may belong to the same Event when:
      1. They belong to the same Entity.
      2. The Event is still open.
      3. The Signal's observed_at is within the correlation window
         of the Event's created_at.

Default correlation window: 24 hours.
No AI, LLM, embeddings, semantic similarity, network calls, or scheduling.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import Event, Signal
from .repository import Repository


class CorrelationConfig:
    """Configurable, deterministic correlation parameters."""

    def __init__(
        self,
        correlation_window_seconds: int = 24 * 3600,
        default_importance: str = "medium",
    ):
        if correlation_window_seconds <= 0:
            raise ValueError("correlation_window_seconds must be positive")
        self.correlation_window_seconds = correlation_window_seconds
        self.default_importance = default_importance

    @property
    def window(self) -> timedelta:
        return timedelta(seconds=self.correlation_window_seconds)


class EventCorrelator:
    """Correlates Signals into Events deterministically.

    Does NOT:
        - call AI / LLM
        - send Telegram messages
        - access the network
        - schedule jobs
        - use wall-clock time (test injection supported)
    """

    def __init__(
        self,
        repository: Repository,
        config: Optional[CorrelationConfig] = None,
        now_factory: Optional[callable] = None,
    ):
        self._repo = repository
        self._config = config or CorrelationConfig()
        # Injectable clock for deterministic tests
        self._now_factory = now_factory or _utcnow

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def correlate(self, signal: Signal) -> Event:
        """Correlate *signal* into an existing open Event, or create a new one.

        The Signal is first persisted to the repository (create_signal).
        If it is a duplicate fingerprint for the same entity+signal_type,
        create_signal returns None and correlate() re-persists it with a
        derived unique fingerprint before proceeding.

        Steps:
            1. Persist the Signal into the repository
            2. Determine the time window: [now - window, now]
            3. Look for an open Event for the same entity within the window
            4. If found → attach signal to that Event
            5. Otherwise → create a new Event and attach signal

        Returns the Event the signal belongs to.
        """
        now = self._now_factory()

        # Ensure the signal exists in the database before correlating.
        signal = self._persist_signal(signal)

        window_start = now - self._config.window

        open_event = self._repo.find_open_event_for_entity(
            entity_id=signal.entity_id,
            cutoff=window_start,
        )

        if open_event is not None:
            self._repo.attach_signal_to_event(open_event.id, signal.id)
            self._repo.update_event(
                event_id=open_event.id,
                updated_at=now,
            )
            return open_event

        event_type = _derive_event_type(signal)
        event = self._repo.create_event(
            entity_id=signal.entity_id,
            event_type=event_type,
            status="open",
            importance=self._config.default_importance,
            created_at=signal.observed_at,
        )
        self._repo.attach_signal_to_event(event.id, signal.id)
        return event

    def close_event(self, event_id: int) -> Optional[Event]:
        """Close an open Event. A closed Event will not accept new Signals.

        Returns the closed Event or None if not found.
        """
        return self._repo.update_event(event_id=event_id, status="closed")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _persist_signal(self, signal: Signal) -> Signal:
        """Persist a Signal into the repository, returning the stored object.

        If the fingerprint is already taken (duplicate observation),
        a new unique fingerprint is derived and used.
        """
        stored = self._repo.create_signal(
            entity_id=signal.entity_id,
            signal_type=signal.signal_type,
            observed_at=signal.observed_at,
            value=signal.value,
            fingerprint=signal.fingerprint,
        )
        if stored is not None:
            return stored

        # Duplicate fingerprint — create a new unique one
        unique_fp = (
            signal.fingerprint
            if signal.fingerprint
            else "auto-fp"
        ) + "-dup-" + str(abs(hash(signal.value or "")))
        stored = self._repo.create_signal(
            entity_id=signal.entity_id,
            signal_type=signal.signal_type,
            observed_at=signal.observed_at,
            value=signal.value,
            fingerprint=unique_fp,
        )
        if stored is None:
            raise RuntimeError(
                f"unable to persist signal for entity={signal.entity_id} "
                f"type={signal.signal_type}"
            )
        return stored

    def _count_signals_for_event(self, event_id: int) -> int:
        return len(self._repo.get_event_signals(event_id))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _derive_event_type(signal: Signal) -> str:
    """Derive a canonical event_type from a Signal's type.

    Phase 8 V1 mapping — deterministic, one-to-one:
        content_change  →  content_change

    No suffix, no transformation. Keep it extensible for future
    signal types (star_velocity, release, trending, commit_velocity)
    without rewriting this layer.
    """
    return signal.signal_type
