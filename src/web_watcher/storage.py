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


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply the full schema to a connection."""
    cur = conn.cursor()
    for stmt in SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)
            if stmt.upper().startswith("PRAGMA"):
                continue
            conn.commit()
    conn.commit()
