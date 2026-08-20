"""Tests for ObservationResult model."""

import pytest
from datetime import datetime, timezone
from web_watcher.observation import ObservationResult, ObservationStatus
from web_watcher.diff import DiffResult
from web_watcher.rule_models import ExtractionResult, ExtractionStatus


class TestObservationResult:

    def test_summary_contains_key_fields(self):
        obs = ObservationResult(
            target_id="web:example",
            status=ObservationStatus.CHANGED,
            status_code=200,
            reason="Content changed",
            observed_at=datetime(2026, 8, 19, 12, 0, 0),
        )
        s = obs.summary()
        assert s["target_id"] == "web:example"
        assert s["status"] == ObservationStatus.CHANGED
        assert s["status_code"] == 200
        assert s["reason"] == "Content changed"

    def test_changed_extractors_reports_changed_names(self):
        diff1 = DiffResult.changed("a", "b", summary="a->b")
        diff2 = DiffResult.unchanged("c", "c")
        obs = ObservationResult(
            target_id="web:example",
            status=ObservationStatus.CHANGED,
            diffs={"price": diff1, "title": diff2},
        )
        assert obs.changed_extractors() == ["price"]

    def test_first_observation_status(self):
        obs = ObservationResult(
            target_id="web:example",
            status=ObservationStatus.FIRST_OBSERVATION,
        )
        assert obs.is_first_observation() is True
        assert obs.is_changed() is False

    def test_extraction_failure_status(self):
        obs = ObservationResult(
            target_id="web:example",
            status=ObservationStatus.EXTRACTION_FAILURE,
            extracted_results={
                "price": ExtractionResult(status=ExtractionStatus.SELECTOR_NOT_FOUND)
            },
        )
        assert obs.has_extraction_failures() is True
        assert obs.is_changed() is False

    def test_http_failure_status(self):
        obs = ObservationResult(
            target_id="web:example",
            status=ObservationStatus.HTTP_FAILURE,
            status_code=500,
        )
        assert obs.has_http_failure() is True

    def test_unchanged_status(self):
        obs = ObservationResult(
            target_id="web:example",
            status=ObservationStatus.UNCHANGED,
        )
        assert obs.is_unchanged() is True

    def test_evidence_chain_serializable(self):
        obs = ObservationResult(
            target_id="web:example",
            status=ObservationStatus.CHANGED,
            evidence={
                "target_id": "web:example",
                "url": "https://example.com",
                "extractor_results": {
                    "price": {
                        "status": "found",
                        "normalized_value": "$10",
                        "changed": True,
                    }
                },
            },
        )
        # Evidence should be serializable as JSON-compatible dict.
        import json
        json.dumps(obs.evidence)
