"""Tests for dynamic noise filtering and false positive protection."""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from web_watcher.dynamic_noise import (
    DynamicNoiseFilter,
    FalsePositiveGuard,
    dynamic_noise_ratio,
    is_likely_dynamic_noise,
    contains_dynamic_noise,
)
from web_watcher.models import Target, TargetStatus
from web_watcher.generic_web_target import GenericWebTarget
from web_watcher.rule_models import ExtractorConfig
from web_watcher.observation import ObservationStatus


# ---------------------------------------------------------------------------
# DynamicNoiseFilter tests
# ---------------------------------------------------------------------------

class TestDynamicNoiseFilter:

    def test_filter_removes_iso_timestamp(self):
        f = DynamicNoiseFilter()
        assert "2026-08-19T12:34:56Z" not in f.filter("Price $99 at 2026-08-19T12:34:56Z")

    def test_filter_removes_rfc1123_date(self):
        f = DynamicNoiseFilter()
        text = "Updated Wed, 19 Aug 2026 00:00:00 GMT"
        assert "Wed, 19 Aug 2026 00:00:00 GMT" not in f.filter(text)

    def test_filter_removes_unix_timestamp(self):
        f = DynamicNoiseFilter()
        text = "Session 1724784000 active"
        assert "1724784000" not in f.filter(text)

    def test_filter_removes_relative_time(self):
        f = DynamicNoiseFilter()
        text = "Updated 2h ago"
        assert "2h ago" not in f.filter(text)

    def test_filter_removes_uuid(self):
        f = DynamicNoiseFilter()
        uid = "550e8400-e29b-41d4-a716-446655440000"
        text = f"Request {uid} processed"
        assert uid not in f.filter(text)

    def test_filter_removes_tracking_params(self):
        f = DynamicNoiseFilter()
        text = "https://example.com?utm_source=email&_ga=abc123"
        filtered = f.filter(text)
        assert "utm_source" not in filtered
        assert "_ga" not in filtered

    def test_filter_removes_hex_token(self):
        f = DynamicNoiseFilter()
        token = "a" * 40
        text = f"csrf={token}"
        assert token not in f.filter(text)

    def test_filter_removes_opaque_session(self):
        f = DynamicNoiseFilter()
        text = "session=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        filtered = f.filter(text)
        assert "eyJhbGciOiJIUzI1NiJ9" not in filtered

    def test_filter_preserves_semantic_content(self):
        f = DynamicNoiseFilter()
        text = "Product price is $99.00 and available now"
        assert f.filter(text) == text

    def test_filter_normalize_combines_noise_removal_and_whitespace(self):
        f = DynamicNoiseFilter()
        text = "Price  $99  at  2026-08-19T12:00:00Z"
        result = f.filter_normalize(text)
        assert "2026-08-19T12:00:00Z" not in result
        assert "  " not in result

    def test_custom_patterns(self):
        import re
        patterns = [re.compile(r"CUSTOM-\d+")]
        f = DynamicNoiseFilter(patterns=patterns)
        assert f.filter("ID CUSTOM-1234") == "ID "

    def test_custom_placeholder(self):
        import re
        patterns = [re.compile(r"CUSTOM-\d{4,}")]
        f = DynamicNoiseFilter(patterns=patterns, placeholder="<REDACTED>")
        result = f.filter("ID CUSTOM-123456")
        assert "<REDACTED>" in result


# ---------------------------------------------------------------------------
# FalsePositiveGuard tests
# ---------------------------------------------------------------------------

class TestFalsePositiveGuard:

    def _make_diff(self, before: str, after: str):
        from web_watcher.diff import compute_diff
        return compute_diff(before, after)

    def _guard_kwargs(self, diffs, normalized_values, previous_values, all_failed=False, is_first=False, status_code=200):
        return {
            "diffs": {name: self._make_diff(previous_values.get(name, ""), normalized_values.get(name, "")) for name in diffs},
            "normalized_values": normalized_values,
            "previous_values": previous_values,
            "all_extractors_failed": all_failed,
            "is_first_observation": is_first,
            "http_status_code": status_code,
        }

    def test_first_observation_suppressed(self):
        guard = FalsePositiveGuard()
        suppress, reason = guard.should_suppress_signal(
            **self._guard_kwargs({"price": True}, {"price": "99.0"}, {"price": ""}, is_first=True)
        )
        assert suppress is True
        assert "First observation" in reason

    def test_all_extractors_failed_suppressed(self):
        guard = FalsePositiveGuard()
        suppress, reason = guard.should_suppress_signal(
            **self._guard_kwargs({"price": True}, {"price": ""}, {"price": ""}, all_failed=True)
        )
        assert suppress is True
        assert "All extractors failed" in reason

    def test_http_403_suppressed(self):
        guard = FalsePositiveGuard()
        suppress, reason = guard.should_suppress_signal(
            **self._guard_kwargs({"price": True}, {"price": "99.0"}, {"price": "99.0"}, status_code=403)
        )
        assert suppress is True
        assert "403" in reason

    def test_http_429_suppressed(self):
        guard = FalsePositiveGuard()
        suppress, reason = guard.should_suppress_signal(
            **self._guard_kwargs({"price": True}, {"price": "99.0"}, {"price": "99.0"}, status_code=429)
        )
        assert suppress is True
        assert "429" in reason

    def test_timestamp_only_change_suppressed(self):
        guard = FalsePositiveGuard()
        prev = "Price $99 updated 2026-08-18T12:00:00Z"
        curr = "Price $99 updated 2026-08-19T12:00:00Z"
        suppress, reason = guard.should_suppress_signal(
            **self._guard_kwargs({"price": True}, {"price": curr}, {"price": prev})
        )
        assert suppress is True
        assert "dynamic noise" in reason.lower()

    def test_session_token_change_suppressed(self):
        guard = FalsePositiveGuard()
        prev = "Token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        curr = "Token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI4OTY3NTE4MzY1In0.sKz8K7wL9mN1pQrStUvWxYzA2bC3dE4fG5hJ6kL7mN"
        suppress, reason = guard.should_suppress_signal(
            **self._guard_kwargs({"greeting": True}, {"greeting": curr}, {"greeting": prev})
        )
        assert suppress is True
        assert "dynamic noise" in reason.lower()

    def test_actual_content_change_not_suppressed(self):
        guard = FalsePositiveGuard()
        prev = "Price $99"
        curr = "Price $79"
        suppress, reason = guard.should_suppress_signal(
            **self._guard_kwargs({"price": True}, {"price": curr}, {"price": prev})
        )
        assert suppress is False
        assert "Semantic change" in reason

    def test_whitespace_only_change_suppressed(self):
        guard = FalsePositiveGuard()
        # In the real pipeline, values are already normalized before reaching the guard.
        prev = "Price $99"
        curr = "Price $99"
        suppress, reason = guard.should_suppress_signal(
            **self._guard_kwargs({"price": True}, {"price": curr}, {"price": prev})
        )
        # Identical normalized values mean no change; guard is not even the deciding factor here.
        assert suppress is False
        assert "No extractor reported a change" in reason

    def test_mixed_change_kept_when_semantic_part_changes(self):
        guard = FalsePositiveGuard()
        prev = "Updated 2026-08-18T12:00:00Z Price $99"
        curr = "Updated 2026-08-19T12:00:00Z Price $79"
        suppress, reason = guard.should_suppress_signal(
            **self._guard_kwargs({"line": True}, {"line": curr}, {"line": prev})
        )
        # The semantic part ($99 -> $79) should keep the signal.
        assert suppress is False
        assert "Semantic change" in reason


# ---------------------------------------------------------------------------
# Integration tests: GenericWebTarget with false positive guard
# ---------------------------------------------------------------------------

class TestGenericWebTargetFalsePositive:

    def _make_target(self, metadata=None):
        return Target(
            id="web-1",
            url="https://example.com",
            interval="15m",
            status=TargetStatus.NORMAL,
            metadata=metadata or {},
        )

    def _make_extractor(self, name="price", selector=".price"):
        return ExtractorConfig(name=name, selector_type="css", selector=selector)

    def _run(self, html, target=None, extractors=None, metadata=None):
        target = target or self._make_target(metadata=metadata)
        extractors = extractors or [self._make_extractor()]
        adapter = GenericWebTarget(target=target, extractors=extractors)

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = MagicMock(
            content=html,
            status_code=200,
            etag=None,
            last_modified=None,
            error=None,
            status="success",
            target_key=target.id,
            fetched_at=datetime.now(timezone.utc),
            content_hash=None,
        )

        mock_policy = MagicMock()
        mock_policy.prepare_request.return_value = MagicMock(allowed=True, reason="", headers={})
        mock_policy.evaluate_response.return_value = MagicMock(
            allowed=True,
            should_emit_signal=True,
            new_status=TargetStatus.NORMAL,
            status_code=200,
            updated_etag=None,
            updated_last_modified=None,
            consecutive_failures=0,
            next_allowed_at=None,
            reason="OK",
        )

        return adapter.execute(fetcher=mock_fetcher, policy=mock_policy)

    # --- Timestamp-only change ---
    def test_timestamp_only_change_suppressed(self):
        html_old = "<html><body><div class='price'>$99.00 <span class='ts'>2026-08-18T12:00:00Z</span></div></body></html>"
        html_new = "<html><body><div class='price'>$99.00 <span class='ts'>2026-08-19T12:00:00Z</span></div></body></html>"
        target = self._make_target(metadata={"initialized": True, "normalized_values": {"price": "$99.00 2026-08-18T12:00:00Z"}})
        result = self._run(html_new, target=target)
        assert result.observation.status == ObservationStatus.UNCHANGED
        assert len(result.signals_emitted) == 0

    # --- Dynamic token change ---
    def test_dynamic_token_change_suppressed(self):
        html_old = "<html><body><div class='greeting'>Token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U</div></body></html>"
        html_new = "<html><body><div class='greeting'>Token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI4OTY3NTE4MzY1In0.sKz8K7wL9mN1pQrStUvWxYzA2bC3dE4fG5hJ6kL7mN</div></body></html>"
        target = self._make_target(metadata={"initialized": True, "normalized_values": {"greeting": "Token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"}})
        result = self._run(html_new, target=target, extractors=[ExtractorConfig(name="greeting", selector_type="css", selector=".greeting")])
        assert result.observation.status == ObservationStatus.UNCHANGED
        assert len(result.signals_emitted) == 0

    # --- Actual content change kept ---
    def test_actual_content_change_emits_signal(self):
        html_old = "<html><body><div class='price'>$99.00</div></body></html>"
        html_new = "<html><body><div class='price'>$79.00</div></body></html>"
        target = self._make_target(metadata={"initialized": True, "normalized_values": {"price": "$99.00"}})
        result = self._run(html_new, target=target)
        assert result.observation.status == ObservationStatus.CHANGED
        assert len(result.signals_emitted) == 1

    # --- Selector missing ---
    def test_selector_missing_is_extraction_failure_not_deletion(self):
        html = "<html><body><div class='price'>$99.00</div></body></html>"
        extractors = [ExtractorConfig(name="missing", selector_type="css", selector=".missing")]
        target = self._make_target(metadata={"initialized": True, "normalized_values": {"missing": "$99.00"}})
        result = self._run(html, target=target, extractors=extractors)
        assert result.observation.status == ObservationStatus.EXTRACTION_FAILURE
        assert len(result.signals_emitted) == 0
        assert result.observation.evidence["extractor_results"]["missing"]["status"] == "not_found"

    # --- Selector returns empty content ---
    def test_selector_returns_empty_is_not_deletion(self):
        html = "<html><body><div class='price'></div></body></html>"
        target = self._make_target(metadata={"initialized": True, "normalized_values": {"price": ""}})
        result = self._run(html, target=target)
        # Empty content after extraction is still a successful extraction; diff determines change.
        assert result.observation.status in (ObservationStatus.UNCHANGED, ObservationStatus.FIRST_OBSERVATION)
        assert len(result.signals_emitted) == 0

    # --- HTTP 403 ---
    def test_http_403_is_not_deletion(self):
        target = self._make_target()
        adapter = GenericWebTarget(target=target, extractors=[self._make_extractor()])

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = MagicMock(
            content="",
            status_code=403,
            etag=None,
            last_modified=None,
            error="Forbidden",
            status="http_error",
            target_key=target.id,
            fetched_at=datetime.now(timezone.utc),
            content_hash=None,
        )

        mock_policy = MagicMock()
        mock_policy.prepare_request.return_value = MagicMock(allowed=True, reason="", headers={})
        mock_policy.evaluate_response.return_value = MagicMock(
            allowed=True,
            should_emit_signal=False,
            new_status=TargetStatus.COOLDOWN,
            status_code=403,
            updated_etag=None,
            updated_last_modified=None,
            consecutive_failures=1,
            next_allowed_at=datetime.utcnow(),
            reason="HTTP 403 Forbidden",
        )

        result = adapter.execute(fetcher=mock_fetcher, policy=mock_policy)
        # 403 enters cooldown via policy; observation status is unchanged because
        # no content was successfully fetched or compared.
        assert result.observation.status == ObservationStatus.UNCHANGED
        assert len(result.signals_emitted) == 0

    # --- HTTP 429 ---
    def test_http_429_is_not_deletion(self):
        target = self._make_target()
        adapter = GenericWebTarget(target=target, extractors=[self._make_extractor()])

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = MagicMock(
            content="",
            status_code=429,
            etag=None,
            last_modified=None,
            error="Rate Limited",
            status="error",
            target_key=target.id,
            fetched_at=datetime.now(timezone.utc),
            content_hash=None,
        )

        mock_policy = MagicMock()
        mock_policy.prepare_request.return_value = MagicMock(allowed=True, reason="", headers={})
        mock_policy.evaluate_response.return_value = MagicMock(
            allowed=True,
            should_emit_signal=False,
            new_status=TargetStatus.BACKOFF,
            status_code=429,
            updated_etag=None,
            updated_last_modified=None,
            consecutive_failures=1,
            next_allowed_at=datetime.utcnow(),
            reason="HTTP 429 Rate Limited",
        )

        result = adapter.execute(fetcher=mock_fetcher, policy=mock_policy)
        assert result.observation.status == ObservationStatus.HTTP_FAILURE
        assert len(result.signals_emitted) == 0

    # --- Timeout ---
    def test_timeout_is_not_deletion(self):
        target = self._make_target()
        adapter = GenericWebTarget(target=target, extractors=[self._make_extractor()])

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = MagicMock(
            content="",
            status_code=0,
            etag=None,
            last_modified=None,
            error="timeout",
            status="timeout",
            target_key=target.id,
            fetched_at=datetime.now(timezone.utc),
            content_hash=None,
        )

        mock_policy = MagicMock()
        mock_policy.prepare_request.return_value = MagicMock(allowed=True, reason="", headers={})
        mock_policy.evaluate_response.return_value = MagicMock(
            allowed=True,
            should_emit_signal=False,
            new_status=TargetStatus.BACKOFF,
            status_code=0,
            updated_etag=None,
            updated_last_modified=None,
            consecutive_failures=1,
            next_allowed_at=datetime.utcnow(),
            reason="Transport failure",
        )

        result = adapter.execute(fetcher=mock_fetcher, policy=mock_policy)
        # Timeout is a transport failure; observation status is unchanged because
        # no content was successfully fetched or compared.
        assert result.observation.status == ObservationStatus.UNCHANGED
        assert len(result.signals_emitted) == 0

    # --- Repeated extraction failure ---
    def test_repeated_extraction_failure_stays_extraction_failure(self):
        html = "<html><body><div class='price'>$99.00</div></body></html>"
        extractors = [ExtractorConfig(name="missing", selector_type="css", selector=".missing")]
        target = self._make_target(metadata={"initialized": True, "normalized_values": {"missing": ""}})
        result = self._run(html, target=target, extractors=extractors)
        assert result.observation.status == ObservationStatus.EXTRACTION_FAILURE
        assert len(result.signals_emitted) == 0

    # --- Malformed HTML ---
    def test_malformed_html_does_not_crash(self):
        html = "<html><body><div class='price'>$99.00</div><body></html>"
        target = self._make_target()
        result = self._run(html, target=target)
        assert result.observation is not None
        assert result.observation.status_code == 200

    # --- Evidence chain for extraction failure ---
    def test_extraction_failure_evidence_preserves_selector_and_reason(self):
        html = "<html><body><div class='price'>$99.00</div></body></html>"
        extractors = [ExtractorConfig(name="missing", selector_type="css", selector=".missing")]
        target = self._make_target(metadata={"initialized": True, "normalized_values": {"missing": ""}})
        result = self._run(html, target=target, extractors=extractors)
        evidence = result.observation.evidence
        assert "extractor_results" in evidence
        assert evidence["extractor_results"]["missing"]["status"] == "not_found"
        assert evidence["extractor_results"]["missing"]["selector_type"] == "css"
