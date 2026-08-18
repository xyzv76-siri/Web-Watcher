"""Domain vocabulary for Event/Decision Importance levels."""

from enum import Enum
from typing import Any, Union


class Importance(str, Enum):
    """Normalized importance levels across the Web-Watcher pipeline."""

    IGNORE = "ignore"
    INTERESTING = "interesting"
    IMPORTANT = "important"
    CRITICAL = "critical"

    @classmethod
    def from_value(cls, val: Union[str, "Importance", Any]) -> "Importance":
        """Safely parse input string or Enum into an Importance member."""
        if isinstance(val, cls):
            return val
        if isinstance(val, str):
            normalized = val.strip().lower()
            for member in cls:
                if member.value == normalized:
                    return member
            upper_name = val.strip().upper()
            if upper_name in cls.__members__:
                return cls.__members__[upper_name]
        raise ValueError(f"Invalid importance value: {val}")

    def __str__(self) -> str:
        return self.value
