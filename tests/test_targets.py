"""Tests for WatchTarget model and validation."""

import pytest

from web_watcher.targets import (
    SUPPORTED_TARGET_TYPES,
    WatchTarget,
    validate_watch_target,
)


def _mk_target(**overrides):
    defaults = {
        "key": "github:openai/gpt",
        "target_type": "github_repository",
        "name": "GPT",
        "locator": "https://github.com/openai/gpt",
        "enabled": True,
        "priority": 50,
        "poll_interval_seconds": None,
    }
    defaults.update(overrides)
    return WatchTarget(**defaults)


class TestWatchTargetDataclass:

    def test_all_supported_types_defined(self):
        assert len(SUPPORTED_TARGET_TYPES) == 3
        assert "github_repository" in SUPPORTED_TARGET_TYPES
        assert "official_website" in SUPPORTED_TARGET_TYPES
        assert "news_source" in SUPPORTED_TARGET_TYPES

    def test_default_priority_and_enabled(self):
        t = WatchTarget(
            key="a", target_type="github_repository", name="X", locator="https://x"
        )
        assert t.priority == 50
        assert t.enabled is True

    def test_frozen_after_creation(self):
        t = _mk_target()
        with pytest.raises(Exception):
            t.key = "changed"


class TestValidateWatchTarget:

    def test_valid_target(self):
        t = _mk_target()
        validate_watch_target(t)

    def test_valid_official_website(self):
        t = _mk_target(
            target_type="official_website",
            locator="https://example.com/docs",
        )
        validate_watch_target(t)

    def test_valid_news_source(self):
        t = _mk_target(
            target_type="news_source",
            locator="https://example.com/feed",
        )
        validate_watch_target(t)

    def test_valid_with_poll_interval(self):
        t = _mk_target(poll_interval_seconds=3600)
        validate_watch_target(t)

    def test_empty_key_rejected(self):
        with pytest.raises(ValueError, match="key"):
            validate_watch_target(_mk_target(key=""))

    def test_whitespace_key_rejected(self):
        with pytest.raises(ValueError, match="key"):
            validate_watch_target(_mk_target(key="   "))

    def test_unsupported_type_rejected(self):
        with pytest.raises(ValueError, match="unsupported"):
            validate_watch_target(_mk_target(target_type="twitter_account"))

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name"):
            validate_watch_target(_mk_target(name=""))

    def test_empty_locator_rejected(self):
        with pytest.raises(ValueError, match="locator"):
            validate_watch_target(_mk_target(locator=""))

    def test_priority_below_zero_rejected(self):
        with pytest.raises(ValueError, match="priority"):
            validate_watch_target(_mk_target(priority=-1))

    def test_priority_above_100_rejected(self):
        with pytest.raises(ValueError, match="priority"):
            validate_watch_target(_mk_target(priority=101))

    def test_priority_boundary_zero_accepted(self):
        validate_watch_target(_mk_target(priority=0))

    def test_priority_boundary_100_accepted(self):
        validate_watch_target(_mk_target(priority=100))

    def test_negative_poll_interval_rejected(self):
        with pytest.raises(ValueError, match="poll interval"):
            validate_watch_target(_mk_target(poll_interval_seconds=-1))

    def test_zero_poll_interval_rejected(self):
        with pytest.raises(ValueError, match="poll interval"):
            validate_watch_target(_mk_target(poll_interval_seconds=0))

    def test_one_second_poll_accepted(self):
        validate_watch_target(_mk_target(poll_interval_seconds=1))