"""Persistence operations for Web Watcher core state."""

import json
import sqlite3

from datetime import datetime, timezone
from typing import Optional, Union, Dict, List, Any

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


class Repository:
    def __init__(self, database_path):
        self.connection = open_database(database_path)
        initialize_schema(self.connection)

    def close(self):
        self.connection.close()

    def create_entity(
        self,
        canonical_key: str,
        name: str,
        entity_type: str,
    ) -> Entity:
        now = utc_now().isoformat()

        cursor = self.connection.execute(
            """
            INSERT INTO entities
                (canonical_key, name, entity_type, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (canonical_key, name, entity_type, now),
        )

        self.connection.commit()

        return Entity(
            id=cursor.lastrowid,
            canonical_key=canonical_key,
            name=name,
            entity_type=entity_type,
        )

    def get_entity_by_key(
        self,
        canonical_key: str,
    ) -> Optional[Entity]:
        row = self.connection.execute(
            """
            SELECT id, canonical_key, name, entity_type
            FROM entities
            WHERE canonical_key = ?
            """,
            (canonical_key,),
        ).fetchone()

        if row is None:
            return None

        return Entity(
            id=row[0],
            canonical_key=row[1],
            name=row[2],
            entity_type=row[3],
        )

    def get_or_create_entity(
        self,
        canonical_key: str,
        name: str,
        entity_type: str,
    ) -> Entity:
        """Return the existing entity for *canonical_key*, or create one.

        Repeated calls with the same canonical_key always return the
        same entity — no duplicate rows are ever created.
        """
        existing = self.get_entity_by_key(canonical_key)
        if existing is not None:
            return existing
        return self.create_entity(canonical_key, name, entity_type)

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def create_signal(
        self,
        entity_id: int,
        signal_type: Union[SignalType, str],
        observed_at: datetime,
        value: Optional[str] = None,
        fingerprint: Optional[str] = None,
    ) -> Optional[Signal]:
        """Insert a Signal row.

        The signals table enforces UNIQUE(entity_id, signal_type,
        fingerprint). If the fingerprint already exists for the same
        entity+signal_type, the insert is skipped and None is returned.

        This guarantees a single observation never produces duplicate
        Signal rows.
        """
        now = utc_now_iso()
        obs = (
            observed_at.isoformat()
            if observed_at.tzinfo is None
            else observed_at.isoformat()
        )
        db_type = _normalize_signal_type(signal_type) or SignalType.CONTENT_CHANGE.value

        try:
            cursor = self.connection.execute(
                """
                INSERT INTO signals
                    (entity_id, signal_type, observed_at, value, fingerprint, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (entity_id, db_type, obs, value, fingerprint, now),
            )
            self.connection.commit()
            return Signal(
                id=cursor.lastrowid,
                entity_id=entity_id,
                signal_type=_deserialize_signal_type(db_type),
                observed_at=observed_at,
                value=value,
                fingerprint=fingerprint,
            )
        except sqlite3.IntegrityError:
            # Duplicate fingerprint — do not create another Signal
            self.connection.rollback()
            return None

    def count_signals_for_entity(
        self,
        entity_id: int,
        signal_type: Optional[Union[SignalType, str]] = None,
    ) -> int:
        db_type = _normalize_signal_type(signal_type) if signal_type is not None else None
        if db_type is not None:
            row = self.connection.execute(
                """
                SELECT COUNT(*) as cnt FROM signals
                WHERE entity_id = ? AND signal_type = ?
                """,
                (entity_id, db_type),
            ).fetchone()
        else:
            row = self.connection.execute(
                """
                SELECT COUNT(*) as cnt FROM signals
                WHERE entity_id = ?
                """,
                (entity_id,),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def create_event(
        self,
        entity_id: int,
        event_type: Union[EventType, str],
        status: Union[EventStatus, str] = EventStatus.OPEN,
        importance: Union[Importance, str] = Importance.INTERESTING,
        created_at: Optional[datetime] = None,
    ) -> Event:
        """Create a new Event for the given entity."""
        now = created_at or utc_now()
        now_iso = now.isoformat()
        db_event_type = _normalize_event_type(event_type) or EventType.CONTENT_CHANGE.value
        db_status = _normalize_event_status(status) or EventStatus.OPEN.value
        db_importance = _normalize_importance(importance) or Importance.INTERESTING.value

        cursor = self.connection.execute(
            """
            INSERT INTO events
                (entity_id, event_type, status, importance, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (entity_id, db_event_type, db_status, db_importance, now_iso, now_iso),
        )
        self.connection.commit()

        return Event(
            id=cursor.lastrowid,
            entity_id=entity_id,
            event_type=_deserialize_event_type(db_event_type),
            status=_deserialize_event_status(db_status),
            importance=_deserialize_importance(db_importance),
            created_at=now,
            updated_at=now,
        )

    def get_event(self, event_id: int) -> Optional[Event]:
        """Return the Event with *event_id*, or None."""
        row = self.connection.execute(
            "SELECT id, entity_id, event_type, status, importance, created_at, updated_at FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            return None

        return Event(
            id=row[0],
            entity_id=row[1],
            event_type=_deserialize_event_type(row[2]),
            status=_deserialize_event_status(row[3]),
            importance=_deserialize_importance(row[4]),
            created_at=_parse_iso_datetime(row[5]) or _fallback_datetime(),
            updated_at=_parse_iso_datetime(row[6]) or _fallback_datetime(),
        )

    def update_event(
        self,
        event_id: int,
        status: Optional[Union[EventStatus, str]] = None,
        importance: Optional[Union[Importance, str]] = None,
        updated_at: Optional[datetime] = None,
    ) -> Optional[Event]:
        """Update selected fields of an existing Event.

        Returns the updated Event or None if not found.
        """
        existing = self.get_event(event_id)
        if existing is None:
            return None

        new_status = _normalize_event_status(status) if status is not None else existing.status.value
        new_importance = _normalize_importance(importance) if importance is not None else existing.importance.value
        now = updated_at or utc_now()

        self.connection.execute(
            """
            UPDATE events
            SET status = ?, importance = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_status, new_importance, now.isoformat(), event_id),
        )
        self.connection.commit()

        return Event(
            id=existing.id,
            entity_id=existing.entity_id,
            event_type=existing.event_type,
            status=_deserialize_event_status(new_status),
            importance=_deserialize_importance(new_importance),
            created_at=existing.created_at,
            updated_at=now,
        )

    def attach_signal_to_event(
        self,
        event_id: int,
        signal_id: int,
    ) -> bool:
        """Link a Signal to an Event via the event_signals junction table.

        Returns True on success, False if the pair already exists.
        """
        try:
            self.connection.execute(
                """
                INSERT INTO event_signals (event_id, signal_id)
                VALUES (?, ?)
                """,
                (event_id, signal_id),
            )
            self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            self.connection.rollback()
            return False

    # Backward-compatible alias used by legacy tests/callers
    link_signal_to_event = attach_signal_to_event

    def get_event_signals(self, event_id: int) -> list[Signal]:
        """Return all Signals attached to *event_id*."""
        rows = self.connection.execute(
            """
            SELECT s.id, s.entity_id, s.signal_type, s.observed_at, s.value, s.fingerprint
            FROM signals s
            JOIN event_signals es ON es.signal_id = s.id
            WHERE es.event_id = ?
            ORDER BY s.observed_at ASC
            """,
            (event_id,),
        ).fetchall()

        return [
            Signal(
                id=r["id"],
                entity_id=r["entity_id"],
                signal_type=_signal_type_from_db(r["signal_type"]),
                observed_at=_parse_iso_datetime(r["observed_at"]) or _fallback_datetime(),
                value=r["value"],
                fingerprint=r["fingerprint"],
            )
            for r in rows
        ]

    def find_open_event_for_entity(
        self,
        entity_id: int,
        cutoff: Optional[datetime] = None,
        event_type: Optional[Union[EventType, str]] = None,
    ) -> Optional[Event]:
        """Find the most recent *open* Event for *entity_id* created on or after *cutoff*.

        If *cutoff* is None, no time filter is applied.
        If *event_type* is provided, only events of that type are returned.
        Returns the Event with the latest created_at among matches, or None.
        """
        db_event_type = _normalize_event_type(event_type) if event_type is not None else None
        if cutoff is not None:
            cutoff_str = cutoff.isoformat()
            if db_event_type is not None:
                row = self.connection.execute(
                    """
                    SELECT id, entity_id, event_type, status, importance, created_at, updated_at
                    FROM events
                    WHERE entity_id = ? AND status = ? AND created_at >= ? AND event_type = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (entity_id, EventStatus.OPEN.value, cutoff_str, db_event_type),
                ).fetchone()
            else:
                row = self.connection.execute(
                    """
                    SELECT id, entity_id, event_type, status, importance, created_at, updated_at
                    FROM events
                    WHERE entity_id = ? AND status = ? AND created_at >= ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (entity_id, EventStatus.OPEN.value, cutoff_str),
                ).fetchone()
        else:
            if db_event_type is not None:
                row = self.connection.execute(
                    """
                    SELECT id, entity_id, event_type, status, importance, created_at, updated_at
                    FROM events
                    WHERE entity_id = ? AND status = ? AND event_type = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (entity_id, EventStatus.OPEN.value, db_event_type),
                ).fetchone()
            else:
                row = self.connection.execute(
                    """
                    SELECT id, entity_id, event_type, status, importance, created_at, updated_at
                    FROM events
                    WHERE entity_id = ? AND status = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (entity_id, EventStatus.OPEN.value),
                ).fetchone()

        if row is None:
            return None

        return Event(
            id=row[0],
            entity_id=row[1],
            event_type=_deserialize_event_type(row[2]),
            status=_deserialize_event_status(row[3]),
            importance=_deserialize_importance(row[4]),
            created_at=_parse_iso_datetime(row[5]) or _fallback_datetime(),
            updated_at=_parse_iso_datetime(row[6]) or _fallback_datetime(),
        )

    # ------------------------------------------------------------------
    # Fetch state
    # ------------------------------------------------------------------

    def get_fetch_state(
        self,
        target_key: str,
    ) -> Optional[FetchState]:
        """Return the FetchState for *target_key*, or None if absent."""
        row = self.connection.execute(
            """
            SELECT target_key, etag, last_modified, content_hash, fetched_at
            FROM fetch_state
            WHERE target_key = ?
            """,
            (target_key,),
        ).fetchone()

        if row is None:
            return None

        return FetchState(
            target_key=row["target_key"],
            etag=row["etag"],
            last_modified=row["last_modified"],
            content_hash=row["content_hash"],
            fetched_at=_parse_iso_datetime(row["fetched_at"]),
        )

    def upsert_fetch_state(self, state: FetchState) -> FetchState:
        """Insert or replace the FetchState for a target key.

        Uses a single REPLACE INTO (SQLite UPSERT) so repeated calls
        are idempotent and never create duplicate rows.
        """
        self.connection.execute(
            """
            INSERT OR REPLACE INTO fetch_state
                (target_key, etag, last_modified, content_hash, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                state.target_key,
                state.etag,
                state.last_modified,
                state.content_hash,
                _serialize_datetime(state.fetched_at),
            ),
        )
        self.connection.commit()
        return state

    def save_investigation_result(
        self,
        investigation_id: str,
        event_id: str,
        task_type: str,
        status: str,
        summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        evidence_items: Optional[List[Dict[str, Any]]] = None,
        created_at: Optional[str] = None,
    ) -> None:
        ts = created_at or datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata or {})
        with self.connection:
            self.connection.execute(
                "INSERT INTO investigation_results (id, event_id, task_type, status, summary, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (investigation_id, event_id, str(task_type), str(status), summary, meta_json, ts),
            )
            if evidence_items:
                for idx, item in enumerate(evidence_items):
                    ev_id = f"{investigation_id}_ev_{idx}"
                    ev_type = str(item.get("evidence_type", "generic"))
                    ev_payload = json.dumps(item.get("payload", {}))
                    self.connection.execute(
                        "INSERT INTO investigation_evidence (id, investigation_id, evidence_type, payload, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (ev_id, investigation_id, ev_type, ev_payload, ts),
                    )

    def get_investigation_result(self, investigation_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.connection.execute("SELECT * FROM investigation_results WHERE id = ?", (investigation_id,))
        row = cursor.fetchone()
        if not row:
            return None
        ev_cursor = self.connection.execute(
            "SELECT * FROM investigation_evidence WHERE investigation_id = ? ORDER BY id ASC",
            (investigation_id,),
        )
        evidence = [
            {
                "id": ev["id"],
                "evidence_type": ev["evidence_type"],
                "payload": json.loads(ev["payload"]),
                "created_at": ev["created_at"],
            }
            for ev in ev_cursor.fetchall()
        ]
        return {
            "id": row["id"],
            "event_id": row["event_id"],
            "task_type": row["task_type"],
            "status": row["status"],
            "summary": row["summary"],
            "metadata": json.loads(row["metadata"]),
            "created_at": row["created_at"],
            "evidence": evidence,
        }

    def get_investigation_result_by_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.connection.execute(
            "SELECT id FROM investigation_results WHERE event_id = ? ORDER BY created_at DESC LIMIT 1",
            (event_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return self.get_investigation_result(row["id"])

    def create_notification(
        self,
        event_id: int,
        channel: str,
        status: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Optional["Notification"]:
        """Persist a notification and return the Notification model.

        Returns the existing notification if one already exists for the same event_id and channel.
        """
        now = utc_now_iso()
        try:
            cursor = self.connection.execute(
                """
                INSERT INTO notifications (event_id, channel, status, created_at, sent_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    channel,
                    status,
                    now,
                    None,
                    json.dumps(payload, default=str) if payload is not None else None,
                ),
            )
            self.connection.commit()
            return Notification(
                id=cursor.lastrowid,
                event_id=event_id,
                channel=channel,
                status=status,
                created_at=now,
                sent_at=None,
                payload=payload,
            )
        except sqlite3.IntegrityError:
            self.connection.rollback()
            cursor = self.connection.execute(
                "SELECT id, event_id, channel, status, created_at, sent_at, payload FROM notifications WHERE event_id = ? AND channel = ?",
                (event_id, channel),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return Notification(
                id=row["id"],
                event_id=row["event_id"],
                channel=row["channel"],
                status=row["status"],
                created_at=row["created_at"],
                sent_at=row["sent_at"],
                payload=json.loads(row["payload"]) if row["payload"] else None,
            )

    def get_notification(self, notification_id: int) -> Optional[Notification]:
        cursor = self.connection.execute(
            "SELECT id, event_id, channel, status, created_at, sent_at, payload FROM notifications WHERE id = ?",
            (notification_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return Notification(
            id=row["id"],
            event_id=row["event_id"],
            channel=row["channel"],
            status=row["status"],
            created_at=row["created_at"],
            sent_at=row["sent_at"],
            payload=json.loads(row["payload"]) if row["payload"] else None,
        )

    def list_notifications_for_event(self, event_id: int) -> list[Notification]:
        cursor = self.connection.execute(
            "SELECT id, event_id, channel, status, created_at, sent_at, payload FROM notifications WHERE event_id = ? ORDER BY created_at DESC",
            (event_id,),
        )
        results = []
        for row in cursor.fetchall():
            results.append(
                Notification(
                    id=row["id"],
                    event_id=row["event_id"],
                    channel=row["channel"],
                    status=row["status"],
                    created_at=row["created_at"],
                    sent_at=row["sent_at"],
                    payload=json.loads(row["payload"]) if row["payload"] else None,
                )
            )
        return results

    def get_pending_notifications(self, limit: int = 10) -> list[Notification]:
        cursor = self.connection.execute(
            "SELECT id, event_id, channel, status, created_at, sent_at, payload FROM notifications WHERE status IN ('pending', 'retry_pending') ORDER BY created_at ASC LIMIT ?",
            (limit,),
        )
        results = []
        for row in cursor.fetchall():
            results.append(
                Notification(
                    id=row["id"],
                    event_id=row["event_id"],
                    channel=row["channel"],
                    status=row["status"],
                    created_at=row["created_at"],
                    sent_at=row["sent_at"],
                    payload=json.loads(row["payload"]) if row["payload"] else None,
                )
            )
        return results

    def update_notification_status(
        self,
        notification_id: int,
        status: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        updates = ["status = ?"]
        params: list[Any] = [str(status)]
        if payload is not None:
            updates.append("payload = ?")
            params.append(json.dumps(payload))
        params.append(notification_id)
        with self.connection:
            self.connection.execute(f"UPDATE notifications SET {', '.join(updates)} WHERE id = ?", params)

