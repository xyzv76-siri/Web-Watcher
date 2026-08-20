"""Phase 19-01 tests: Generic Web Target declarative config, URL validation, selector validation, and persistence."""

import pytest
from datetime import datetime, timezone
from urllib.parse import urlparse

from web_watcher.models import Target, TargetStatus
from web_watcher.targets import (
    SUPPORTED_TARGET_TYPES,
    WatchTarget,
    validate_watch_target,
    validate_target_url_policy,
    validate_selector,
    _validate_url,
)
from web_watcher.generic_web_target import GenericWebTarget
from web_watcher.rule_models import ExtractorConfig
from web_watcher.repository import Repository


# ===========================================================================
# Domain — valid / invalid WatchTarget configuration
# ===========================================================================


class TestWebTargetTypeDomain:

    def test_web_type_is_supported(self):
        assert "web" in SUPPORTED_TARGET_TYPES

    def test_valid_web_target(self):
        target = WatchTarget(
            key="web:example/page",
            target_type="web",
            name="Example Page",
            locator="https://example.com/page",
            enabled=True,
            priority=50,
            poll_interval_seconds=300,
        )
        validate_watch_target(target)
        validate_target_url_policy(target)

    def test_valid_minimal_web_target(self):
        target = WatchTarget(
            key="web:example",
            target_type="web",
            name="Example",
            locator="https://example.com",
        )
        validate_watch_target(target)
        validate_target_url_policy(target)

    def test_invalid_url_empty(self):
        with pytest.raises(ValueError, match="URL must not be empty"):
            _validate_url("")

    def test_invalid_url_whitespace_only(self):
        with pytest.raises(ValueError, match="URL must not be empty"):
            _validate_url("   ")

    def test_invalid_url_no_scheme(self):
        with pytest.raises(ValueError, match="scheme"):
            _validate_url("example.com")

    def test_invalid_url_no_hostname(self):
        with pytest.raises(ValueError, match="hostname"):
            _validate_url("https://")

    def test_invalid_scheme_ftp(self):
        with pytest.raises(ValueError, match="scheme must be http or https"):
            _validate_url("ftp://example.com")

    def test_invalid_scheme_file(self):
        with pytest.raises(ValueError, match="scheme must be http or https"):
            _validate_url("file:///etc/passwd")

    def test_invalid_scheme_ssh(self):
        with pytest.raises(ValueError, match="scheme must be http or https"):
            _validate_url("ssh://git@example.com")

    def test_valid_http_url(self):
        _validate_url("http://example.com")

    def test_valid_https_url(self):
        _validate_url("https://example.com/path?query=1")

    def test_valid_https_url_with_port(self):
        _validate_url("https://example.com:8443/path")

    def test_web_target_invalid_url_rejected_by_policy(self):
        target = WatchTarget(
            key="web:bad",
            target_type="web",
            name="Bad",
            locator="ftp://example.com",
        )
        with pytest.raises(ValueError, match="scheme"):
            validate_target_url_policy(target)

    def test_web_target_url_no_hostname_rejected_by_policy(self):
        target = WatchTarget(
            key="web:bad",
            target_type="web",
            name="Bad",
            locator="https://",
        )
        with pytest.raises(ValueError, match="hostname"):
            validate_target_url_policy(target)

    def test_unknown_target_type_rejected(self):
        target = WatchTarget(
            key="unknown:x",
            target_type="twitter_account",
            name="X",
            locator="https://example.com",
        )
        with pytest.raises(ValueError, match="unsupported target type"):
            validate_watch_target(target)

    def test_missing_url_rejected(self):
        target = WatchTarget(
            key="web:x",
            target_type="web",
            name="X",
            locator="",
        )
        with pytest.raises(ValueError, match="locator"):
            validate_watch_target(target)


# ===========================================================================
# Domain — selector validation
# ===========================================================================


class TestSelectorValidation:

    def test_valid_css_selector(self):
        validate_selector("css", "div.pricing .price")

    def test_valid_xpath_selector(self):
        validate_selector("xpath", "//div[@class='pricing']//span[@class='price']")

    def test_empty_selector_type_rejected(self):
        with pytest.raises(ValueError, match="selector type"):
            validate_selector("", "div")

    def test_unsupported_selector_type_rejected(self):
        with pytest.raises(ValueError, match="selector type must be 'css' or 'xpath'"):
            validate_selector("jsonpath", "$.price")

    def test_empty_selector_rejected(self):
        with pytest.raises(ValueError, match="selector must not be empty"):
            validate_selector("css", "")

    def test_whitespace_selector_rejected(self):
        with pytest.raises(ValueError, match="selector must not be empty"):
            validate_selector("css", "   ")


# ===========================================================================
# Domain — GenericWebTarget integration with URL and selector validation
# ===========================================================================


class TestGenericWebTargetDomainValidation:

    def test_valid_target_and_extractors_constructs(self):
        target = Target(
            id="web-1",
            url="https://example.com",
            interval="15m",
            status=TargetStatus.NORMAL,
        )
        extractors = [
            ExtractorConfig(name="price", selector_type="css", selector=".price")
        ]
        adapter = GenericWebTarget(target=target, extractors=extractors)
        assert adapter.target.url == "https://example.com"

    def test_invalid_url_raises_on_construction(self):
        target = Target(
            id="web-bad",
            url="ftp://example.com",
            interval="15m",
            status=TargetStatus.NORMAL,
        )
        with pytest.raises(ValueError, match="scheme"):
            GenericWebTarget(target=target)

    def test_invalid_selector_raises_on_construction(self):
        target = Target(
            id="web-sel",
            url="https://example.com",
            interval="15m",
            status=TargetStatus.NORMAL,
        )
        extractors = [
            ExtractorConfig(name="price", selector_type="jsonpath", selector="$.[0]")
        ]
        with pytest.raises(ValueError, match="selector type"):
            GenericWebTarget(target=target, extractors=extractors)

    def test_empty_selector_raises_on_construction(self):
        target = Target(
            id="web-sel2",
            url="https://example.com",
            interval="15m",
            status=TargetStatus.NORMAL,
        )
        extractors = [
            ExtractorConfig(name="price", selector_type="css", selector="")
        ]
        with pytest.raises(ValueError, match="selector must not be empty"):
            GenericWebTarget(target=target, extractors=extractors)


# ===========================================================================
# Persistence — Target round-trip through Repository
# ===========================================================================


class TestTargetPersistence:

    def test_create_persist_reload(self, tmp_path):
        repo = Repository(tmp_path / "phase19.db")
        target = Target(
            id="web-roundtrip",
            url="https://example.com",
            interval="10m",
            status=TargetStatus.NORMAL,
            etag='"abc"',
            last_modified="Wed, 19 Aug 2026 00:00:00 GMT",
            content_hash="sha256:abc123",
            consecutive_failures=0,
            metadata={"selector": "div.main", "type": "web"},
        )
        repo.save_target(target)
        reloaded = repo.get_target("web-roundtrip")
        assert reloaded is not None
        assert reloaded.id == "web-roundtrip"
        assert reloaded.url == "https://example.com"
        assert reloaded.interval == "10m"
        assert reloaded.status == TargetStatus.NORMAL
        assert reloaded.etag == '"abc"'
        assert reloaded.last_modified == "Wed, 19 Aug 2026 00:00:00 GMT"
        assert reloaded.content_hash == "sha256:abc123"
        assert reloaded.metadata["selector"] == "div.main"
        assert reloaded.metadata["type"] == "web"
        repo.close()

    def test_restart_recovery(self, tmp_path):
        db_path = tmp_path / "restart.db"
        target = Target(
            id="web-restart",
            url="https://example.com/restart",
            interval="5m",
            status=TargetStatus.BACKOFF,
            content_hash="sha256:restart",
            metadata={"initialized": True},
        )
        repo1 = Repository(db_path)
        repo1.save_target(target)
        repo1.close()

        repo2 = Repository(db_path)
        reloaded = repo2.get_target("web-restart")
        assert reloaded is not None
        assert reloaded.url == "https://example.com/restart"
        assert reloaded.status == TargetStatus.BACKOFF
        assert reloaded.content_hash == "sha256:restart"
        assert reloaded.metadata["initialized"] is True
        repo2.close()

    def test_configuration_round_trip(self, tmp_path):
        repo = Repository(tmp_path / "roundtrip.db")
        target = Target(
            id="web-config",
            url="https://example.com/config",
            interval="30m",
            status=TargetStatus.NORMAL,
            metadata={
                "extractors": [
                    {"name": "title", "selector_type": "css", "selector": "h1"},
                    {"name": "price", "selector_type": "xpath", "selector": "//span[@class='price']"},
                ],
                "headers": {"User-Agent": "WebWatcher"},
                "timeout": 15.0,
            },
        )
        repo.save_target(target)
        reloaded = repo.get_target("web-config")
        assert reloaded is not None
        assert reloaded.metadata["extractors"][0]["name"] == "title"
        assert reloaded.metadata["extractors"][0]["selector"] == "h1"
        assert reloaded.metadata["extractors"][1]["selector_type"] == "xpath"
        assert reloaded.metadata["headers"]["User-Agent"] == "WebWatcher"
        assert reloaded.metadata["timeout"] == 15.0
        repo.close()

    def test_existing_targets_remain_readable(self, tmp_path):
        repo = Repository(tmp_path / "existing.db")
        existing = Target(
            id="github:octocat/Hello-World",
            url="https://github.com/octocat/Hello-World",
            interval="15m",
            status=TargetStatus.NORMAL,
        )
        repo.save_target(existing)

        web_target = Target(
            id="web:example",
            url="https://example.com",
            interval="10m",
            status=TargetStatus.NORMAL,
        )
        repo.save_target(web_target)

        all_targets = repo.list_targets()
        ids = {t.id for t in all_targets}
        assert "github:octocat/Hello-World" in ids
        assert "web:example" in ids
        assert len(all_targets) == 2
        repo.close()


# ===========================================================================
# Negative — malformed persisted configuration
# ===========================================================================


class TestMalformedPersistence:

    def test_invalid_persisted_metadata_does_not_crash_reload(self, tmp_path):
        repo = Repository(tmp_path / "badmeta.db")
        target = Target(
            id="web-badmeta",
            url="https://example.com",
            interval="15m",
            status=TargetStatus.NORMAL,
            metadata={"not_json": {1, 2, 3}},  # sets will break JSON serialization
        )
        with pytest.raises((TypeError, ValueError)):
            repo.save_target(target)

    def test_invalid_url_in_persisted_target(self, tmp_path):
        repo = Repository(tmp_path / "invalid.db")
        # Manually insert an invalid URL directly into DB to simulate legacy/corrupt data
        repo._init_target_table()
        repo.connection.execute(
            """
            INSERT INTO targets (id, url, interval, status, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "web-corrupt",
                "not-a-url",
                "15m",
                "normal",
                "{}",
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        repo.connection.commit()
        reloaded = repo.get_target("web-corrupt")
        # Repository should still return a Target object; domain validation is separate
        assert reloaded is not None
        assert reloaded.id == "web-corrupt"
        repo.close()
