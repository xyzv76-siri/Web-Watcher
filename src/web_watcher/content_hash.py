"""Deterministic content hashing utilities."""

import hashlib


def sha256_of(content: str) -> str:
    """Return a deterministic SHA-256 hex digest of *content*.

    Same input always produces the same digest.
    No timestamps or random values are mixed in.
    """
    if not isinstance(content, str):
        raise TypeError("content must be a string")

    return hashlib.sha256(content.encode("utf-8")).hexdigest()
