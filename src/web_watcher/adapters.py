"""Adapter registry.

No concrete adapters are implemented in Phase 5.
"""

from typing import Sequence

from .fetch import SourceAdapter, select_adapter
from .targets import WatchTarget


class AdapterRegistry:
    """Holds an ordered set of adapters and resolves the right one for a target."""

    def __init__(self, adapters: Sequence[SourceAdapter] = ()):
        self._adapters = tuple(adapters)

    @property
    def adapters(self) -> tuple[SourceAdapter, ...]:
        return self._adapters

    def resolve(self, target: WatchTarget) -> SourceAdapter:
        return select_adapter(target, self._adapters)
