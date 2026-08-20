"""Deterministic observation fingerprinting for Generic Web Target.

Unlike raw-html hashing, this fingerprints the *normalized* extracted content,
so deterministic changes to formatting or irrelevant markup do not produce
spurious fingerprints.

The fingerprint is stable across process restarts and does not include
timestamps, random values, or runtime state.
"""

import hashlib
from typing import Optional


def observation_fingerprint(
    target_id: str,
    normalized_content: str,
    selector_fingerprint: Optional[str] = None,
) -> str:
    """Compute a deterministic SHA-256 fingerprint for one observation.

    Inputs:
        target_id            - stable target identifier
        normalized_content   - canonical normalized extracted content
        selector_fingerprint - optional fingerprint of the selector configuration

    Same inputs always produce the same digest.
    """
    parts = [
        target_id,
        normalized_content,
        selector_fingerprint if selector_fingerprint is not None else "",
    ]
    payload = "\x1f".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def selector_config_fingerprint(selector_type: str, selector: str) -> str:
    """Compute a deterministic fingerprint for a selector configuration.

    Two extractors with the same type and selector produce the same fingerprint,
    regardless of name or position in the configuration list.
    """
    payload = f"{selector_type}\x1f{selector}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
