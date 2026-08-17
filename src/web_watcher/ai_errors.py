"""Explicit error categories for the Phase 10A AI contract.

Every AI failure surfaces as an AIError subtype — never as a partial
or coerced AIJudgment.  Consumers can distinguish provider failures,
timeouts, parse errors, schema errors, and unsupported values without
inspection of message text.
"""


class AIError(Exception):
    """Base class for all Phase 10A AI-related failures."""


class ProviderError(AIError):
    """The AI provider was unavailable or returned a non-parseable error."""


class ProviderTimeoutError(AIError):
    """The AI provider did not respond within the allowed time."""


class InvalidResponseError(AIError):
    """The provider response could not be parsed into structured data."""


class InvalidJSONError(InvalidResponseError):
    """The provider response was not valid JSON."""


class SchemaValidationError(InvalidResponseError):
    """The provider response did not conform to the required schema."""


class UnsupportedValueError(SchemaValidationError):
    """A parsed value was outside the allowed domain (e.g. unknown Importance)."""
