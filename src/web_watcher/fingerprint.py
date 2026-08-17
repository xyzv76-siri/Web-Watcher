"""Deterministic fingerprint generation for Signal deduplication.

A fingerprint is a stable, deterministic identifier for a single observation.
Two identical observations produce the same fingerprint; different observations
produce different fingerprints.

No timestamps, random values, or runtime state are mixed in.
"""

import hashlib


def signal_fingerprint(
    entity_id: int,
    signal_type: str,
    value: str | None,
    observed_at: str | None = None,
) -> str:
    """Compute a deterministic SHA-256 fingerprint for a Signal.

    Inputs:
        entity_id    - integer entity id
        signal_type  - canonical string type (e.g. 'content_change')
        value        - canonical string value (may be None)
        observed_at  - optional ISO timestamp string

    The fingerprint is stable across calls with the same inputs.
    """
    parts = [
        str(entity_id),
        signal_type,
        value if value is not None else "",
        observed_at if observed_at is not None else "",
    ]
    payload = "\x1f".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fingerprint_for_signal(entity_id: int, signal_type: str, value: str) -> str:
    """Shorthand for the common case where observed_at is not fingerprinted."""
    return signal_fingerprint(
        entity_id=entity_id,
        signal_type=signal_type,
        value=value,
        observed_at=None,
    )
