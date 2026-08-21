"""Docker / production entrypoint for web-watcher.

Runs ScheduledRunner in a continuous loop with:
- configuration validation
- database / schema validation
- SIGTERM / SIGINT graceful shutdown
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from pathlib import Path

from .cli import get_config
from .config import AppConfig
from .repository import Repository
from .scheduled_runner import ScheduledRunner

LOGGER = logging.getLogger("web_watcher.docker_run")


def _validate_config(config: AppConfig) -> None:
    """Fail fast on obviously invalid config values."""
    if config.default_poll_interval <= 0:
        raise ValueError(f"default_poll_interval must be > 0, got {config.default_poll_interval}")
    if config.default_batch_size <= 0:
        raise ValueError(f"default_batch_size must be > 0, got {config.default_batch_size}")
    if config.default_max_retries < 0:
        raise ValueError(f"default_max_retries must be >= 0, got {config.default_max_retries}")
    if config.default_base_backoff_sec <= 0:
        raise ValueError(f"default_base_backoff_sec must be > 0, got {config.default_base_backoff_sec}")
    if config.retention_max_age_days <= 0:
        raise ValueError(f"retention_max_age_days must be > 0, got {config.retention_max_age_days}")
    valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if config.log_level.upper() not in valid_log_levels:
        raise ValueError(f"log_level must be one of {valid_log_levels}, got {config.log_level}")


def _validate_database(db_path: str) -> Repository:
    """Validate that the database is accessible and schema is loadable."""
    path = Path(db_path)
    if path.exists() and not path.is_file():
        raise ValueError(f"DB path exists but is not a file: {db_path}")

    repo = Repository(db_path)
    # Touch the repo to force schema initialization / migration.
    repo._get_connection()
    return repo


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = get_config()
    _validate_config(config)

    db_path = config.db_path
    LOGGER.info("Validating database at %s", db_path)
    repo = _validate_database(db_path)

    runner = ScheduledRunner(
        repo=repo,
        interval=config.default_poll_interval,
    )

    shutdown_requested = False

    def _handle_shutdown(signum: int, _frame: object) -> None:
        nonlocal shutdown_requested
        LOGGER.info("Received signal %s, requesting graceful shutdown", signum)
        shutdown_requested = True

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    LOGGER.info(
        "Starting web-watcher daemon (interval=%ss, db=%s)",
        config.default_poll_interval,
        db_path,
    )

    while not shutdown_requested:
        try:
            runner.run_once()
        except (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error) as exc:  # pragma: no cover — defensive log
            LOGGER.exception("ScheduledRunner iteration failed: %s", exc)
        if not shutdown_requested:
            # Sleep in small increments so we can react to SIGTERM quickly.
            sleep_time = config.default_poll_interval
            while sleep_time > 0 and not shutdown_requested:
                time.sleep(min(1.0, sleep_time))
                sleep_time -= 1.0

    LOGGER.info("Daemon exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
