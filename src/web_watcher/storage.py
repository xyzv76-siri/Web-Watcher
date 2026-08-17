"""SQLite storage with full schema support."""

import sqlite3
from pathlib import Path

from .storage_schema import SCHEMA


def open_database(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Apply the full schema to a connection using executescript."""
    connection.executescript(SCHEMA)
    connection.commit()


def init_schema(conn: sqlite3.Connection) -> None:
    """Backwards-compatible alias for initialize_schema."""
    initialize_schema(conn)
