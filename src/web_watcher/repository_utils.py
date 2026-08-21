"""Repository utility functions for Web Watcher."""

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Union, Dict, List, Any, Tuple, Callable

from .models import Entity, Event, FetchState, Notification, Signal
from .storage import initialize_schema, open_database
from .event_status import EventStatus
from .event_types import EventType
from .importance import Importance
from .signal_types import SignalType


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _fallback_datetime() -> datetime:
    return datetime.min.replace(tzinfo=timezone.utc)


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _signal_type_from_db(value: str) -> SignalType | str:
    try:
        return SignalType(value)
    except ValueError:
        return value


def _event_type_from_db(value: str) -> EventType | str:
    try:
        return EventType(value)
    except ValueError:
        return value


def _event_status_from_db(value: str) -> EventStatus | str:
    try:
        return EventStatus(value)
    except ValueError:
        return value


def _importance_from_db(value: str) -> Importance | str:
    try:
        return Importance(value)
    except ValueError:
        return value


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


# --- Boundary Normalization Helpers ---

def _normalize_signal_type(val: Union[SignalType, str, None]) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, SignalType):
        return val.value
    if isinstance(val, str):
        cleaned = val.strip().lower()
        for member in SignalType:
            if member.value == cleaned:
                return member.value
        upper_name = val.strip().upper()
        if upper_name in SignalType.__members__:
            return SignalType[upper_name].value
        return cleaned
    return str(val)


def _normalize_event_type(val: Union[EventType, str, None]) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, EventType):
        return val.value
    if isinstance(val, str):
        cleaned = val.strip().lower()
        for member in EventType:
            if member.value == cleaned:
                return member.value
        upper_name = val.strip().upper()
        if upper_name in EventType.__members__:
            return EventType[upper_name].value
        return cleaned
    return str(val)


def _normalize_event_status(val: Union[EventStatus, str, None]) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, EventStatus):
        return val.value
    if isinstance(val, str):
        cleaned = val.strip().lower()
        if cleaned in (EventStatus.OPEN.value, "new"):
            return EventStatus.OPEN.value
        if cleaned in (EventStatus.CLOSED.value, "processed", "discarded"):
            return EventStatus.CLOSED.value
        return cleaned
    return str(val)


def _normalize_importance(val: Union[Importance, str, None]) -> Optional[str]:
    if val is None:
        return None
    try:
        return Importance.from_value(val).value
    except (ValueError, KeyError, AttributeError):
        return str(val).strip().lower()


def _deserialize_signal_type(val: str) -> SignalType:
    try:
        return SignalType(val)
    except ValueError:
        cleaned = val.strip().lower()
        for m in SignalType:
            if m.value == cleaned:
                return m
        return SignalType.CONTENT_CHANGE


def _deserialize_event_type(val: str) -> EventType:
    try:
        return EventType(val)
    except ValueError:
        cleaned = val.strip().lower()
        for m in EventType:
            if m.value == cleaned:
                return m
        return EventType.CONTENT_CHANGE


def _deserialize_event_status(val: str) -> EventStatus:
    try:
        return EventStatus(val)
    except ValueError:
        norm = _normalize_event_status(val)
        return EventStatus(norm) if norm in (EventStatus.OPEN.value, EventStatus.CLOSED.value) else EventStatus.OPEN


def _deserialize_importance(val: str) -> Importance:
    try:
        return Importance.from_value(val)
    except (ValueError, KeyError, AttributeError):
        return Importance.INTERESTING


SCHEMA_VERSION = 3


