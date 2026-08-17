"""Minimal SQLite storage foundation."""

import sqlite3
from pathlib import Path


def open_database(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)
