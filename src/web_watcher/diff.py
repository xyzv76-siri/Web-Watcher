"""Diff computation for web observations.

A Diff answers "where did the content change?" between two normalized
observations of the same target/extractor.

Design:
    - Operates on normalized text (not raw HTML).
    - Returns structured metadata for downstream investigation.
    - Does not attempt semantic diff; this is a line/region-level text diff.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DiffResult:
    """Structured diff between two normalized observations."""

    changed: bool
    before: str = ""
    after: str = ""
    summary: str = ""
    regions: List[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @classmethod
    def unchanged(cls, before: str, after: str) -> "DiffResult":
        return cls(
            changed=False,
            before=before,
            after=after,
            summary="No change",
            regions=[],
            metadata={},
        )

    @classmethod
    def changed(
        cls,
        before: str,
        after: str,
        summary: str = "Content changed",
        regions: Optional[List[str]] = None,
        metadata: Optional[dict] = None,
    ) -> "DiffResult":
        return cls(
            changed=True,
            before=before,
            after=after,
            summary=summary,
            regions=regions or [],
            metadata=metadata or {},
        )


def compute_diff(before: str, after: str) -> DiffResult:
    """Compute a lightweight diff between two normalized text observations.

    This function is intentionally simple and deterministic. It does not
    import heavy third-party diff libraries so that the core observation
    pipeline stays lean.
    """
    if before == after:
        return DiffResult.unchanged(before, after)

    # Build a minimal region summary.
    # Split on whitespace to get comparable tokens.
    before_tokens = before.split()
    after_tokens = after.split()

    if not before_tokens:
        before_summary = "<empty>"
    elif len(before_tokens) <= 3:
        before_summary = " ".join(before_tokens)
    else:
        before_summary = " ".join(before_tokens[:3]) + " ..."

    if not after_tokens:
        after_summary = "<empty>"
    elif len(after_tokens) <= 3:
        after_summary = " ".join(after_tokens)
    else:
        after_summary = " ".join(after_tokens[:3]) + " ..."

    summary = f"Changed: {before_summary} -> {after_summary}"

    regions = []
    if before != after:
        regions.append(f"before_len={len(before)}")
        regions.append(f"after_len={len(after)}")

    return DiffResult.changed(
        before=before,
        after=after,
        summary=summary,
        regions=regions,
        metadata={"before_len": len(before), "after_len": len(after)},
    )
