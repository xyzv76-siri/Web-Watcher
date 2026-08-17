"""Single-fetch service.

Orchestrates one fetch for one target:

    FetchRequest
        ↓
    Adapter (resolved by adapter registry)
        ↓
    FetchResult
        ↓
    Persistence (FetchState upsert + optional Signal creation)

The service performs exactly one fetch operation.
It is NOT a scheduler, does NOT sleep, and does NOT run forever.

Persistence semantics (Phase 7):

    * 304 Not Modified
        - does NOT replace content_hash
        - does NOT create a new Signal
        - preserves etag, last_modified, content_hash
        - does NOT update fetched_at (existing state is authoritative)

    * 200 with NEW content_hash
        - upserts FetchState (etag, last_modified, content_hash, fetched_at)
        - resolves target to a canonical Entity (creating it if absent)
        - creates exactly one Signal with a deterministic fingerprint

    * 200 with UNCHANGED content_hash
        - upserts FetchState (etag, last_modified, fetched_at)
        - does NOT create another Signal

    * Failed FetchResult
        - does NOT update content_hash
        - does NOT create a new Signal
        - does NOT destroy previous FetchState
        - returns the failure result to the caller
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .signal_types import SignalType
from .adapters import AdapterRegistry
from .content_hash import sha256_of
from .fetch import FetchRequest, FetchResult
from .fingerprint import fingerprint_for_signal
from .models import Entity, FetchState, Signal
from .repository import Repository
from .targets import WatchTarget


# Canonical signal type used for "content changed" observations.
_SIGNAL_TYPE_CONTENT_CHANGE = SignalType.CONTENT_CHANGE.value


def _canonical_entity_key(target: WatchTarget) -> str:
    """Map a WatchTarget key to a stable Entity canonical_key.

    Example: 'github:octocat/Hello-World' -> 'github:octocat/Hello-World'.
    The canonical key preserves the locator so that repeated fetches for
    the same target always resolve to the same Entity.
    """
    return target.key


def _canonical_entity_name(target: WatchTarget) -> str:
    return target.name


def _canonical_entity_type(target: WatchTarget) -> str:
    return target.target_type


class FetchService:
    """Executes a single fetch-and-persist cycle for one target."""

    def __init__(
        self,
        repository: Repository,
        adapter_registry: Optional[AdapterRegistry] = None,
    ):
        self._repo = repository
        self._registry = adapter_registry or AdapterRegistry()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_one(self, target: WatchTarget) -> FetchResult:
        """Fetch *target* exactly once and persist the result.

        Returns the FetchResult (may be success or failure).
        """
        adapter = self._registry.resolve(target)

        # Capture prior state *before* the fetch so we can decide whether
        # the new result represents a real content change.
        prior = self._repo.get_fetch_state(target.key)

        request = self._build_request(target, prior)
        result = adapter.fetch(request)

        self._apply_result(target, prior, result)

        return result

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _build_request(
        self,
        target: WatchTarget,
        prior: Optional[FetchState],
    ) -> FetchRequest:
        if prior is None:
            return FetchRequest(target=target)

        return FetchRequest(
            target=target,
            etag=prior.etag,
            last_modified=prior.last_modified,
        )

    def _apply_result(
        self,
        target: WatchTarget,
        prior: Optional[FetchState],
        result: FetchResult,
    ) -> None:
        """Persist a FetchResult according to the Phase 7 rules."""

        if not result.success:
            # Failed fetch → do NOT overwrite previous FetchState,
            # do NOT create a Signal, do NOT destroy previous state.
            return

        # ---- 304 Not Modified ------------------------------------------------
        if result.status_code == 304:
            # Preserve everything: etag, last_modified, content_hash, fetched_at.
            return

        # ---- 200 with content ------------------------------------------------
        if result.content is None or result.status_code != 200:
            # No content (204 / empty) → nothing to persist as state or signal.
            return

        content_hash = result.content_hash or sha256_of(result.content)

        # Did this observation represent a real content change?
        previous_hash = prior.content_hash if prior is not None else None
        new_hash = previous_hash is None or content_hash != previous_hash

        # Update FetchState with fresh metadata (etag, last_modified, fetched_at)
        self._repo.upsert_fetch_state(
            FetchState(
                target_key=result.target_key,
                etag=result.etag,
                last_modified=result.last_modified,
                content_hash=content_hash,
                fetched_at=result.fetched_at,
            )
        )

        if not new_hash:
            # Content unchanged → do NOT create another Signal.
            return

        # Resolve or create the canonical Entity for this target.
        entity = self._repo.get_or_create_entity(
            canonical_key=_canonical_entity_key(target),
            name=_canonical_entity_name(target),
            entity_type=_canonical_entity_type(target),
        )

        # Deterministic fingerprint so the same observation never
        # produces duplicate Signal rows.
        fingerprint = fingerprint_for_signal(
            entity_id=entity.id,
            signal_type=_SIGNAL_TYPE_CONTENT_CHANGE,
            value=content_hash,
        )

        # create_signal() uses the UNIQUE(entity_id, signal_type, fingerprint)
        # constraint to skip duplicates.
        self._repo.create_signal(
            entity_id=entity.id,
            signal_type=_SIGNAL_TYPE_CONTENT_CHANGE,
            observed_at=result.fetched_at,
            value=content_hash,
            fingerprint=fingerprint,
        )
