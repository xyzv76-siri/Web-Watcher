"""Single-fetch service.

Orchestrates one fetch for one target:

    FetchRequest
        ↓
    Adapter (resolved by adapter registry)
        ↓
    FetchResult
        ↓
    Persistence (FetchState upsert)

The service performs exactly one fetch operation.
It is NOT a scheduler, does NOT sleep, and does NOT run forever.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .adapters import AdapterRegistry
from .content_hash import sha256_of
from .fetch import FetchRequest, FetchResult
from .models import FetchState
from .repository import Repository
from .targets import WatchTarget


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

        request = self._build_request(target)
        result = adapter.fetch(request)
        self._persist_result(result)

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_request(self, target: WatchTarget) -> FetchRequest:
        """Build a FetchRequest, attaching cached headers if available."""
        state = self._repo.get_fetch_state(target.key)
        if state is None:
            return FetchRequest(target=target)

        return FetchRequest(
            target=target,
            etag=state.etag,
            last_modified=state.last_modified,
        )

    def _persist_result(self, result: FetchResult) -> None:
        """Persist the fetch result as a FetchState row."""
        if result.success and result.status_code != 304 and result.content:
            # Successful non-304 with content → compute hash + update
            content_hash = sha256_of(result.content)

            self._repo.upsert_fetch_state(
                FetchState(
                    target_key=result.target_key,
                    etag=result.etag,
                    last_modified=result.last_modified,
                    content_hash=content_hash,
                    fetched_at=result.fetched_at,
                )
            )

        elif result.success and result.status_code == 304:
            # 304 Not Modified: preserve existing state, no write needed
            return

        # Non-success results are NOT persisted as fetch state.
        # They are returned to the caller but do not overwrite cached state.
