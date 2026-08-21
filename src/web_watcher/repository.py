"""Persistence operations for Web Watcher core state."""

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

logger = logging.getLogger(__name__)


@dataclass
class Migration:
    version: int
    name: str
    up: Callable[["Repository"], None]
    down: Optional[Callable[["Repository"], None]] = None
    checksum: Optional[str] = None

    def compute_checksum(self) -> str:
        return hashlib.sha256(self.name.encode("utf-8")).hexdigest()[:16]


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


class Repository:
    def __init__(self, database_path):
        self.connection = open_database(database_path)
        initialize_schema(self.connection)
        self._apply_migrations()

    def close(self):
        self.connection.close()

    def _get_schema_version(self) -> int:
        cursor = self.connection.execute("SELECT MAX(version) as v FROM schema_version")
        row = cursor.fetchone()
        return int(row["v"]) if row and row["v"] is not None else 0

    def _set_schema_version(self, version: int) -> None:
        now_iso = utc_now_iso()
        self.connection.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (version, now_iso),
        )
        self.connection.commit()

    @classmethod
    def migration_registry(cls) -> Dict[int, Migration]:
        migrations = {
            1: Migration(
                version=1,
                name="init_notification_table",
                up=lambda repo: repo._init_notification_table(),
                down=lambda repo: repo.connection.execute("DROP TABLE IF EXISTS notifications"),
            ),
            2: Migration(
                version=2,
                name="init_host_rate_limit_table",
                up=lambda repo: repo._init_host_rate_limit_table(),
                down=lambda repo: repo.connection.execute("DROP TABLE IF EXISTS host_rate_limits"),
            ),
            3: Migration(
                version=3,
                name="init_host_rate_limit_claim_until",
                up=lambda repo: repo._init_host_rate_limit_claim_until(),
                down=lambda repo: repo.connection.execute("ALTER TABLE host_rate_limits DROP COLUMN IF EXISTS claim_until"),
            ),
        }
        for migration in migrations.values():
            migration.checksum = migration.compute_checksum()
        return migrations

    def _apply_migrations(self) -> None:
        current = self._get_schema_version()
        registry = self.migration_registry()
        for next_version in range(current + 1, SCHEMA_VERSION + 1):
            migration = registry.get(next_version)
            if migration is None:
                raise RuntimeError(f"No migration registered for version {next_version}")
            migration.checksum = migration.compute_checksum()
            logger.info("Applying migration v%d: %s", next_version, migration.name)
            migration.up(self)
            self._set_schema_version(next_version)
            logger.info("Applied migration v%d: %s", next_version, migration.name)

    def _apply_migration(self, version: int) -> None:
        # Backward compatibility: delegate to registry if available.
        registry = self.migration_registry()
        migration = registry.get(version)
        if migration is None:
            raise RuntimeError(f"No migration registered for version {version}")
        migration.up(self)

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
                INSERT INTO notifications (event_id, channel, status, created_at, sent_at, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    channel,
                    status,
                    now,
                    None,
                    json.dumps(payload, default=str) if payload is not None else None,
                    now,
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
                "SELECT id, event_id, channel, status, created_at, sent_at, payload, dispatch_owner, dispatch_until, dispatch_token FROM notifications WHERE event_id = ? AND channel = ?",
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
                dispatch_owner=row["dispatch_owner"],
                dispatch_until=_parse_iso_datetime(row["dispatch_until"]),
                dispatch_token=row["dispatch_token"],
            )

    def get_notification(self, notification_id: int) -> Optional[Notification]:
        cursor = self.connection.execute(
            "SELECT id, event_id, channel, status, created_at, sent_at, payload, dispatch_owner, dispatch_until, dispatch_token FROM notifications WHERE id = ?",
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
            dispatch_owner=row["dispatch_owner"],
            dispatch_until=_parse_iso_datetime(row["dispatch_until"]),
            dispatch_token=row["dispatch_token"],
        )

    def list_notifications_for_event(self, event_id: int) -> list[Notification]:
        cursor = self.connection.execute(
            "SELECT id, event_id, channel, status, created_at, sent_at, payload, dispatch_owner, dispatch_until, dispatch_token FROM notifications WHERE event_id = ? ORDER BY created_at DESC",
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
                    dispatch_owner=row["dispatch_owner"],
                    dispatch_until=_parse_iso_datetime(row["dispatch_until"]),
                    dispatch_token=row["dispatch_token"],
                )
            )
        return results

    def _init_notification_table(self):
        cols = [c[1] for c in self.connection.execute("PRAGMA table_info(notifications)").fetchall()]
        for col_name, col_type in [
            ("dispatch_owner", "TEXT"),
            ("dispatch_until", "TEXT"),
            ("dispatch_token", "TEXT"),
            ("updated_at", "TEXT"),
        ]:
            if col_name not in cols:
                self.connection.execute(f"ALTER TABLE notifications ADD COLUMN {col_name} {col_type}")
        self.connection.commit()

    def get_pending_notifications(self, limit: int = 10) -> list[Notification]:
        now_iso = utc_now_iso()
        cursor = self.connection.execute(
            "SELECT id, event_id, channel, status, created_at, sent_at, payload, dispatch_owner, dispatch_until, dispatch_token FROM notifications WHERE status IN ('pending', 'retry_pending') AND (dispatch_until IS NULL OR dispatch_until < ?) ORDER BY created_at ASC LIMIT ?",
            (now_iso, limit),
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
                    dispatch_owner=row["dispatch_owner"],
                    dispatch_until=_parse_iso_datetime(row["dispatch_until"]),
                    dispatch_token=row["dispatch_token"],
                )
            )
        return results

    def claim_notifications(
        self,
        worker_id: str,
        limit: int = 10,
        lease_duration_sec: float = 300.0,
        now: Optional[datetime] = None,
    ) -> list[Notification]:
        """
        Atomic claim for distributed notification dispatch.
        Selects pending/retry_pending notifications and assigns a short-lived lease with a unique dispatch token.
        """
        import uuid
        from datetime import timedelta, timezone

        self._init_notification_table()

        if now is None:
            now_dt = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now_dt = now.replace(tzinfo=timezone.utc)
        else:
            now_dt = now.astimezone(timezone.utc)

        dispatch_until_dt = now_dt + timedelta(seconds=lease_duration_sec)
        now_iso = now_dt.isoformat()
        dispatch_until_iso = dispatch_until_dt.isoformat()

        claimed: list[Notification] = []

        with self.connection:
            cursor = self.connection.cursor()
            rows = cursor.execute("""
                SELECT id, event_id, channel, status, created_at, sent_at, payload
                FROM notifications
                WHERE status IN ('pending', 'retry_pending')
                  AND (dispatch_until IS NULL OR dispatch_until < ?)
                ORDER BY created_at ASC
                LIMIT ?
            """, (now_iso, limit)).fetchall()

            for r in rows:
                notification_id = r["id"]
                dispatch_token = str(uuid.uuid4())
                cursor.execute("""
                    UPDATE notifications
                    SET dispatch_owner = ?, dispatch_until = ?, dispatch_token = ?
                    WHERE id = ? AND (dispatch_until IS NULL OR dispatch_until < ?)
                """, (worker_id, dispatch_until_iso, dispatch_token, notification_id, now_iso))

                if cursor.rowcount:
                    claimed.append(
                        Notification(
                            id=notification_id,
                            event_id=r["event_id"],
                            channel=r["channel"],
                            status=r["status"],
                            created_at=r["created_at"],
                            sent_at=r["sent_at"],
                            payload=json.loads(r["payload"]) if r["payload"] else None,
                            dispatch_owner=worker_id,
                            dispatch_until=dispatch_until_dt,
                            dispatch_token=dispatch_token,
                        )
                    )

        return claimed

    def release_notification_dispatch(
        self,
        notification_id: int,
        dispatch_token: str,
        now: Optional[datetime] = None,
    ) -> bool:
        """
        Release a claimed notification's dispatch lease if the token matches.
        Returns True if the row was updated, False if the lease was lost.
        """
        if now is None:
            now_iso = utc_now_iso()
        else:
            now_iso = _serialize_datetime(now)

        with self.connection:
            cursor = self.connection.execute("""
                UPDATE notifications
                SET dispatch_owner = NULL, dispatch_until = NULL, dispatch_token = NULL, updated_at = ?
                WHERE id = ? AND dispatch_token = ?
            """, (now_iso, notification_id, dispatch_token))
            return cursor.rowcount > 0

    def finalize_notification_dispatch(
        self,
        notification_id: int,
        dispatch_token: str,
        status: str,
        sent_at: Optional[datetime] = None,
        payload: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> bool:
        """
        Fenced finalization: only succeeds if the dispatch_token matches.
        Clears dispatch fields on successful finalization.
        Returns True if the row was updated, False if the lease was lost.
        """
        if now is None:
            now_iso = utc_now_iso()
        else:
            now_iso = _serialize_datetime(now)

        sent_at_iso = _serialize_datetime(sent_at)
        updates = ["status = ?", "sent_at = ?", "dispatch_owner = NULL", "dispatch_until = NULL", "dispatch_token = NULL", "updated_at = ?"]
        params: list[Any] = [str(status), sent_at_iso, now_iso]

        if payload is not None:
            updates.append("payload = ?")
            params.append(json.dumps(payload))

        params.extend([notification_id, dispatch_token])

        with self.connection:
            cursor = self.connection.execute(f"""
                UPDATE notifications
                SET {', '.join(updates)}
                WHERE id = ? AND dispatch_token = ?
            """, params)
            return cursor.rowcount > 0

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

    def delete_old_events(self, cutoff: datetime) -> int:
        cursor = self.connection.execute(
            "DELETE FROM events WHERE created_at < ?",
            (_serialize_datetime(cutoff),),
        )
        self.connection.commit()
        return cursor.rowcount

    def delete_old_notifications(self, cutoff: datetime) -> int:
        cursor = self.connection.execute(
            "DELETE FROM notifications WHERE created_at < ?",
            (_serialize_datetime(cutoff),),
        )
        self.connection.commit()
        return cursor.rowcount

    def list_events(
        self,
        entity_id: Optional[Union[int, List[int]]] = None,
        event_type: Optional[Union[EventType, str, List[Union[EventType, str]]]] = None,
        importance: Optional[Union[Importance, str, List[Union[Importance, str]]]] = None,
        status: Optional[Union[EventStatus, str, List[Union[EventStatus, str]]]] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> list[Event]:
        """Return events matching the provided filters."""
        clauses: list[str] = []
        params: list[Any] = []

        if entity_id is not None:
            if isinstance(entity_id, list):
                if entity_id:
                    clauses.append(f"entity_id IN ({', '.join('?' * len(entity_id))})")
                    params.extend(entity_id)
            else:
                clauses.append("entity_id = ?")
                params.append(entity_id)
        if event_type is not None:
            if isinstance(event_type, list):
                if event_type:
                    clauses.append(f"event_type IN ({', '.join('?' * len(event_type))})")
                    params.extend(str(t) for t in event_type)
            else:
                clauses.append("event_type = ?")
                params.append(str(event_type))
        if importance is not None:
            if isinstance(importance, list):
                if importance:
                    clauses.append(f"importance IN ({', '.join('?' * len(importance))})")
                    params.extend(str(i) for i in importance)
            else:
                clauses.append("importance = ?")
                params.append(str(importance))
        if status is not None:
            if isinstance(status, list):
                if status:
                    clauses.append(f"status IN ({', '.join('?' * len(status))})")
                    params.extend(str(s) for s in status)
            else:
                clauses.append("status = ?")
                params.append(str(status))
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(_serialize_datetime(since))

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT id, entity_id, event_type, status, importance, created_at, updated_at FROM events {where} ORDER BY created_at DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"

        rows = self.connection.execute(sql, params).fetchall()
        return [
            Event(
                id=r["id"],
                entity_id=r["entity_id"],
                event_type=_deserialize_event_type(r["event_type"]),
                status=_deserialize_event_status(r["status"]),
                importance=_deserialize_importance(r["importance"]),
                created_at=_parse_iso_datetime(r["created_at"]) or _fallback_datetime(),
                updated_at=_parse_iso_datetime(r["updated_at"]) or _fallback_datetime(),
            )
            for r in rows
        ]

    def list_notifications(
        self,
        event_id: Optional[Union[int, List[int]]] = None,
        channel: Optional[Union[str, List[str]]] = None,
        status: Optional[Union[str, List[str]]] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> list[Notification]:
        """Return notifications matching the provided filters."""
        clauses: list[str] = []
        params: list[Any] = []

        if event_id is not None:
            if isinstance(event_id, list):
                if event_id:
                    clauses.append(f"event_id IN ({', '.join('?' * len(event_id))})")
                    params.extend(event_id)
            else:
                clauses.append("event_id = ?")
                params.append(event_id)
        if channel is not None:
            if isinstance(channel, list):
                if channel:
                    clauses.append(f"channel IN ({', '.join('?' * len(channel))})")
                    params.extend(channel)
            else:
                clauses.append("channel = ?")
                params.append(channel)
        if status is not None:
            if isinstance(status, list):
                if status:
                    clauses.append(f"status IN ({', '.join('?' * len(status))})")
                    params.extend(status)
            else:
                clauses.append("status = ?")
                params.append(status)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(_serialize_datetime(since))

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT id, event_id, channel, status, created_at, sent_at, payload, dispatch_owner, dispatch_until, dispatch_token FROM notifications {where} ORDER BY created_at DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"

        rows = self.connection.execute(sql, params).fetchall()
        return [
            Notification(
                id=r["id"],
                event_id=r["event_id"],
                channel=r["channel"],
                status=r["status"],
                created_at=_parse_iso_datetime(r["created_at"]) or _fallback_datetime(),
                sent_at=_parse_iso_datetime(r["sent_at"]),
                payload=json.loads(r["payload"] or "{}"),
                dispatch_owner=r["dispatch_owner"],
                dispatch_until=_parse_iso_datetime(r["dispatch_until"]),
                dispatch_token=r["dispatch_token"],
            )
            for r in rows
        ]

    def _init_target_table(self):
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS targets (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                interval TEXT NOT NULL DEFAULT '15m',
                status TEXT NOT NULL DEFAULT 'normal',
                etag TEXT,
                last_modified TEXT,
                content_hash TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                last_fetched_at TEXT,
                next_allowed_at TEXT,
                lease_owner TEXT,
                lease_until TEXT,
                claim_token TEXT,
                execution_id TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                tags TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # Incrementally add columns for existing databases
        cols = [c[1] for c in self.connection.execute("PRAGMA table_info(targets)").fetchall()]
        for col_name, col_type in [
            ("lease_owner", "TEXT"),
            ("lease_until", "TEXT"),
            ("claim_token", "TEXT"),
            ("execution_id", "TEXT"),
            ("tags", "TEXT"),
        ]:
            if col_name not in cols:
                self.connection.execute(f"ALTER TABLE targets ADD COLUMN {col_name} {col_type}")
        self.connection.commit()

    def _init_signal_tables(self):
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                value TEXT,
                fingerprint TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                importance TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS event_signals (
                event_id INTEGER NOT NULL,
                signal_id INTEGER NOT NULL,
                PRIMARY KEY (event_id, signal_id)
            )
        """)
        self.connection.commit()

    def _init_host_rate_limit_table(self):
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS host_rate_limits (
                host TEXT PRIMARY KEY,
                next_allowed_at TEXT,
                claim_token TEXT,
                claimed_at TEXT,
                claim_until TEXT
            )
        """)
        self.connection.commit()

    def _init_host_rate_limit_claim_until(self):
        """Migration v3: add claim_until column to host_rate_limits."""
        try:
            self.connection.execute("ALTER TABLE host_rate_limits ADD COLUMN claim_until TEXT")
            self.connection.commit()
        except sqlite3.OperationalError:
            # Column already exists
            pass

    def acquire_host_request(
        self,
        host: str,
        now: datetime,
        lease_seconds: float = 300.0,
    ) -> Tuple[bool, Optional[str], Optional[float]]:
        """
        Atomically acquire permission to send an HTTP request to a host.

        Succeeds only when:
          - no active claim exists for the host, or the existing claim has expired
          - next_allowed_at is NULL or has passed

        Returns (allowed, claim_token, wait_seconds).
        """
        import uuid
        from datetime import timedelta, timezone

        if not host:
            return True, None, None

        now_dt = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        now_iso = now_dt.isoformat()
        claim_token = str(uuid.uuid4())
        claim_until = (now_dt + timedelta(seconds=lease_seconds)).isoformat()

        with self.connection:
            cursor = self.connection.cursor()
            cursor.execute("""
                UPDATE host_rate_limits
                SET claim_token = ?, claimed_at = ?, claim_until = ?
                WHERE host = ?
                  AND (claim_token IS NULL OR claim_until <= ?)
                  AND (next_allowed_at IS NULL OR next_allowed_at <= ?)
            """, (claim_token, now_iso, claim_until, host, now_iso, now_iso))

            if cursor.rowcount:
                return True, claim_token, None

            # Determine why acquire failed: active claim or rate-limit window
            row = cursor.execute("""
                SELECT next_allowed_at, claim_token, claim_until
                FROM host_rate_limits
                WHERE host = ?
            """, (host,)).fetchone()

            if row:
                # If there is an active claim, the caller must wait for the claim lease
                if row["claim_token"] is not None and row["claim_until"] is not None:
                    try:
                        claim_until_dt = datetime.fromisoformat(row["claim_until"])
                        remaining = max(0.0, (claim_until_dt - now_dt).total_seconds())
                        return False, None, remaining
                    except ValueError:
                        pass

                if row["next_allowed_at"] is not None:
                    try:
                        next_allowed = datetime.fromisoformat(row["next_allowed_at"])
                        remaining = max(0.0, (next_allowed - now_dt).total_seconds())
                        return False, None, remaining
                    except ValueError:
                        pass

            # No row exists; insert a new unclaimed row
            cursor.execute("""
                INSERT INTO host_rate_limits (host, claim_token, claimed_at, claim_until)
                VALUES (?, ?, ?, ?)
            """, (host, claim_token, now_iso, claim_until))
            return True, claim_token, None

    def release_host_request(self, host: str, claim_token: str) -> bool:
        """Release a host request claim only if the token matches."""
        with self.connection:
            cursor = self.connection.execute("""
                UPDATE host_rate_limits
                SET claim_token = NULL, claimed_at = NULL, claim_until = NULL
                WHERE host = ? AND claim_token = ?
            """, (host, claim_token))
            return cursor.rowcount > 0

    def update_host_next_allowed(self, host: str, next_allowed_at: Optional[datetime]) -> None:
        """Update the next allowed time for a host and clear the active claim."""
        if not host:
            return
        now_iso = utc_now_iso()
        next_allowed_iso = _serialize_datetime(next_allowed_at)
        with self.connection:
            self.connection.execute("""
                INSERT INTO host_rate_limits (host, next_allowed_at, claimed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(host) DO UPDATE SET
                    next_allowed_at = excluded.next_allowed_at,
                    claimed_at = excluded.claimed_at,
                    claim_token = NULL,
                    claim_until = NULL
            """, (host, next_allowed_iso, now_iso))

    def reap_stale_claims(self, older_than: Optional[datetime] = None) -> int:
        """Clear expired claims. Returns number of rows updated."""
        if older_than is None:
            older_than = utc_now()
        cutoff_iso = older_than.isoformat()
        with self.connection:
            cursor = self.connection.execute("""
                UPDATE host_rate_limits
                SET claim_token = NULL, claimed_at = NULL, claim_until = NULL
                WHERE claim_until IS NOT NULL AND claim_until <= ?
            """, (cutoff_iso,))
            return cursor.rowcount

    def renew_host_request(self, host: str, claim_token: str, now: datetime, lease_seconds: float = 300.0) -> bool:
        """Renew an existing claim's lease. Returns True if the claim was renewed."""
        now_dt = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        now_iso = now_dt.isoformat()
        new_until = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        with self.connection:
            cursor = self.connection.execute("""
                UPDATE host_rate_limits
                SET claimed_at = ?, claim_until = ?
                WHERE host = ? AND claim_token = ?
            """, (now_iso, new_until, host, claim_token))
            return cursor.rowcount > 0

    def save_target(self, target: Any) -> None:
        self._init_target_table()
        now_iso = utc_now_iso()
        status_val = target.status.value if hasattr(target.status, "value") else str(target.status)
        last_fetched_iso = _serialize_datetime(target.last_fetched_at)
        next_allowed_iso = _serialize_datetime(target.next_allowed_at)
        lease_owner = getattr(target, "lease_owner", None)
        lease_until_iso = _serialize_datetime(getattr(target, "lease_until", None))
        claim_token = getattr(target, "claim_token", None)
        meta_json = json.dumps(target.metadata or {})
        tags_json = json.dumps(list(target.tags or []))

        self.connection.execute("""
            INSERT INTO targets (
                id, url, interval, status, etag, last_modified, content_hash,
                consecutive_failures, last_fetched_at, next_allowed_at,
                lease_owner, lease_until, claim_token,
                metadata_json, tags, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                url = excluded.url,
                interval = excluded.interval,
                status = excluded.status,
                etag = excluded.etag,
                last_modified = excluded.last_modified,
                content_hash = excluded.content_hash,
                consecutive_failures = excluded.consecutive_failures,
                last_fetched_at = excluded.last_fetched_at,
                next_allowed_at = excluded.next_allowed_at,
                lease_owner = excluded.lease_owner,
                lease_until = excluded.lease_until,
                claim_token = excluded.claim_token,
                metadata_json = excluded.metadata_json,
                tags = excluded.tags,
                updated_at = excluded.updated_at
        """, (
            target.id, target.url, target.interval, status_val, target.etag,
            target.last_modified, target.content_hash, target.consecutive_failures,
            last_fetched_iso, next_allowed_iso,
            lease_owner, lease_until_iso, claim_token,
            meta_json, tags_json, now_iso, now_iso
        ))
        self.connection.commit()

    def get_target(self, target_id: str) -> Optional[Any]:
        self._init_target_table()
        from web_watcher.models import Target, TargetStatus
        row = self.connection.execute("SELECT * FROM targets WHERE id = ?", (target_id,)).fetchone()
        if not row:
            return None
        return Target(
            id=row["id"],
            url=row["url"],
            interval=row["interval"],
            status=TargetStatus(row["status"]),
            etag=row["etag"],
            last_modified=row["last_modified"],
            content_hash=row["content_hash"],
            consecutive_failures=row["consecutive_failures"],
            last_fetched_at=_parse_iso_datetime(row["last_fetched_at"]),
            next_allowed_at=_parse_iso_datetime(row["next_allowed_at"]),
            lease_owner=row["lease_owner"],
            lease_until=_parse_iso_datetime(row["lease_until"]),
            claim_token=row["claim_token"],
            execution_id=row["execution_id"],
            metadata=json.loads(row["metadata_json"] or "{}"),
            tags=json.loads(row["tags"] or "[]"),
        )

    def list_targets(
        self,
        tags: Optional[List[str]] = None,
        require_all: bool = False,
    ) -> List[Any]:
        self._init_target_table()
        from web_watcher.models import Target, TargetStatus

        if tags:
            rows = self.connection.execute("SELECT * FROM targets ORDER BY id ASC").fetchall()
            matched = []
            for r in rows:
                target_tags = json.loads(r["tags"] or "[]")
                if require_all:
                    if all(t in target_tags for t in tags):
                        matched.append(r)
                else:
                    if any(t in target_tags for t in tags):
                        matched.append(r)
            rows = matched
        else:
            rows = self.connection.execute("SELECT * FROM targets ORDER BY id ASC").fetchall()

        return [
            Target(
                id=r["id"],
                url=r["url"],
                interval=r["interval"],
                status=TargetStatus(r["status"]),
                etag=r["etag"],
                last_modified=r["last_modified"],
                content_hash=r["content_hash"],
                consecutive_failures=r["consecutive_failures"],
                last_fetched_at=_parse_iso_datetime(r["last_fetched_at"]),
                next_allowed_at=_parse_iso_datetime(r["next_allowed_at"]),
                lease_owner=r["lease_owner"],
                lease_until=_parse_iso_datetime(r["lease_until"]),
                claim_token=r["claim_token"],
                execution_id=r["execution_id"],
                metadata=json.loads(r["metadata_json"] or "{}"),
                tags=json.loads(r["tags"] or "[]"),
            )
            for r in rows
        ]

    def update_target_status(
        self,
        target_id: str,
        status: Any,
        consecutive_failures: Optional[int] = None,
        next_allowed_at: Optional[datetime] = None,
    ) -> None:
        self._init_target_table()
        status_val = status.value if hasattr(status, "value") else str(status)
        now_iso = utc_now_iso()
        next_iso = _serialize_datetime(next_allowed_at)

        if consecutive_failures is not None:
            self.connection.execute("""
                UPDATE targets SET
                    status = ?,
                    consecutive_failures = ?,
                    next_allowed_at = ?,
                    updated_at = ?
                WHERE id = ?
            """, (status_val, consecutive_failures, next_iso, now_iso, target_id))
        else:
            self.connection.execute("""
                UPDATE targets SET
                    status = ?,
                    next_allowed_at = ?,
                    updated_at = ?
                WHERE id = ?
            """, (status_val, next_iso, now_iso, target_id))
        self.connection.commit()

    def list_schedulable_targets(self, now: Optional[datetime] = None) -> List[Any]:
        self._init_target_table()
        from web_watcher.models import Target, TargetStatus
        now = now or utc_now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        targets = self.list_targets()
        schedulable = []
        for t in targets:
            # 1. 如果有明确的冷却/退避倒计时，未到期则不可调度
            if t.next_allowed_at and now < t.next_allowed_at:
                continue
            # 2. 如果处于 COOLDOWN 且倒计时已到期，自动迁移至 RECOVERING (允许单次探针)
            if t.status == TargetStatus.COOLDOWN:
                t.status = TargetStatus.RECOVERING
                self.update_target_status(t.id, TargetStatus.RECOVERING)
            schedulable.append(t)
        return schedulable

    def claim_targets(
        self,
        worker_id: str,
        limit: int = 10,
        lease_duration_sec: float = 300.0,
        now: Optional[datetime] = None,
    ) -> List[Any]:
        """
        Atomic claim for distributed workers.
        Selects schedulable targets and assigns a short-lived lease with a unique claim token.
        """
        import uuid
        from datetime import timedelta, timezone

        self._init_target_table()
        from web_watcher.models import Target, TargetStatus

        if now is None:
            now_dt = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now_dt = now.replace(tzinfo=timezone.utc)
        else:
            now_dt = now.astimezone(timezone.utc)

        lease_until_dt = now_dt + timedelta(seconds=lease_duration_sec)
        now_iso = now_dt.isoformat()
        lease_until_iso = lease_until_dt.isoformat()

        claimed: List[Target] = []

        with self.connection:
            cursor = self.connection.cursor()
            rows = cursor.execute("""
                SELECT id, url, interval, status, etag, last_modified, content_hash,
                       consecutive_failures, last_fetched_at, next_allowed_at, metadata_json
                FROM targets
                WHERE (status IN ('normal', 'recovering', 'cooldown'))
                  AND (next_allowed_at IS NULL OR next_allowed_at <= ?)
                  AND (lease_until IS NULL OR lease_until < ?)
                ORDER BY next_allowed_at ASC NULLS FIRST
                LIMIT ?
            """, (now_iso, now_iso, limit)).fetchall()

            for r in rows:
                target_id = r["id"]
                new_status = (
                    TargetStatus.RECOVERING.value
                    if r["status"] == TargetStatus.COOLDOWN.value
                    else r["status"]
                )
                claim_token = str(uuid.uuid4())
                execution_id = str(uuid.uuid4())
                cursor.execute("""
                    UPDATE targets
                    SET status = ?, lease_owner = ?, lease_until = ?, claim_token = ?, execution_id = ?, updated_at = ?
                    WHERE id = ? AND (lease_until IS NULL OR lease_until < ?)
                """, (new_status, worker_id, lease_until_iso, claim_token, execution_id, now_iso, target_id, now_iso))

                claimed.append(Target(
                    id=target_id,
                    url=r["url"],
                    interval=r["interval"],
                    status=TargetStatus(new_status),
                    etag=r["etag"],
                    last_modified=r["last_modified"],
                    content_hash=r["content_hash"],
                    consecutive_failures=r["consecutive_failures"],
                    last_fetched_at=_parse_iso_datetime(r["last_fetched_at"]),
                    next_allowed_at=_parse_iso_datetime(r["next_allowed_at"]),
                    lease_owner=worker_id,
                    lease_until=lease_until_dt,
                    claim_token=claim_token,
                    execution_id=execution_id,
                    metadata=json.loads(r["metadata_json"] or "{}"),
                ))

        return claimed

    def commit_target_execution(
        self,
        target_id: str,
        claim_token: str,
        new_status: Any,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        content_hash: Optional[str] = None,
        consecutive_failures: int = 0,
        next_allowed_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> bool:
        """
        Fenced commit: only succeeds if the claim_token matches.
        Clears lease fields on successful commit.
        Returns True if the row was updated, False if the lease was lost.
        """
        from datetime import timezone

        self._init_target_table()
        self._init_signal_tables()

        if now is None:
            now_dt = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now_dt = now.replace(tzinfo=timezone.utc)
        else:
            now_dt = now.astimezone(timezone.utc)

        next_iso = _serialize_datetime(next_allowed_at)
        now_iso = now_dt.isoformat()
        status_val = new_status.value if hasattr(new_status, "value") else str(new_status)
        meta_json = json.dumps(metadata) if metadata is not None else None

        with self.connection:
            cursor = self.connection.cursor()
            if meta_json is not None:
                cursor.execute("""
                    UPDATE targets SET
                        status = ?, etag = ?, last_modified = ?, content_hash = ?,
                        consecutive_failures = ?, last_fetched_at = ?, next_allowed_at = ?,
                        lease_owner = NULL, lease_until = NULL, claim_token = NULL, execution_id = NULL,
                        metadata_json = ?, updated_at = ?
                    WHERE id = ? AND claim_token = ?
                """, (
                    status_val, etag, last_modified, content_hash,
                    consecutive_failures, now_iso, next_iso,
                    meta_json, now_iso, target_id, claim_token
                ))
            else:
                cursor.execute("""
                    UPDATE targets SET
                        status = ?, etag = ?, last_modified = ?, content_hash = ?,
                        consecutive_failures = ?, last_fetched_at = ?, next_allowed_at = ?,
                        lease_owner = NULL, lease_until = NULL, claim_token = NULL, execution_id = NULL,
                        updated_at = ?
                    WHERE id = ? AND claim_token = ?
                """, (
                    status_val, etag, last_modified, content_hash,
                    consecutive_failures, now_iso, next_iso,
                    now_iso, target_id, claim_token
                ))
            return cursor.rowcount > 0

    def release_target_lease(
        self,
        target_id: str,
        claim_token: str,
        now: Optional[datetime] = None,
    ) -> bool:
        """
        Release a lease without persisting execution results.
        Returns True if the lease was released, False if it was already taken.
        """
        from datetime import timezone

        self._init_target_table()
        self._init_signal_tables()

        if now is None:
            now_dt = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now_dt = now.replace(tzinfo=timezone.utc)
        else:
            now_dt = now.astimezone(timezone.utc)

        now_iso = now_dt.isoformat()

        with self.connection:
            cursor = self.connection.cursor()
            cursor.execute("""
                UPDATE targets
                SET lease_owner = NULL, lease_until = NULL, claim_token = NULL, execution_id = NULL, updated_at = ?
                WHERE id = ? AND claim_token = ?
            """, (now_iso, target_id, claim_token))
            return cursor.rowcount > 0

    def finalize_execution(
        self,
        target_id: str,
        claim_token: str,
        worker_id: str,
        transition: Any,
        signals: List[Any],
        correlation_plan: Any = None,
        now: Optional[datetime] = None,
    ) -> bool:
        """
        Atomic execution finalization with fencing.

        Verifies the claim_token, updates the target, persists signals,
        creates/updates events, and creates event-signal links — all in
        a single transaction. Returns True if the target was updated,
        False if the lease was stale or the target was not found.
        """
        from datetime import timezone

        self._init_target_table()
        self._init_signal_tables()

        if now is None:
            now_dt = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now_dt = now.replace(tzinfo=timezone.utc)
        else:
            now_dt = now.astimezone(timezone.utc)

        now_iso = now_dt.isoformat()
        status_val = transition.status.value if hasattr(transition.status, "value") else str(transition.status)
        next_iso = _serialize_datetime(transition.next_allowed_at)
        last_fetched_iso = _serialize_datetime(transition.last_fetched_at) if hasattr(transition, 'last_fetched_at') and transition.last_fetched_at else now_iso
        meta_json = json.dumps(transition.metadata) if getattr(transition, 'metadata', None) is not None else None

        try:
            with self.connection:
                cursor = self.connection.cursor()

                # 1. Fencing: verify claim_token and get current lease
                target_row = cursor.execute("""
                    SELECT status, claim_token FROM targets WHERE id = ?
                """, (target_id,)).fetchone()

                if not target_row:
                    return False

                if target_row["claim_token"] != claim_token:
                    # Stale claim
                    return False

                # 2. Get or create entity for this target
                entity = self.get_or_create_entity(
                    canonical_key=target_id,
                    name=target_id,
                    entity_type="target",
                )
                entity_id = entity.id

                # 3. Update target and clear lease
                if meta_json is not None:
                    cursor.execute("""
                        UPDATE targets SET
                            status = ?, etag = ?, last_modified = ?, content_hash = ?,
                            consecutive_failures = ?, last_fetched_at = ?, next_allowed_at = ?,
                            lease_owner = NULL, lease_until = NULL, claim_token = NULL, execution_id = NULL,
                            metadata_json = ?, updated_at = ?
                        WHERE id = ? AND claim_token = ?
                    """, (
                        status_val,
                        getattr(transition, 'etag', None),
                        getattr(transition, 'last_modified', None),
                        getattr(transition, 'content_hash', None),
                        getattr(transition, 'consecutive_failures', 0),
                        last_fetched_iso,
                        next_iso,
                        meta_json,
                        now_iso,
                        target_id,
                        claim_token,
                    ))
                else:
                    cursor.execute("""
                        UPDATE targets SET
                            status = ?, etag = ?, last_modified = ?, content_hash = ?,
                            consecutive_failures = ?, last_fetched_at = ?, next_allowed_at = ?,
                            lease_owner = NULL, lease_until = NULL, claim_token = NULL, execution_id = NULL,
                            updated_at = ?
                        WHERE id = ? AND claim_token = ?
                    """, (
                        status_val,
                        getattr(transition, 'etag', None),
                        getattr(transition, 'last_modified', None),
                        getattr(transition, 'content_hash', None),
                        getattr(transition, 'consecutive_failures', 0),
                        last_fetched_iso,
                        next_iso,
                        now_iso,
                        target_id,
                        claim_token,
                    ))

                if cursor.rowcount == 0:
                    return False

                # 4. Persist signals
                signal_id_map = {}
                for sig in signals:
                    try:
                        raw_value = sig.value
                        if isinstance(raw_value, (dict, list)):
                            raw_value = json.dumps(raw_value, default=str)
                        sig_cursor = cursor.execute("""
                            INSERT INTO signals
                                (entity_id, signal_type, observed_at, value, fingerprint, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            entity_id,
                            sig.signal_type.value if hasattr(sig.signal_type, 'value') else str(sig.signal_type),
                            sig.observed_at.isoformat(),
                            raw_value,
                            sig.fingerprint,
                            now_iso,
                        ))
                        signal_id_map[sig.fingerprint] = sig_cursor.lastrowid
                    except sqlite3.IntegrityError:
                        # Duplicate fingerprint — skip
                        continue

                # 5. Create events
                event_id_map = {}
                for evt in getattr(correlation_plan, 'events_to_create', []) or []:
                    # Validate event_type
                    try:
                        EventType(evt.event_type)
                    except ValueError:
                        raise ValueError(f"Invalid event_type: {evt.event_type}")

                    evt_cursor = cursor.execute("""
                        INSERT INTO events
                            (entity_id, event_type, status, importance, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        entity_id,
                        evt.event_type,
                        evt.status,
                        evt.importance,
                        evt.created_at.isoformat(),
                        evt.updated_at.isoformat(),
                    ))
                    event_id_map[id(evt)] = evt_cursor.lastrowid

                # 6. Update events
                for evt in getattr(correlation_plan, 'events_to_update', []) or []:
                    cursor.execute("""
                        UPDATE events SET
                            status = ?, importance = ?, updated_at = ?
                        WHERE id = ?
                    """, (
                        evt.status,
                        evt.importance,
                        evt.updated_at.isoformat(),
                        evt.event_id,
                    ))

                # 7. Create links
                signals_list = list(signals) if signals else []
                new_event_ids = [
                    event_id_map[id(evt)]
                    for evt in getattr(correlation_plan, 'events_to_create', []) or []
                ]
                new_event_idx = 0

                for idx, link in enumerate(getattr(correlation_plan, 'links', []) or []):
                    try:
                        event_id = link.event_id
                        signal_id = link.signal_id

                        # Resolve event_id placeholder for newly created events
                        if event_id is None:
                            if new_event_idx < len(new_event_ids):
                                event_id = new_event_ids[new_event_idx]
                                new_event_idx += 1
                            else:
                                continue

                        # Resolve signal_id placeholder (-1) to persisted signal ID
                        if signal_id == -1 and idx < len(signals_list):
                            sig = signals_list[idx]
                            signal_id = signal_id_map.get(sig.fingerprint)
                            if signal_id is None:
                                continue

                        if event_id is None or signal_id is None:
                            continue

                        cursor.execute("""
                            INSERT INTO event_signals (event_id, signal_id)
                            VALUES (?, ?)
                        """, (event_id, signal_id))
                    except sqlite3.IntegrityError:
                        # Invalid link — skip
                        continue

                return True
        except Exception:
            logger.exception("finalize_execution failed for correlation plan")
            return False

    def commit_plan(
        self,
        correlation_plan: Any,
        now: Optional[datetime] = None,
    ) -> bool:
        """
        Persist a CorrelationPlan atomically without target fencing.

        This is intended for signal-driven flows (e.g., webhooks) that do not
        hold a target lease. It persists signals, creates/updates events, and
        creates event-signal links in a single transaction.
        """
        from datetime import timezone

        self._init_signal_tables()

        if now is None:
            now_dt = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now_dt = now.replace(tzinfo=timezone.utc)
        else:
            now_dt = now.astimezone(timezone.utc)

        now_iso = now_dt.isoformat()

        try:
            with self.connection:
                cursor = self.connection.cursor()

                # 1. Persist signals
                signal_id_map = {}
                for sig in getattr(correlation_plan, 'signals_to_persist', []) or []:
                    try:
                        sig_cursor = cursor.execute("""
                            INSERT INTO signals
                                (entity_id, signal_type, observed_at, value, fingerprint, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            sig.entity_id,
                            sig.signal_type,
                            sig.observed_at.isoformat(),
                            sig.value,
                            sig.fingerprint,
                            now_iso,
                        ))
                        signal_id_map[sig.fingerprint] = sig_cursor.lastrowid
                    except sqlite3.IntegrityError:
                        continue

                # 2. Create events
                event_id_map = {}
                for evt in getattr(correlation_plan, 'events_to_create', []) or []:
                    try:
                        EventType(evt.event_type)
                    except ValueError:
                        raise ValueError(f"Invalid event_type: {evt.event_type}")

                    evt_cursor = cursor.execute("""
                        INSERT INTO events
                            (entity_id, event_type, status, importance, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        evt.entity_id,
                        evt.event_type,
                        evt.status,
                        evt.importance,
                        evt.created_at.isoformat(),
                        evt.updated_at.isoformat(),
                    ))
                    event_id_map[id(evt)] = evt_cursor.lastrowid

                # 3. Update events
                for evt in getattr(correlation_plan, 'events_to_update', []) or []:
                    cursor.execute("""
                        UPDATE events SET
                            status = ?, importance = ?, updated_at = ?
                        WHERE id = ?
                    """, (
                        evt.status,
                        evt.importance,
                        evt.updated_at.isoformat(),
                        evt.event_id,
                    ))

                # 4. Create links
                links = getattr(correlation_plan, 'links', []) or []
                signals_list = getattr(correlation_plan, 'signals_to_persist', []) or []
                for idx, link in enumerate(links):
                    try:
                        event_id = link.event_id
                        signal_id = link.signal_id

                        if event_id in event_id_map:
                            event_id = event_id_map[event_id]
                        elif event_id is None and event_id_map:
                            event_id = next(iter(event_id_map.values()))

                        if signal_id == -1 and idx < len(signals_list):
                            sig = signals_list[idx]
                            signal_id = signal_id_map.get(sig.fingerprint)
                            if signal_id is None:
                                continue

                        if event_id is None or signal_id is None:
                            continue

                        cursor.execute("""
                            INSERT INTO event_signals (event_id, signal_id)
                            VALUES (?, ?)
                        """, (event_id, signal_id))
                    except sqlite3.IntegrityError:
                        continue

                return True
        except Exception:
            logger.exception("finalize_execution failed for correlation plan")
            return False
