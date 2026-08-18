"""Unit tests for AppConfig and environment-based configuration (Phase 15-A)."""

import os
from unittest.mock import patch
from web_watcher.config import AppConfig, get_config


def test_app_config_defaults():
    cfg = AppConfig()
    assert cfg.db_path == "web_watcher.db"
    assert cfg.default_cooldown_seconds == 300.0
    assert cfg.default_batch_size == 10
    assert cfg.default_poll_interval == 1.0
    assert cfg.default_max_retries == 3
    assert cfg.default_base_backoff_sec == 1.0
    assert cfg.log_level == "INFO"
    assert cfg.webhook_url is None


@patch.dict(os.environ, {
    "WEB_WATCHER_DB": "custom.db",
    "WEB_WATCHER_COOLDOWN": "120",
    "WEB_WATCHER_BATCH_SIZE": "20",
    "WEB_WATCHER_POLL_INTERVAL": "2.5",
    "WEB_WATCHER_MAX_RETRIES": "5",
    "WEB_WATCHER_BASE_BACKOFF": "0.5",
    "WEB_WATCHER_LOG_LEVEL": "DEBUG",
    "WEB_WATCHER_WEBHOOK_URL": "https://example.com/hook",
})
def test_get_config_reads_env():
    cfg = get_config()
    assert cfg.db_path == "custom.db"
    assert cfg.default_cooldown_seconds == 120.0
    assert cfg.default_batch_size == 20
    assert cfg.default_poll_interval == 2.5
    assert cfg.default_max_retries == 5
    assert cfg.default_base_backoff_sec == 0.5
    assert cfg.log_level == "DEBUG"
    assert cfg.webhook_url == "https://example.com/hook"


@patch.dict(os.environ, {}, clear=True)
def test_get_config_falls_back_to_defaults():
    cfg = get_config()
    assert cfg.db_path == "web_watcher.db"
    assert cfg.default_cooldown_seconds == 300.0
    assert cfg.log_level == "INFO"
    assert cfg.webhook_url is None
