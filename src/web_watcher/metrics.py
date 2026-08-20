"""Minimal metrics abstraction for Web Watcher.

Only records counters; no external dependencies.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple


class Metrics:
    """Thread-unsafe in-memory counter collection."""

    def __init__(self) -> None:
        self._counters: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], int] = {}

    def _key(self, name: str, tags: Optional[Dict[str, str]] = None) -> Tuple[str, Tuple[Tuple[str, str], ...]]:
        if not tags:
            return (name, ())
        return (name, tuple(sorted((k, str(v)) for k, v in tags.items())))

    def increment(self, name: str, tags: Optional[Dict[str, str]] = None, amount: int = 1) -> None:
        key = self._key(name, tags)
        self._counters[key] = self._counters.get(key, 0) + amount

    def get(self, name: str, tags: Optional[Dict[str, str]] = None) -> int:
        key = self._key(name, tags)
        return self._counters.get(key, 0)

    def to_dict(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for (name, tag_tuple), value in self._counters.items():
            if tag_tuple:
                suffix = ",".join(f"{k}={v}" for k, v in tag_tuple)
                result[f"{name}[{suffix}]"] = value
            else:
                result[name] = value
        return result

    def reset(self) -> None:
        self._counters.clear()
