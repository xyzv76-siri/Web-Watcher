"""Core Web Watcher domain models."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


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
    signal_type: str
    observed_at: datetime
    value: Optional[str] = None
    fingerprint: Optional[str] = None


@dataclass(frozen=True)
class Event:
    id: Optional[int]
    entity_id: int
    event_type: str
    status: str
    importance: str
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


@dataclass(frozen=True)
class FetchState:
    target_key: str
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_hash: Optional[str] = None
    fetched_at: Optional[datetime] = None
