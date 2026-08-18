"""Core Web Watcher domain models."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from .event_status import EventStatus
from .event_types import EventType
from .importance import Importance
from .signal_types import SignalType


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
    id: Optional[int]
    event_id: int
    channel: str
    status: str
    created_at: datetime
    sent_at: Optional[datetime] = None
    payload: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class FetchState:
    target_key: str
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_hash: Optional[str] = None
    fetched_at: Optional[datetime] = None
