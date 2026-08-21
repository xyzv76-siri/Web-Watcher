"""Configuration management with environment variables and defaults (Phase 15-A)."""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AppConfig:
    db_path: str = "web_watcher.db"
    default_cooldown_seconds: float = 300.0
    default_batch_size: int = 10
    default_poll_interval: float = 1.0
    default_max_retries: int = 3
    default_base_backoff_sec: float = 1.0
    log_level: str = "INFO"
    webhook_url: Optional[str] = None
    retention_max_age_days: int = 30
    retention_dry_run: bool = False
    noise_reduction_level: str = "standard"


def get_config() -> AppConfig:
    """Build AppConfig from environment variables with sensible defaults."""
    return AppConfig(
        db_path=os.getenv("WEB_WATCHER_DB", "web_watcher.db"),
        default_cooldown_seconds=float(os.getenv("WEB_WATCHER_COOLDOWN", "300")),
        default_batch_size=int(os.getenv("WEB_WATCHER_BATCH_SIZE", "10")),
        default_poll_interval=float(os.getenv("WEB_WATCHER_POLL_INTERVAL", "1.0")),
        default_max_retries=int(os.getenv("WEB_WATCHER_MAX_RETRIES", "3")),
        default_base_backoff_sec=float(os.getenv("WEB_WATCHER_BASE_BACKOFF", "1.0")),
        log_level=os.getenv("WEB_WATCHER_LOG_LEVEL", "INFO"),
        webhook_url=os.getenv("WEB_WATCHER_WEBHOOK_URL"),
        retention_max_age_days=int(os.getenv("WEB_WATCHER_RETENTION_MAX_AGE_DAYS", "30")),
        retention_dry_run=os.getenv("WEB_WATCHER_RETENTION_DRY_RUN", "false").lower() in ("1", "true", "yes"),
        noise_reduction_level=os.getenv("WEB_WATCHER_NOISE_REDUCTION_LEVEL", "standard"),
    )
