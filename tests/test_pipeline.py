"""Tests for the unified pipeline enforcing the causal chain:

    Fetch → Observation → Signal → Event → Investigation → Policy → Notification
"""

import pytest
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from web_watcher.event_correlator import EventCorrelator
from web_watcher.event_status import EventStatus
from web_watcher.event_types import EventType
from web_watcher.importance import Importance
from web_watcher.investigation_adapter import EventInvestigationAdapter
from web_watcher.models import Entity, Event, Signal, Target
from web_watcher.notification_dispatcher import NotificationDispatcher
from web_watcher.notification_enricher import NotificationEnricher
from web_watcher.pipeline import UnifiedPipeline, Observation, PipelineResult
from web_watcher.repository import Repository
from web_watcher.signal_types import SignalType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entity(repo: Repository, canonical_key: str = "pipeline-1") -> Entity:
    return repo.create_entity(canonical_key=canonical_key, name=canonical_key, entity_type="service")


def _make_signal(entity_id: int, signal_type: SignalType = SignalType.CONTENT_CHANGE) -> Signal:
    now = datetime.now(timezone.utc)
    return Signal(
        id=entity_id,
        entity_id=entity_id,
        signal_type=signal_type,
        observed_at=now,
        value="change",
        fingerprint=f"fp-{entity_id}",
    )


def _make_observation(target_id: str, signals: Optional[List[Signal]] = None) -> Observation:
    now = datetime.now(timezone.utc)
    return Observation(
        target_id=target_id,
        target_type="generic_web",
        status_code=200,
        observed_at=now,
        outcome="success_changed",
        signals=signals or [],
        evidence={"before": "old", "after": "new", "diff": "diff"},
        metadata={"url": "https://example.com"},
    )


# ---------------------------------------------------------------------------
# Core causal chain tests
# ---------------------------------------------------------------------------

class TestUnifiedPipelineCausalChain:

    def test_signal_leads_to_event(self):
        repo = Repository(":memory:")
        entity = _make_entity(repo, "chain-1")
        sig = _make_signal(entity.id, SignalType.CONTENT_CHANGE)
        obs = _make_observation("t1", signals=[sig])

        pipeline = UnifiedPipeline(repository=repo)
        result = pipeline.process_observation(obs)

        assert result.event is not None
        assert result.event.event_type == EventType.CONTENT_CHANGE
        assert result.event.status == EventStatus.OPEN

    def test_event_leads_to_investigation_when_eligible(self):
        repo = Repository(":memory:")
        entity = _make_entity(repo, "chain-2")
        sig = _make_signal(entity.id, SignalType.CONTENT_CHANGE)
        obs = _make_observation("t2", signals=[sig])

        investigation_adapter = EventInvestigationAdapter(min_importance=Importance.INTERESTING)
        mock_planner = MagicMock()
        mock_engine = MagicMock()
        mock_result = MagicMock()
        mock_result.status = "completed"
        mock_result.summary = "Investigation completed"
        mock_result.findings = ()
        mock_result.evidence = ()
        mock_result.confidence = 0.9
        mock_result.steps_used = 1
        mock_result.pages_checked = 1
        mock_result.failure_reason = ""
        mock_result.metadata = {}
        mock_engine.execute.return_value = mock_result
        pipeline = UnifiedPipeline(
            repository=repo,
            auto_investigate=True,
            investigation_adapter=investigation_adapter,
            planner=mock_planner,
            engine=mock_engine,
        )
        result = pipeline.process_observation(obs)

        # CONTENT_CHANGE with IMPORTANT importance should trigger investigation
        # (INTERESTING threshold includes IMPORTANT and CRITICAL)
        assert result.investigation_dispatched is True

    def test_event_leads_to_notification_when_enabled(self):
        repo = Repository(":memory:")
        entity = _make_entity(repo, "chain-3")
        sig = _make_signal(entity.id, SignalType.STARS_CHANGED)
        obs = _make_observation("t3", signals=[sig])

        pipeline = UnifiedPipeline(
            repository=repo,
            auto_notify=True,
            notify_channel="webhook",
        )
        result = pipeline.process_observation(obs)

        assert result.notification is not None
        assert result.notification.channel == "webhook"
        assert result.notification.status == "pending"

    def test_notification_is_final_layer_only(self):
        """Notification must not modify target state, event semantics, or observation semantics."""
        repo = Repository(":memory:")
        entity = _make_entity(repo, "chain-4")
        sig = _make_signal(entity.id, SignalType.RELEASE_PUBLISHED)
        obs = _make_observation("t4", signals=[sig])

        pipeline = UnifiedPipeline(
            repository=repo,
            auto_notify=True,
            notify_channel="webhook",
        )
        result = pipeline.process_observation(obs)

        # Notification should not have modified the original observation
        assert obs.signals == [sig]
        assert obs.evidence == {"before": "old", "after": "new", "diff": "diff"}
        assert result.event is not None
        assert result.notification is not None

    def test_full_chain_enforcement(self):
        """All steps must complete: Signal → Event → Investigation → Notification."""
        repo = Repository(":memory:")
        entity = _make_entity(repo, "chain-5")
        sig = _make_signal(entity.id, SignalType.CONTENT_CHANGE)
        obs = _make_observation("t5", signals=[sig])

        investigation_adapter = EventInvestigationAdapter(min_importance=Importance.INTERESTING)
        mock_planner = MagicMock()
        mock_engine = MagicMock()
        mock_result = MagicMock()
        mock_result.status = "completed"
        mock_result.summary = "Investigation completed"
        mock_result.findings = ()
        mock_result.evidence = ()
        mock_result.confidence = 0.9
        mock_result.steps_used = 1
        mock_result.pages_checked = 1
        mock_result.failure_reason = ""
        mock_result.metadata = {}
        mock_engine.execute.return_value = mock_result
        pipeline = UnifiedPipeline(
            repository=repo,
            auto_investigate=True,
            auto_notify=True,
            notify_channel="webhook",
            investigation_adapter=investigation_adapter,
            planner=mock_planner,
            engine=mock_engine,
        )
        result = pipeline.process_observation(obs)

        assert result.signals_emitted == [sig]
        assert result.event is not None
        assert result.investigation_dispatched is True
        assert result.notification is not None


# ---------------------------------------------------------------------------
# Suppression window tests
# ---------------------------------------------------------------------------

class TestSuppressionWindow:

    def test_suppressed_within_window(self):
        repo = Repository(":memory:")
        entity = _make_entity(repo, "suppress-1")
        sig1 = _make_signal(entity.id, SignalType.CONTENT_CHANGE)
        obs1 = _make_observation("suppress-1", signals=[sig1])

        pipeline = UnifiedPipeline(repository=repo, suppression_window_seconds=3600)
        result1 = pipeline.process_observation(obs1)
        assert result1.event is not None
        assert result1.suppressed is False

        # Second signal within window should be suppressed
        sig2 = _make_signal(entity.id, SignalType.CONTENT_CHANGE)
        obs2 = _make_observation("suppress-1", signals=[sig2])
        result2 = pipeline.process_observation(obs2)
        assert result2.suppressed is True
        assert "Suppressed" in (result2.suppression_reason or "")

    def test_not_suppressed_outside_window(self):
        repo = Repository(":memory:")
        entity = _make_entity(repo, "suppress-2")
        sig1 = _make_signal(entity.id, SignalType.CONTENT_CHANGE)
        obs1 = _make_observation("suppress-2", signals=[sig1])

        pipeline = UnifiedPipeline(repository=repo, suppression_window_seconds=1)
        result1 = pipeline.process_observation(obs1)
        assert result1.event is not None
        assert result1.suppressed is False

        # Wait for window to expire
        import time
        time.sleep(1.1)

        sig2 = _make_signal(entity.id, SignalType.CONTENT_CHANGE)
        obs2 = _make_observation("suppress-2", signals=[sig2])
        result2 = pipeline.process_observation(obs2)
        assert result2.suppressed is False

    def test_different_event_types_not_suppressed(self):
        repo = Repository(":memory:")
        entity = _make_entity(repo, "suppress-3")
        sig1 = _make_signal(entity.id, SignalType.CONTENT_CHANGE)
        obs1 = _make_observation("suppress-3", signals=[sig1])

        pipeline = UnifiedPipeline(repository=repo, suppression_window_seconds=3600)
        result1 = pipeline.process_observation(obs1)
        assert result1.event is not None
        assert result1.suppressed is False

        # Different event type should not be suppressed
        sig2 = _make_signal(entity.id, SignalType.STARS_CHANGED)
        obs2 = _make_observation("suppress-3", signals=[sig2])
        result2 = pipeline.process_observation(obs2)
        assert result2.suppressed is False


# ---------------------------------------------------------------------------
# Evidence propagation tests
# ---------------------------------------------------------------------------

class TestEvidencePropagation:

    def test_observation_evidence_propagates_to_notification(self):
        repo = Repository(":memory:")
        entity = _make_entity(repo, "evidence-1")
        sig = _make_signal(entity.id, SignalType.CONTENT_CHANGE)
        obs = Observation(
            target_id="evidence-1",
            target_type="generic_web",
            status_code=200,
            observed_at=datetime.now(timezone.utc),
            outcome="success_changed",
            signals=[sig],
            evidence={
                "before": "old content",
                "after": "new content",
                "diff": "+new line\n-old line",
                "fingerprint": "abc123",
                "selector": ".content",
                "timestamp": "2026-08-19T12:00:00Z",
            },
            metadata={"url": "https://example.com"},
        )

        pipeline = UnifiedPipeline(
            repository=repo,
            auto_notify=True,
            notify_channel="webhook",
        )
        result = pipeline.process_observation(obs)

        assert result.notification is not None
        payload = result.notification.payload
        assert "observation" in payload
        assert payload["observation"]["evidence"]["before"] == "old content"
        assert payload["observation"]["evidence"]["after"] == "new content"
        assert payload["observation"]["evidence"]["diff"] == "+new line\n-old line"

    def test_observation_metadata_propagates_to_notification(self):
        repo = Repository(":memory:")
        entity = _make_entity(repo, "evidence-2")
        sig = _make_signal(entity.id, SignalType.CONTENT_CHANGE)
        obs = Observation(
            target_id="evidence-2",
            target_type="generic_web",
            status_code=200,
            observed_at=datetime.now(timezone.utc),
            outcome="success_changed",
            signals=[sig],
            evidence={"before": "old", "after": "new", "diff": "diff"},
            metadata={
                "url": "https://example.com/page",
                "target_type": "generic_web",
                "extractor_results": {
                    "title": {"status": "found", "raw_value": "Old Title", "normalized_value": "Old Title"}
                },
            },
        )

        pipeline = UnifiedPipeline(
            repository=repo,
            auto_notify=True,
            notify_channel="webhook",
        )
        result = pipeline.process_observation(obs)

        assert result.notification is not None
        payload = result.notification.payload
        assert payload["observation"]["metadata"]["url"] == "https://example.com/page"
        assert "extractor_results" in payload["observation"]["metadata"]


# ---------------------------------------------------------------------------
# No bypass tests
# ---------------------------------------------------------------------------

class TestNoBypass:

    def test_signal_without_event_has_no_notification(self):
        repo = Repository(":memory:")
        # No entity, so signal cannot be correlated
        sig = Signal(id=1, entity_id=99999, signal_type=SignalType.CONTENT_CHANGE,
                     observed_at=datetime.now(timezone.utc), value="x", fingerprint="fp")
        obs = _make_observation("bypass-1", signals=[sig])

        pipeline = UnifiedPipeline(
            repository=repo,
            auto_notify=True,
            notify_channel="webhook",
        )
        result = pipeline.process_observation(obs)

        # Signal exists but no event could be created, so no notification
        assert result.signals_emitted == [sig]
        assert result.event is None
        assert result.notification is None

    def test_event_without_investigation_has_no_enriched_notification(self):
        repo = Repository(":memory:")
        entity = _make_entity(repo, "bypass-2")
        sig = _make_signal(entity.id, SignalType.STARS_CHANGED)
        obs = _make_observation("bypass-2", signals=[sig])

        # Min importance is CRITICAL, STARS_CHANGED is INTERESTING
        investigation_adapter = EventInvestigationAdapter(min_importance=Importance.CRITICAL)
        pipeline = UnifiedPipeline(
            repository=repo,
            auto_investigate=True,
            auto_notify=True,
            notify_channel="webhook",
            investigation_adapter=investigation_adapter,
        )
        result = pipeline.process_observation(obs)

        # Event created but no investigation dispatched
        assert result.event is not None
        assert result.investigation_dispatched is False
        # Notification still created but without investigation
        assert result.notification is not None
        assert result.notification.payload.get("has_investigation") is False

    def test_notification_without_event_has_no_delivery(self):
        repo = Repository(":memory:")
        # No signals → no event → no notification
        obs = _make_observation("bypass-3", signals=[])

        pipeline = UnifiedPipeline(
            repository=repo,
            auto_notify=True,
            auto_deliver=True,
            notify_channel="webhook",
        )
        result = pipeline.process_observation(obs)

        assert result.notification is None
        assert result.delivery_result is None


# ---------------------------------------------------------------------------
# Batch processing tests
# ---------------------------------------------------------------------------

class TestBatchProcessing:

    def test_batch_observations_all_processed(self):
        repo = Repository(":memory:")
        entities = [_make_entity(repo, f"batch-{i}") for i in range(3)]
        observations = [
            _make_observation(f"batch-{i}", signals=[_make_signal(entities[i].id, SignalType.CONTENT_CHANGE)])
            for i in range(3)
        ]

        pipeline = UnifiedPipeline(
            repository=repo,
            auto_notify=True,
            notify_channel="webhook",
        )
        results = pipeline.run_batch(observations)

        assert len(results) == 3
        for i, result in enumerate(results):
            assert result.target_id == f"batch-{i}"
            assert result.event is not None
            assert result.notification is not None

    def test_batch_with_mixed_signals(self):
        repo = Repository(":memory:")
        entity = _make_entity(repo, "batch-mixed")
        sig1 = _make_signal(entity.id, SignalType.CONTENT_CHANGE)
        sig2 = _make_signal(entity.id, SignalType.STARS_CHANGED)
        obs = _make_observation("batch-mixed", signals=[sig1, sig2])

        pipeline = UnifiedPipeline(repository=repo, auto_notify=True, notify_channel="webhook")
        results = pipeline.run_batch([obs])

        assert len(results) == 1
        result = results[0]
        assert len(result.signals_emitted) == 2
        assert result.event is not None
