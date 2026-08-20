"""End-to-end extraction, normalization, fingerprint, diff, and observation tests for GenericWebTarget."""

import hashlib
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from web_watcher.models import Target, TargetStatus
from web_watcher.targets import WatchTarget, validate_watch_target, validate_target_url_policy
from web_watcher.generic_web_target import GenericWebTarget, TargetExecutionResult
from web_watcher.rule_models import ExtractorConfig
from web_watcher.observation import ObservationStatus
from web_watcher.web_fingerprint import observation_fingerprint, selector_config_fingerprint
from web_watcher.normalizer import normalize_extracted_text
from web_watcher.diff import compute_diff


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HTML_A = "<html><body><div class='price'>$10.00</div></body></html>"
HTML_B = "<html><body><div class='price'>$12.00</div></body></html>"
HTML_SAME_FORMATTING = "<html><body>\n  <div class='price'>$10.00</div>\n</body></html>"
HTML_EMPTY_SELECTOR = "<html><body><div class='price'></div></body></html>"
HTML_MALFORMED = "<html><body><div class='price'>$10.00</div><body></html>"  # missing </html>


def _make_target(url: str = "https://example.com", metadata: Optional[dict] = None) -> Target:
    return Target(
        id="web-1",
        url=url,
        interval="15m",
        status=TargetStatus.NORMAL,
        metadata=metadata or {},
    )


def _make_extractor(name: str = "price", selector: str = ".price") -> ExtractorConfig:
    return ExtractorConfig(name=name, selector_type="css", selector=selector)


def _run_execute(html_content: str, target: Optional[Target] = None, extractors=None, metadata=None):
    target = target or _make_target(metadata=metadata)
    extractors = extractors or [_make_extractor()]
    adapter = GenericWebTarget(target=target, extractors=extractors)

    mock_fetcher = MagicMock()
    mock_fetcher.fetch.return_value = MagicMock(
        content=html_content,
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
    mock_policy.prepare_request.return_value = MagicMock(
        allowed=True,
        reason="",
        headers={},
    )
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

    result = adapter.execute(fetcher=mock_fetcher, policy=mock_policy)
    return result


# ---------------------------------------------------------------------------
# 1. same content → unchanged
# ---------------------------------------------------------------------------

class TestSameContentUnchanged:

    def test_identical_html_produces_unchanged(self):
        result = _run_execute(HTML_A, metadata={})
        assert result.observation is not None
        assert result.observation.status == ObservationStatus.FIRST_OBSERVATION

    def test_second_call_with_same_content_is_unchanged(self):
        target = _make_target(metadata={"initialized": True, "normalized_values": {"price": "$10.00"}})
        result = _run_execute(HTML_A, target=target)
        assert result.observation.status == ObservationStatus.UNCHANGED
        assert result.outcome.name == "SUCCESS_UNCHANGED"
        assert len(result.signals_emitted) == 0


# ---------------------------------------------------------------------------
# 2. real content change → changed
# ---------------------------------------------------------------------------

class TestRealContentChange:

    def test_price_change_detected(self):
        target = _make_target(metadata={"initialized": True, "normalized_values": {"price": "$10.00"}})
        result = _run_execute(HTML_B, target=target)
        assert result.observation.status == ObservationStatus.CHANGED
        assert result.outcome.name == "SUCCESS_CHANGED"
        assert len(result.signals_emitted) == 1
        assert result.observation.changed_extractors() == ["price"]
        diff = result.observation.diffs["price"]
        assert diff.changed is True
        assert diff.before == "$10.00"
        assert diff.after == "$12.00"


# ---------------------------------------------------------------------------
# 3. formatting-only change → unchanged
# ---------------------------------------------------------------------------

class TestFormattingOnlyChange:

    def test_whitespace_only_difference_is_unchanged(self):
        target = _make_target(metadata={"initialized": True, "normalized_values": {"price": "$10.00"}})
        result = _run_execute(HTML_SAME_FORMATTING, target=target)
        assert result.observation.status == ObservationStatus.UNCHANGED
        assert len(result.signals_emitted) == 0


# ---------------------------------------------------------------------------
# 4. whitespace variation → unchanged
# ---------------------------------------------------------------------------

class TestWhitespaceVariation:

    def test_extra_spaces_normalized_away(self):
        target = _make_target(metadata={"initialized": True, "normalized_values": {"price": "$10.00"}})
        html = "<html><body><div class='price'>  $10.00  </div></body></html>"
        result = _run_execute(html, target=target)
        assert result.observation.status == ObservationStatus.UNCHANGED

    def test_newlines_normalized_away(self):
        target = _make_target(metadata={"initialized": True, "normalized_values": {"price": "$10.00"}})
        html = "<html><body><div class='price'>\n$10.00\n</div></body></html>"
        result = _run_execute(html, target=target)
        assert result.observation.status == ObservationStatus.UNCHANGED


# ---------------------------------------------------------------------------
# 5. selector extraction
# ---------------------------------------------------------------------------

class TestSelectorExtraction:

    def test_css_selector_extracts_text(self):
        result = _run_execute(HTML_A)
        assert result.extracted_results["price"].is_found
        assert result.extracted_values["price"] == "$10.00"

    def test_multiple_extractors(self):
        html = "<html><body><div class='price'>$10.00</div><h1>Title</h1></body></html>"
        extractors = [
            ExtractorConfig(name="price", selector_type="css", selector=".price"),
            ExtractorConfig(name="title", selector_type="css", selector="h1"),
        ]
        target = _make_target()
        result = _run_execute(html, target=target, extractors=extractors)
        assert result.extracted_results["price"].value == "$10.00"
        assert result.extracted_results["title"].value == "Title"

    def test_missing_selector_yields_not_found(self):
        html = "<html><body><div class='price'>$10.00</div></body></html>"
        extractors = [ExtractorConfig(name="missing", selector_type="css", selector=".missing")]
        target = _make_target()
        result = _run_execute(html, target=target, extractors=extractors)
        assert not result.extracted_results["missing"].is_found


# ---------------------------------------------------------------------------
# 6. first observation
# ---------------------------------------------------------------------------

class TestFirstObservation:

    def test_first_observation_establishes_baseline(self):
        result = _run_execute(HTML_A)
        assert result.observation.status == ObservationStatus.FIRST_OBSERVATION
        assert len(result.signals_emitted) == 0
        assert result.observation.reason == "First successful fetch; baseline established"

    def test_first_observation_sets_initialized_flag(self):
        result = _run_execute(HTML_A)
        assert result.updated_metadata.get("initialized") is True

    def test_first_observation_stores_normalized_values(self):
        result = _run_execute(HTML_A)
        assert "normalized_values" in result.updated_metadata
        assert result.updated_metadata["normalized_values"]["price"] == "$10.00"


# ---------------------------------------------------------------------------
# 7. 304 short-circuit
# ---------------------------------------------------------------------------

class Test304ShortCircuit:

    def test_304_returns_unchanged_without_extraction(self):
        target = _make_target()
        adapter = GenericWebTarget(target=target, extractors=[_make_extractor()])

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = MagicMock(
            content=HTML_A,
            status_code=304,
            etag="\"abc\"",
            last_modified="Wed, 19 Aug 2026 00:00:00 GMT",
            error=None,
            status="not_modified",
            target_key=target.id,
            fetched_at=datetime.now(timezone.utc),
            content_hash=None,
        )

        mock_policy = MagicMock()
        mock_policy.prepare_request.return_value = MagicMock(allowed=True, reason="", headers={})
        mock_policy.evaluate_response.return_value = MagicMock(
            allowed=True,
            should_emit_signal=False,
            new_status=TargetStatus.NORMAL,
            status_code=304,
            updated_etag="\"abc\"",
            updated_last_modified="Wed, 19 Aug 2026 00:00:00 GMT",
            consecutive_failures=0,
            next_allowed_at=None,
            reason="Not Modified",
        )

        result = adapter.execute(fetcher=mock_fetcher, policy=mock_policy)
        assert result.is_304 is True
        assert result.observation.status == ObservationStatus.UNCHANGED
        assert result.observation.status_code == 304
        assert len(result.signals_emitted) == 0
        assert result.observation.reason == "HTTP 304 Not Modified; short-circuited without extraction or fingerprinting"


# ---------------------------------------------------------------------------
# 8. empty extracted content
# ---------------------------------------------------------------------------

class TestEmptyExtractedContent:

    def test_empty_selector_content(self):
        target = _make_target(metadata={"initialized": True, "normalized_values": {"price": ""}})
        result = _run_execute(HTML_EMPTY_SELECTOR, target=target)
        assert result.observation.status == ObservationStatus.UNCHANGED
        assert result.observation.normalized_values["price"] == ""


# ---------------------------------------------------------------------------
# 9. malformed HTML
# ---------------------------------------------------------------------------

class TestMalformedHTML:

    def test_malformed_html_does_not_crash(self):
        target = _make_target()
        result = _run_execute(HTML_MALFORMED, target=target)
        # BeautifulSoup is lenient; extraction should still work or report not_found.
        assert result.observation is not None
        assert result.observation.status_code == 200


# ---------------------------------------------------------------------------
# 10. deterministic fingerprint
# ---------------------------------------------------------------------------

class TestDeterministicFingerprint:

    def test_fingerprint_stable_across_calls(self):
        target = _make_target(metadata={"initialized": True, "normalized_values": {"price": "$10.00"}})
        r1 = _run_execute(HTML_A, target=target)
        r2 = _run_execute(HTML_A, target=target)
        fp1 = r1.observation.fingerprints.get("price", "")
        fp2 = r2.observation.fingerprints.get("price", "")
        assert fp1 == fp2
        assert fp1 != ""

    def test_fingerprint_changes_when_content_changes(self):
        target = _make_target(metadata={"initialized": True, "normalized_values": {"price": "$10.00"}})
        r1 = _run_execute(HTML_A, target=target)
        r2 = _run_execute(HTML_B, target=target)
        fp1 = r1.observation.fingerprints.get("price", "")
        fp2 = r2.observation.fingerprints.get("price", "")
        assert fp1 != fp2


# ---------------------------------------------------------------------------
# 11. process restart fingerprint stability
# ---------------------------------------------------------------------------

class TestFingerprintRestartStability:

    def test_same_observation_same_fingerprint_after_restart(self):
        selector_fp = selector_config_fingerprint("css", ".price")
        fp1 = observation_fingerprint("web-1", "$10.00", selector_fp)
        # Simulate process restart by recomputing from scratch.
        fp2 = observation_fingerprint("web-1", "$10.00", selector_fp)
        assert fp1 == fp2

    def test_fingerprint_independent_of_timestamp(self):
        selector_fp = selector_config_fingerprint("css", ".price")
        fp1 = observation_fingerprint("web-1", "$10.00", selector_fp)
        import time
        time.sleep(0.01)
        fp2 = observation_fingerprint("web-1", "$10.00", selector_fp)
        assert fp1 == fp2


# ---------------------------------------------------------------------------
# 12. before/after diff
# ---------------------------------------------------------------------------

class TestBeforeAfterDiff:

    def test_diff_preserves_before_and_after(self):
        target = _make_target(metadata={"initialized": True, "normalized_values": {"price": "$10.00"}})
        result = _run_execute(HTML_B, target=target)
        diff = result.observation.diffs["price"]
        assert diff.before == "$10.00"
        assert diff.after == "$12.00"

    def test_diff_summary_present(self):
        target = _make_target(metadata={"initialized": True, "normalized_values": {"price": "$10.00"}})
        result = _run_execute(HTML_B, target=target)
        diff = result.observation.diffs["price"]
        assert diff.summary != ""
        assert "Changed:" in diff.summary

    def test_diff_regions_present(self):
        target = _make_target(metadata={"initialized": True, "normalized_values": {"price": "$10.00"}})
        result = _run_execute(HTML_B, target=target)
        diff = result.observation.diffs["price"]
        assert "before_len=6" in diff.regions
        assert "after_len=6" in diff.regions


# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------

class TestNegativeCases:

    def test_unknown_target_type_rejected(self):
        target = WatchTarget(
            key="unknown:x",
            target_type="twitter_account",
            name="X",
            locator="https://example.com",
        )
        with pytest.raises(ValueError, match="unsupported target type"):
            validate_watch_target(target)

    def test_invalid_url_rejected(self):
        target = WatchTarget(
            key="web:bad",
            target_type="web",
            name="Bad",
            locator="ftp://example.com",
        )
        with pytest.raises(ValueError, match="scheme"):
            validate_target_url_policy(target)

    def test_generic_web_target_construction_validates_url(self):
        target = _make_target(url="ftp://example.com")
        with pytest.raises(ValueError, match="scheme"):
            GenericWebTarget(target=target)

    def test_generic_web_target_construction_validates_selector(self):
        target = _make_target()
        extractors = [ExtractorConfig(name="bad", selector_type="jsonpath", selector="$.[0]")]
        with pytest.raises(ValueError, match="selector type"):
            GenericWebTarget(target=target, extractors=extractors)

    def test_all_extractors_failed_yields_extraction_failure(self):
        html = "<html><body><div class='price'>$10.00</div></body></html>"
        extractors = [
            ExtractorConfig(name="missing1", selector_type="css", selector=".missing1"),
            ExtractorConfig(name="missing2", selector_type="css", selector=".missing2"),
        ]
        target = _make_target(metadata={"initialized": True, "normalized_values": {}})
        result = _run_execute(html, target=target, extractors=extractors)
        assert result.observation.status == ObservationStatus.EXTRACTION_FAILURE
        assert len(result.signals_emitted) == 0

    def test_partial_selector_failure_does_not_emit_signal(self):
        html = "<html><body><div class='price'>$10.00</div></body></html>"
        extractors = [
            ExtractorConfig(name="price", selector_type="css", selector=".price"),
            ExtractorConfig(name="missing", selector_type="css", selector=".missing"),
        ]
        previous_values = {
            "price": "10.00",
            "missing": "old",
        }
        target = _make_target(metadata={"initialized": True, "normalized_values": previous_values})
        result = _run_execute(html, target=target, extractors=extractors)
        assert result.observation.status == ObservationStatus.EXTRACTION_FAILURE
        assert len(result.signals_emitted) == 0
        assert "Partial selector failure" in result.observation.reason

    def test_first_observation_never_emits_signal(self):
        result = _run_execute(HTML_A)
        assert len(result.signals_emitted) == 0
        assert result.observation.status == ObservationStatus.FIRST_OBSERVATION
