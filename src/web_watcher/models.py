"""Core Web Watcher domain models."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from .event_status import EventStatus
from .event_types import EventType
from .importance import Importance
from .notification_status import NotificationStatus
from .signal_types import SignalType


class TargetStatus(str, Enum):
    NORMAL = "normal"  # 正常巡检调度中
    BACKOFF = "backoff"  # 发生临时网络故障/429退避中
    COOLDOWN = "cooldown"  # 触发连续失败熔断/长周期隔离
    RECOVERING = "recovering"  # 冷却窗口到期，试探探针状态


@dataclass(frozen=True)
class Entity:
    id: Optional[int]
    canonical_key: str
    name: str
    entity_type: str


@dataclass(frozen=True)
class Signal:
    id: Optional[int]
    entity_id: int
    signal_type: SignalType
    observed_at: datetime
    value: Optional[str] = None
    fingerprint: Optional[str] = None


@dataclass(frozen=True)
class Event:
    id: Optional[int]
    entity_id: int
    event_type: EventType
    status: EventStatus
    importance: Importance
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Notification:
    """Last-mile delivery record for an Event.

    Lifecycle:
    1. ``PENDING`` — created by Pipeline/ScheduledRunner after Policy resolves
       an Event to a notifiable action.
    2. ``SUPPRESSED`` — AlertSilencer decides not to send (dedup / cooldown).
    3. ``RETRY_PENDING`` — transient delivery failure; dispatcher will retry
       with exponential backoff.
    4. ``DELIVERED`` — external channel returned success.
    5. ``FAILED`` — retries exhausted; no further automatic attempts.

    The ``dispatch_token`` / ``dispatch_owner`` / ``dispatch_until`` fields
    implement at-least-once fencing so that a crashed worker cannot cause
    duplicate external side-effects after recovery.
    """

    id: Optional[int]
    event_id: int
    channel: str
    status: NotificationStatus
    created_at: datetime
    sent_at: Optional[datetime] = None
    payload: Optional[Dict[str, Any]] = None
    dispatch_owner: Optional[str] = None
    dispatch_until: Optional[datetime] = None
    dispatch_token: Optional[str] = None


@dataclass(frozen=True)
class FetchState:
    target_key: str
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_hash: Optional[str] = None
    fetched_at: Optional[datetime] = None


@dataclass
class Target:
    id: str
    url: str
    interval: str = "15m"
    status: TargetStatus = TargetStatus.NORMAL
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_hash: Optional[str] = None
    consecutive_failures: int = 0
    last_fetched_at: Optional[datetime] = None
    next_allowed_at: Optional[datetime] = None
    lease_owner: Optional[str] = None
    lease_until: Optional[datetime] = None
    claim_token: Optional[str] = None
    execution_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})
