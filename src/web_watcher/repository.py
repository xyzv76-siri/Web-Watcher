"""Persistence operations for Web Watcher core state."""

from datetime import datetime, timezone
from typing import Optional

from .models import Entity
from .storage import open_database, initialize_schema


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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