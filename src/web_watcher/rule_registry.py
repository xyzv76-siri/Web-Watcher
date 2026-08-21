"""Rule Registry: runtime rule enable/disable/priority/group management (Phase 20-B)."""

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


class RuleRegistry:
    """Manages runtime rule state in SQLite.
    
    Provides:
    - Enable/disable rules without modifying YAML
    - Priority ordering for execution
    - Group management for tag-based scheduling
    """

    def __init__(self, repository):
        self.repo = repository
        self._ensure_table()

    def _ensure_table(self):
        """Create rule_registry table if it does not exist."""
        self.repo.connection.execute("""
            CREATE TABLE IF NOT EXISTS rule_registry (
                rule_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                priority INTEGER NOT NULL DEFAULT 0,
                group_name TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self.repo.connection.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _row_to_dict(self, row: tuple) -> Dict[str, Any]:
        return {
            "rule_id": row[0],
            "enabled": bool(row[1]),
            "priority": row[2],
            "group_name": row[3],
            "metadata": json.loads(row[4] or "{}"),
            "created_at": row[5],
            "updated_at": row[6],
        }

    def upsert(self, rule_id: str, enabled: bool = True, priority: int = 0, group_name: str = "", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create or update a rule registry entry."""
        now = self._now()
        existing = self.repo.connection.execute(
            "SELECT rule_id, enabled, priority, group_name, metadata_json, created_at, updated_at FROM rule_registry WHERE rule_id = ?",
            (rule_id,),
        ).fetchone()

        meta = metadata or {}
        if existing:
            self.repo.connection.execute(
                """
                UPDATE rule_registry
                SET enabled = ?, priority = ?, group_name = ?, metadata_json = ?, updated_at = ?
                WHERE rule_id = ?
                """,
                (1 if enabled else 0, priority, group_name, json.dumps(meta, ensure_ascii=False), now, rule_id),
            )
        else:
            self.repo.connection.execute(
                """
                INSERT INTO rule_registry (rule_id, enabled, priority, group_name, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (rule_id, 1 if enabled else 0, priority, group_name, json.dumps(meta, ensure_ascii=False), now, now),
            )

        self.repo.connection.commit()
        return self.get(rule_id)

    def get(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Get a single rule registry entry."""
        row = self.repo.connection.execute(
            "SELECT rule_id, enabled, priority, group_name, metadata_json, created_at, updated_at FROM rule_registry WHERE rule_id = ?",
            (rule_id,),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_rules(self, group_name: Optional[str] = None, enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        """List rule registry entries with optional filters."""
        query = "SELECT rule_id, enabled, priority, group_name, metadata_json, created_at, updated_at FROM rule_registry WHERE 1=1"
        params: List[Any] = []

        if group_name is not None:
            query += " AND group_name = ?"
            params.append(group_name)

        if enabled is not None:
            query += " AND enabled = ?"
            params.append(1 if enabled else 0)

        query += " ORDER BY priority DESC, rule_id ASC"

        rows = self.repo.connection.execute(query, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def enable(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Enable a rule."""
        return self.upsert(rule_id, enabled=True)

    def disable(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Disable a rule."""
        return self.upsert(rule_id, enabled=False)

    def set_priority(self, rule_id: str, priority: int) -> Optional[Dict[str, Any]]:
        """Set execution priority for a rule."""
        existing = self.get(rule_id)
        enabled = existing["enabled"] if existing else True
        return self.upsert(rule_id, enabled=enabled, priority=priority)

    def set_group(self, rule_id: str, group_name: str) -> Optional[Dict[str, Any]]:
        """Assign a rule to a group."""
        existing = self.get(rule_id)
        enabled = existing["enabled"] if existing else True
        priority = existing["priority"] if existing else 0
        return self.upsert(rule_id, enabled=enabled, priority=priority, group_name=group_name)

    def remove(self, rule_id: str) -> bool:
        """Remove a rule from the registry."""
        cur = self.repo.connection.execute("DELETE FROM rule_registry WHERE rule_id = ?", (rule_id,))
        self.repo.connection.commit()
        return cur.rowcount > 0

    def get_enabled_rules(self, group_name: Optional[str] = None) -> List[str]:
        """Get list of enabled rule IDs, optionally filtered by group."""
        query = "SELECT rule_id FROM rule_registry WHERE enabled = 1"
        params: List[Any] = []
        if group_name is not None:
            query += " AND group_name = ?"
            params.append(group_name)
        query += " ORDER BY priority DESC, rule_id ASC"
        rows = self.repo.connection.execute(query, params).fetchall()
        return [r[0] for r in rows]
