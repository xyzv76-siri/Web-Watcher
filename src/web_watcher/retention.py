"""Data retention policy enforcement (Phase 16-A)."""

from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass
from web_watcher.config import AppConfig
from web_watcher.repository import Repository


@dataclass
class RetentionPolicy:
    max_age_days: int = 30
    max_rows: Optional[int] = None
    dry_run: bool = False


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

    def enforce(self) -> dict:
        """Apply retention policy and return a summary of actions taken."""
        summary = {"deleted_events": 0, "deleted_notifications": 0, "dry_run": self.policy.dry_run}

        if self.policy.dry_run:
            return summary

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.policy.max_age_days)

        if hasattr(self.repo, "delete_old_events"):
            summary["deleted_events"] = self.repo.delete_old_events(cutoff=cutoff)

        if hasattr(self.repo, "delete_old_notifications"):
            summary["deleted_notifications"] = self.repo.delete_old_notifications(cutoff=cutoff)

        return summary

    def cleanup_old_records(self) -> dict:
        """Alias for enforce(), kept for backward compatibility with existing call sites."""
        return self.enforce()
