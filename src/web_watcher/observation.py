"""Observation result model for Generic Web Target.

This module defines the structured output of a single observation cycle.
It is intentionally decoupled from Signal/Event creation; downstream
components decide whether and how to promote an observation into events.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .diff import DiffResult
from .rule_models import ExtractionResult


class ObservationStatus(str):
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    EXTRACTION_FAILURE = "extraction_failure"
    HTTP_FAILURE = "http_failure"
    FIRST_OBSERVATION = "first_observation"


@dataclass
class ObservationResult:
    """Result of observing a single Generic Web Target."""

    target_id: str
    status: str
    # Raw response reference (not the full HTML, to keep payloads small).
    # Downstream can request raw HTML from evidence storage if needed.
    status_code: Optional[int] = None
    # Per-extractor results
    extracted_results: Dict[str, ExtractionResult] = field(default_factory=dict)
    # Normalized, fingerprinted content keyed by extractor name.
    normalized_values: Dict[str, str] = field(default_factory=dict)
    # Fingerprints keyed by extractor name.
    fingerprints: Dict[str, str] = field(default_factory=dict)
    # Diffs keyed by extractor name.
    diffs: Dict[str, DiffResult] = field(default_factory=dict)
    # Previous normalized values (for evidence chain).
    previous_values: Dict[str, str] = field(default_factory=dict)
    # Stable evidence chain metadata.
    evidence: Dict[str, Any] = field(default_factory=dict)
    observed_at: datetime = field(default_factory=datetime.utcnow)
    reason: str = ""

    def is_changed(self) -> bool:
        return self.status == ObservationStatus.CHANGED

    def is_unchanged(self) -> bool:
        return self.status == ObservationStatus.UNCHANGED

    def is_first_observation(self) -> bool:
        return self.status == ObservationStatus.FIRST_OBSERVATION

    def has_extraction_failures(self) -> bool:
        return self.status == ObservationStatus.EXTRACTION_FAILURE

    def has_http_failure(self) -> bool:
        return self.status == ObservationStatus.HTTP_FAILURE

    def changed_extractors(self) -> List[str]:
        return [name for name, diff in self.diffs.items() if diff.changed]

    def summary(self) -> Dict[str, Any]:
        return {
            "target_id": self.target_id,
            "status": self.status,
            "status_code": self.status_code,
            "changed_extractors": self.changed_extractors(),
            "extraction_failures": [
                name for name, result in self.extracted_results.items()
                if not result.is_found
            ],
            "observed_at": self.observed_at.isoformat(),
            "reason": self.reason,
        }
