"""Data retention policy enforcement (Phase 16-A)."""

from datetime import datetime, timedelta, timezone
from typing import Optional, List, Union
from dataclasses import dataclass, field
from web_watcher.config import AppConfig
from web_watcher.repository import Repository
from web_watcher.event_types import EventType
from web_watcher.event_status import EventStatus
from web_watcher.importance import Importance


@dataclass
class RetentionPolicy:
    max_age_days: int = 30
    max_rows: Optional[int] = None
    dry_run: bool = False
    entity_ids: Optional[List[int]] = None
    event_types: Optional[List[Union[EventType, str]]] = None
    importances: Optional[List[Union[Importance, str]]] = None
    statuses: Optional[List[Union[EventStatus, str]]] = None
    channels: Optional[List[str]] = None


class RetentionManager:
    """Enforces data retention policies on the repository."""

    def __init__(
        self,
        repo: Repository,
        policy: Optional[RetentionPolicy] = None,
        config: Optional[AppConfig] = None,
    ):
        self.repo = repo
        self.config = config
        if policy is not None:
            self.policy = policy
        elif config is not None:
            self.policy = RetentionPolicy(
                max_age_days=config.retention_max_age_days,
                dry_run=config.retention_dry_run,
            )
        else:
            self.policy = RetentionPolicy()

    def _build_event_where(self, cutoff: datetime) -> tuple[str, list]:
        clauses = ["created_at < ?"]
        params = [cutoff.isoformat()]

        if self.policy.entity_ids:
            clauses.append(f"entity_id IN ({', '.join('?' * len(self.policy.entity_ids))})")
            params.extend(self.policy.entity_ids)
        if self.policy.event_types:
            clauses.append(f"event_type IN ({', '.join('?' * len(self.policy.event_types))})")
            params.extend(str(t) for t in self.policy.event_types)
        if self.policy.importances:
            clauses.append(f"importance IN ({', '.join('?' * len(self.policy.importances))})")
            params.extend(str(i) for i in self.policy.importances)
        if self.policy.statuses:
            clauses.append(f"status IN ({', '.join('?' * len(self.policy.statuses))})")
            params.extend(str(s) for s in self.policy.statuses)

        where = " AND ".join(clauses)
        return where, params

    def _build_notification_where(self, cutoff: datetime) -> tuple[str, list]:
        clauses = ["created_at < ?"]
        params = [cutoff.isoformat()]

        if self.policy.channels:
            clauses.append(f"channel IN ({', '.join('?' * len(self.policy.channels))})")
            params.extend(self.policy.channels)

        where = " AND ".join(clauses)
        return where, params

    def _has_filters(self) -> bool:
        return any([
            self.policy.entity_ids,
            self.policy.event_types,
            self.policy.importances,
            self.policy.statuses,
            self.policy.channels,
        ])

    def enforce(self) -> dict:
        """Apply retention policy and return a summary of actions taken."""
        summary = {
            "deleted_events": 0,
            "deleted_notifications": 0,
            "dry_run": self.policy.dry_run,
            "filters": {
                "entity_ids": self.policy.entity_ids,
                "event_types": [str(t) for t in self.policy.event_types] if self.policy.event_types else None,
                "importances": [str(i) for i in self.policy.importances] if self.policy.importances else None,
                "statuses": [str(s) for s in self.policy.statuses] if self.policy.statuses else None,
                "channels": self.policy.channels,
            },
        }

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.policy.max_age_days)
        use_selective = self._has_filters()

        if self.policy.dry_run:
            if use_selective and hasattr(self.repo, "connection"):
                event_where, event_params = self._build_event_where(cutoff)
                notif_where, notif_params = self._build_notification_where(cutoff)

                cursor = self.repo.connection.execute(f"SELECT COUNT(*) FROM events WHERE {event_where}", event_params)
                summary["deleted_events"] = cursor.fetchone()[0]

                cursor = self.repo.connection.execute(f"SELECT COUNT(*) FROM notifications WHERE {notif_where}", notif_params)
                summary["deleted_notifications"] = cursor.fetchone()[0]
            return summary

        if use_selective and hasattr(self.repo, "connection"):
            event_where, event_params = self._build_event_where(cutoff)
            cursor = self.repo.connection.execute(
                f"DELETE FROM events WHERE {event_where}",
                event_params,
            )
            summary["deleted_events"] = cursor.rowcount

            notif_where, notif_params = self._build_notification_where(cutoff)
            cursor = self.repo.connection.execute(
                f"DELETE FROM notifications WHERE {notif_where}",
                notif_params,
            )
            summary["deleted_notifications"] = cursor.rowcount

            self.repo.connection.commit()
        else:
            if hasattr(self.repo, "delete_old_events"):
                summary["deleted_events"] = self.repo.delete_old_events(cutoff=cutoff)

            if hasattr(self.repo, "delete_old_notifications"):
                summary["deleted_notifications"] = self.repo.delete_old_notifications(cutoff=cutoff)

        return summary

    def cleanup_old_records(self) -> dict:
        """Alias for enforce(), kept for backward compatibility with existing call sites."""
        return self.enforce()
