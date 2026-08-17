"""Persistence operations for Web Watcher core state."""

import sqlite3

from datetime import datetime, timezone
from typing import Optional

from .models import Entity, Event, FetchState, Signal
from .storage import initialize_schema, open_database


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
        signal_type: str,
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

        try:
            cursor = self.connection.execute(
                """
                INSERT INTO signals
                    (entity_id, signal_type, observed_at, value, fingerprint, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (entity_id, signal_type, obs, value, fingerprint, now),
            )
            self.connection.commit()
            return Signal(
                id=cursor.lastrowid,
                entity_id=entity_id,
                signal_type=signal_type,
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
        signal_type: Optional[str] = None,
    ) -> int:
        if signal_type is not None:
            row = self.connection.execute(
                """
                SELECT COUNT(*) as cnt FROM signals
                WHERE entity_id = ? AND signal_type = ?
                """,
                (entity_id, signal_type),
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
        event_type: str,
        status: str = "open",
        importance: str = "medium",
        created_at: Optional[datetime] = None,
    ) -> Event:
        """Create a new Event for the given entity."""
        now = created_at or utc_now()
        now_iso = now.isoformat()

        cursor = self.connection.execute(
            """
            INSERT INTO events
                (entity_id, event_type, status, importance, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (entity_id, event_type, status, importance, now_iso, now_iso),
        )
        self.connection.commit()

        return Event(
            id=cursor.lastrowid,
            entity_id=entity_id,
            event_type=event_type,
            status=status,
            importance=importance,
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
            event_type=row[2],
            status=row[3],
            importance=row[4],
            created_at=_parse_iso_datetime(row[5]) or _fallback_datetime(),
            updated_at=_parse_iso_datetime(row[6]) or _fallback_datetime(),
        )

    def update_event(
        self,
        event_id: int,
        status: Optional[str] = None,
        importance: Optional[str] = None,
        updated_at: Optional[datetime] = None,
    ) -> Optional[Event]:
        """Update selected fields of an existing Event.

        Returns the updated Event or None if not found.
        """
        existing = self.get_event(event_id)
        if existing is None:
            return None

        new_status = status if status is not None else existing.status
        new_importance = importance if importance is not None else existing.importance
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
            status=new_status,
            importance=new_importance,
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
                signal_type=r["signal_type"],
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
    ) -> Optional[Event]:
        """Find the most recent *open* Event for *entity_id* created on or after *cutoff*.

        If *cutoff* is None, no time filter is applied.
        Returns the Event with the latest created_at among matches, or None.
        """
        if cutoff is not None:
            cutoff_str = cutoff.isoformat()
            row = self.connection.execute(
                """
                SELECT id, entity_id, event_type, status, importance, created_at, updated_at
                FROM events
                WHERE entity_id = ? AND status = 'open' AND created_at >= ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (entity_id, cutoff_str),
            ).fetchone()
        else:
            row = self.connection.execute(
                """
                SELECT id, entity_id, event_type, status, importance, created_at, updated_at
                FROM events
                WHERE entity_id = ? AND status = 'open'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (entity_id,),
            ).fetchone()

        if row is None:
            return None

        return Event(
            id=row[0],
            entity_id=row[1],
            event_type=row[2],
            status=row[3],
            importance=row[4],
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