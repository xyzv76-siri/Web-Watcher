"""Content normalization for extracted web observations.

Normalization converts raw extracted content into a stable, deterministic
form suitable for fingerprinting and diffing.

Boundaries:
    - Collapse whitespace variations that are not semantically meaningful.
    - Preserve text ordering and semantic content.
    - Do NOT remove arbitrary content.
    - Do NOT over-normalize to the point where real changes disappear.
"""

import re


_WHITESPACE_RE = re.compile(r"[ \t\r\n]+")


def normalize_extracted_text(raw: str) -> str:
    """Normalize extracted text content for stable fingerprinting.

    Steps:
        1. Convert non-string values to string representation.
        2. Strip leading/trailing whitespace.
        3. Collapse internal runs of whitespace characters into a single space.

    This removes formatting-only differences (indentation, line breaks,
    multiple spaces) while preserving the meaningful text.
    """
    if not isinstance(raw, str):
        raw = str(raw)
    if not raw:
        return ""
    stripped = raw.strip()
    return _WHITESPACE_RE.sub(" ", stripped)


def normalize_html_text(html_fragment: str) -> str:
    """Normalize an HTML fragment by stripping tags and normalizing whitespace.

    This is a lightweight fallback when selector extraction yields HTML
    rather than plain text. It does NOT attempt full DOM normalization.
    """
    if not html_fragment:
        return ""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", html_fragment)
    return normalize_extracted_text(text)
