"""Data retention policy enforcement (Phase 16-A)."""

from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass
from web_watcher.repository import Repository


@dataclass
class RetentionPolicy:
    max_age_days: int = 30
    max_rows: Optional[int] = None
    dry_run: bool = False


class RetentionManager:
    """Enforces data retention policies on the repository."""

    def __init__(self, repo: Repository, policy: Optional[RetentionPolicy] = None):
        self.repo = repo
        self.policy = policy or RetentionPolicy()

    def enforce(self) -> dict:
        """Apply retention policy and return a summary of actions taken."""
        summary = {"deleted_events": 0, "deleted_notifications": 0, "dry_run": self.policy.dry_run}

        if self.policy.dry_run:
            return summary

        cutoff = datetime.utcnow() - timedelta(days=self.policy.max_age_days)

        if hasattr(self.repo, "delete_old_events"):
            summary["deleted_events"] = self.repo.delete_old_events(cutoff=cutoff)

        if hasattr(self.repo, "delete_old_notifications"):
            summary["deleted_notifications"] = self.repo.delete_old_notifications(cutoff=cutoff)

        return summary
