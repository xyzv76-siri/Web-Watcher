"""Fetcher and source-adapter contracts.

This module defines interfaces only.
No network access is performed here.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Optional, Protocol, Sequence

from .targets import WatchTarget


@dataclass(frozen=True)
class FetchRequest:
    """Immutable request sent to a source adapter."""

    target: WatchTarget
    etag: Optional[str] = None
    last_modified: Optional[str] = None


@dataclass(frozen=True)
class FetchResult:
    """Immutable result returned by a source adapter."""

    target_key: str
    success: bool
    status_code: Optional[int]
    fetched_at: datetime
    content: Optional[str] = None
    content_type: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_hash: Optional[str] = None
    error: Optional[str] = None
    metadata: Mapping[str, str] = field(default_factory=dict)


class Fetcher(Protocol):
    """Protocol for any object that can fetch a single target."""

    def fetch(self, request: FetchRequest) -> FetchResult: ...


class SourceAdapter(Protocol):
    """Protocol for a target-type-specific fetch implementation."""

    def supports(self, target: WatchTarget) -> bool: ...

    def fetch(self, request: FetchRequest) -> FetchResult: ...


def select_adapter(
    target: WatchTarget,
    adapters: Sequence[SourceAdapter],
) -> SourceAdapter:
    """Select exactly one adapter for a target.

    Raises LookupError when zero or multiple adapters match.
    """
    matches = [
        adapter
        for adapter in adapters
        if adapter.supports(target)
    ]

    if not matches:
        raise LookupError(
            f"no adapter available for target: {target.key}"
        )

    if len(matches) > 1:
        raise LookupError(
            f"multiple adapters available for target: {target.key}"
        )

    return matches[0]
