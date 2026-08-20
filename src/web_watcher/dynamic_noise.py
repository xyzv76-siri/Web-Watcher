"""Dynamic noise detection and filtering for web observations.

This module identifies and normalizes common dynamic content patterns
that are not semantically meaningful for change detection:

- timestamps (absolute and relative)
- session identifiers (UUID, GUID, random tokens)
- tracking parameters
- countdown timers
- non-semantic formatting variations

Design goals:
    - deterministic
    - configurable
    - bounded
    - explainable
    - no false negatives (real changes must not be hidden)
"""

import re
from typing import List, Optional, Pattern


# ---------------------------------------------------------------------------
# Compiled patterns (ordered by specificity to avoid partial matches)
# ---------------------------------------------------------------------------

# ISO 8601 timestamps with timezone: 2026-08-19T12:34:56Z, 2026-08-19T12:34:56+08:00
_ISO_TIMESTAMP_RE: re.Pattern = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)

# Common HTTP-date / RFC 1123: Wed, 19 Aug 2026 00:00:00 GMT
_RFC1123_DATE_RE: re.Pattern = re.compile(
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s\d{2}\s(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s\d{4}\s\d{2}:\d{2}:\d{2}\sGMT"
)

# Unix timestamps (10 or 13 digit numbers standing alone or with context)
_UNIX_TIMESTAMP_RE: re.Pattern = re.compile(r"\b\d{10,13}\b")

# Relative time phrases commonly seen in countdowns / age indicators
_RELATIVE_TIME_RE: re.Pattern = re.compile(
    r"\b(?:\d+[smhd])\b|\b(?:just now|moments? ago|seconds? ago|minutes? ago|hours? ago|days? ago|weeks? ago|months? ago|years? ago)\b",
    re.IGNORECASE,
)

# UUID / GUID variants (8-4-4-4-12 hex digits, with or without braces)
_UUID_RE: re.Pattern = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Tracking / analytics query parameters (utm_*, _ga, _gid, etc.)
_TRACKING_PARAM_RE: re.Pattern = re.compile(
    r"(?:utm_[a-zA-Z0-9_]+|_ga|_gid|_gat|__utm[bvp]|fbclid|gclid|msclkid|dclid|twclid|_gl|gbraid|wbraid|_ke|mc_[a-zA-Z0-9_]+|pk_[a-zA-Z0-9_]+)=[^\s&]+"
)

# Random-looking hex tokens (32+ hex chars, often used as CSRF / session tokens)
_HEX_TOKEN_RE: re.Pattern = re.compile(r"\b[0-9a-fA-F]{32,}\b")

# Generic base64-ish tokens (20+ chars, mixed case, digits, +/=)
_BASE64_TOKEN_RE: re.Pattern = re.compile(r"\b[A-Za-z0-9+/]{20,}={0,2}\b")

# JWT / JWS tokens: three base64url segments joined by dots
_JWT_RE: re.Pattern = re.compile(r"\b[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")

# Session / cookie style name=value where value is a long opaque string
_OPAQUE_SESSION_RE: re.Pattern = re.compile(
    r"\b(?:session|sess|sid|token|auth|access|refresh|csrf|xsrf|nonce|state|code|sig)[-_]?[a-zA-Z0-9]*\s*[=:]\s*[^\s]{16,}\b",
    re.IGNORECASE,
)


def _default_patterns() -> List[re.Pattern]:
    return [
        _ISO_TIMESTAMP_RE,
        _RFC1123_DATE_RE,
        _UNIX_TIMESTAMP_RE,
        _RELATIVE_TIME_RE,
        _UUID_RE,
        _TRACKING_PARAM_RE,
        _HEX_TOKEN_RE,
        _JWT_RE,
        _BASE64_TOKEN_RE,
        _OPAQUE_SESSION_RE,
    ]


class DynamicNoiseFilter:
    """Configurable dynamic noise filter.

    Usage:
        noise_filter = DynamicNoiseFilter()
        clean = noise_filter.filter("Price $99 at 2026-08-19T12:00:00Z session=abc123...")
    """

    def __init__(self, patterns: Optional[List[Pattern]] = None, placeholder: str = ""):
        self.patterns = patterns if patterns is not None else _default_patterns()
        self.placeholder = placeholder

    def filter(self, text: str) -> str:
        """Replace dynamic noise in *text* with the configured placeholder.

        Returns the filtered text. The original text is never modified.
        """
        if not text:
            return text
        result = text
        for pattern in self.patterns:
            result = pattern.sub(self.placeholder, result)
        return result

    def filter_normalize(self, text: str) -> str:
        """Filter then normalize whitespace.

        Convenience method combining noise removal with standard normalization.
        """
        filtered = self.filter(text)
        # Collapse any whitespace runs introduced by removals.
        filtered = re.sub(r"[ \t\r\n]+", " ", filtered).strip()
        return filtered


# ---------------------------------------------------------------------------
# High-level dynamic-noise detection helpers
# ---------------------------------------------------------------------------

def is_likely_dynamic_noise(text: str) -> bool:
    """Heuristic: does *text* look like pure dynamic noise?

    This is a lightweight pre-check, not a guarantee.
    """
    if not text or not text.strip():
        return False

    # If the entire text matches a single dynamic pattern, treat as noise.
    stripped = text.strip()
    for pattern in _default_patterns():
        if pattern.fullmatch(stripped):
            return True
    return False


def dynamic_noise_ratio(text: str) -> float:
    """Estimate the fraction of *text* consumed by dynamic patterns.

    Returns a value in [0.0, 1.0].
    """
    if not text or not text.strip():
        return 0.0

    total_len = len(text)
    matched_len = 0
    for pattern in _default_patterns():
        for _ in pattern.finditer(text):
            pass  # We just need the count of matches
    # Simpler: compute length of all matched spans.
    covered_spans: List[tuple] = []
    for pattern in _default_patterns():
        for m in pattern.finditer(text):
            covered_spans.append((m.start(), m.end()))
    # Merge overlapping spans
    covered_spans.sort()
    merged: List[tuple] = []
    for start, end in covered_spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    covered = sum(end - start for start, end in merged)
    return min(1.0, covered / total_len)


def contains_dynamic_noise(text: str, threshold: float = 0.3) -> bool:
    """Return True if dynamic noise exceeds *threshold* of the text."""
    return dynamic_noise_ratio(text) >= threshold


# ---------------------------------------------------------------------------
# False-positive guard
# ---------------------------------------------------------------------------

class FalsePositiveGuard:
    """Observation-level false positive protection.

    This guard decides whether a detected change should be promoted to a
    Signal or suppressed as likely dynamic noise.

    Rules:
        1. First observation never produces a signal.
        2. Extraction failure never produces a content-change signal.
        3. If all changed extractors are pure dynamic noise, suppress signal.
        4. If dynamic noise ratio exceeds threshold and semantic content is unchanged, suppress.
        5. HTTP failures (403/429/timeout/etc.) are never content changes.
    """

    def __init__(
        self,
        noise_filter: Optional[DynamicNoiseFilter] = None,
        dynamic_noise_threshold: float = 0.5,
    ):
        self.noise_filter = noise_filter or DynamicNoiseFilter()
        self.dynamic_noise_threshold = dynamic_noise_threshold

    def should_suppress_signal(
        self,
        diffs: dict,
        normalized_values: dict,
        previous_values: dict,
        all_extractors_failed: bool,
        is_first_observation: bool,
        http_status_code: Optional[int] = None,
    ) -> tuple[bool, str]:
        """Return (suppress, reason) for a candidate change observation.

        Args:
            diffs: extractor_name -> DiffResult
            normalized_values: extractor_name -> current normalized text
            previous_values: extractor_name -> previous normalized text
            all_extractors_failed: True if every extractor reported not_found
            is_first_observation: True if this is the first successful fetch
            http_status_code: HTTP status code, if available

        Returns:
            (suppress: bool, reason: str)
        """
        # Rule 1: first observation never emits a fake change signal.
        if is_first_observation:
            return True, "First observation; baseline established, not a change"

        # Rule 2: extraction failure never emits a content-change signal.
        if all_extractors_failed:
            return True, "All extractors failed; cannot confirm content change"

        # Rule 3: HTTP failures are never content changes.
        if http_status_code is not None and http_status_code >= 400:
            return True, f"HTTP {http_status_code}; not a content change"

        # Rule 4 & 5: dynamic-noise-based suppression.
        changed_extractors = [name for name, diff in diffs.items() if diff.changed]
        if not changed_extractors:
            return False, "No extractor reported a change"

        suppressed_extractors: List[str] = []
        kept_extractors: List[str] = []

        for name in changed_extractors:
            prev = previous_values.get(name, "")
            curr = normalized_values.get(name, "")
            # Filter dynamic noise from both sides.
            prev_clean = self.noise_filter.filter(prev)
            curr_clean = self.noise_filter.filter(curr)
            # If semantic content is identical after noise removal, suppress.
            if prev_clean == curr_clean:
                suppressed_extractors.append(name)
            else:
                kept_extractors.append(name)

        if kept_extractors:
            return False, f"Semantic change detected in: {', '.join(kept_extractors)}"

        if suppressed_extractors:
            return True, (
                f"Change suppressed as likely dynamic noise in: {', '.join(suppressed_extractors)}"
            )

        return False, "No extractor reported a change"
