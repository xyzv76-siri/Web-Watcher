"""Persistence operations for Web Watcher core state."""

import sqlite3

from datetime import datetime, timezone
from typing import Optional

from .models import Entity, FetchState, Signal
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