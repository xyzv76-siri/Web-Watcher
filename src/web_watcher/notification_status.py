"""Domain vocabulary for Notification delivery status."""

from enum import StrEnum


class NotificationStatus(StrEnum):
    """Lifecycle states for a Notification in the last-mile delivery pipeline."""

    PENDING = "pending"  # Created but not yet dispatched
    DELIVERED = "delivered"  # External channel accepted the delivery
    FAILED = "failed"  # Delivery exhausted max retries
    RETRY_PENDING = "retry_pending"  # Transient failure, will retry with backoff
    SUPPRESSED = "suppressed"  # Silenced by AlertSilencer before dispatch
