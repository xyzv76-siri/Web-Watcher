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
    )
