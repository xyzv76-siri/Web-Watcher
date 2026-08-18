"""End-to-end pipeline tests for Phase 12-C final integration."""

from datetime import datetime, timezone

from web_watcher.channel_senders import BaseChannelSender, DeliveryResult
from web_watcher.event_correlator import EventCorrelator
from web_watcher.event_types import EventType
from web_watcher.importance import Importance
from web_watcher.models import Signal
from web_watcher.notification_dispatcher import NotificationDispatcher
from web_watcher.notification_enricher import NotificationEnricher
from web_watcher.pipeline_runner import PipelineRunner
from web_watcher.repository import Repository
from web_watcher.signal_types import SignalType


class DummySuccessSender(BaseChannelSender):
    def send(self, notification):
        return DeliveryResult(success=True, status_code=200, response_body="ok")


def test_end_to_end_pipeline_creates_event_and_notification():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="e2e-1", name="E2E App", entity_type="service")
    now = datetime.now(timezone.utc)
    signal = Signal(id=1, entity_id=entity.id, signal_type=SignalType.CONTENT_CHANGE, observed_at=now, value="diff", fingerprint="fp-e2e-1")

    runner = PipelineRunner(repository=repo, auto_notify=True, notify_channel="webhook")
    result = runner.process_signal(signal)

    assert result["signal_id"] == signal.id
    assert result["event"] is not None
    assert result["event"].event_type == EventType.CONTENT_CHANGE
    assert result["event"].importance == Importance.IMPORTANT
    assert result["notification"] is not None
    assert result["notification"].channel == "webhook"
    assert result["notification"].payload["has_investigation"] is False


def test_pipeline_auto_notify_disabled_skips_notification():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="e2e-2", name="E2E App 2", entity_type="service")
    now = datetime.now(timezone.utc)
    signal = Signal(id=1, entity_id=entity.id, signal_type=SignalType.STARS_CHANGED, observed_at=now, value="120", fingerprint="fp-e2e-2")

    runner = PipelineRunner(repository=repo, auto_notify=False)
    result = runner.process_signal(signal)

    assert result["event"] is not None
    assert result["notification"] is None


def test_pipeline_batch_processing_returns_all_results():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="e2e-3", name="E2E App 3", entity_type="service")
    now = datetime.now(timezone.utc)
    signals = [
        Signal(id=1, entity_id=entity.id, signal_type=SignalType.CONTENT_CHANGE, observed_at=now, value="a", fingerprint="fp-a"),
        Signal(id=2, entity_id=entity.id, signal_type=SignalType.CONTENT_CHANGE, observed_at=now, value="b", fingerprint="fp-b"),
        Signal(id=3, entity_id=entity.id, signal_type=SignalType.CONTENT_CHANGE, observed_at=now, value="c", fingerprint="fp-c"),
    ]

    runner = PipelineRunner(repository=repo, auto_notify=True)
    results = runner.run_batch_signals(signals)

    assert len(results) == 3
    for res in results:
        assert res["event"] is not None
        assert res["notification"] is not None


def test_pipeline_enriched_notification_includes_investigation():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="e2e-4", name="E2E App 4", entity_type="service")
    event = repo.create_event(entity_id=entity.id, event_type=EventType.CONTENT_CHANGE, importance=Importance.CRITICAL)

    repo.save_investigation_result(
        investigation_id="inv_e2e",
        event_id=event.id,
        task_type="diff_analysis",
        status="completed",
        summary="Critical drift detected",
        evidence_items=[{"evidence_type": "delta", "payload": {"lines": 5}}],
    )

    runner = PipelineRunner(repository=repo, auto_notify=True)
    signal = Signal(id=1, entity_id=entity.id, signal_type=SignalType.CONTENT_CHANGE, observed_at=datetime.now(timezone.utc), value="x")
    result = runner.process_signal(signal)

    assert result["notification"] is not None
    payload = result["notification"].payload
    assert payload["has_investigation"] is True
    assert payload["investigation"]["summary"] == "Critical drift detected"


def test_pipeline_auto_deliver_dispatches_notification():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="e2e-5", name="E2E App 5", entity_type="service")
    now = datetime.now(timezone.utc)
    signal = Signal(id=1, entity_id=entity.id, signal_type=SignalType.CONTENT_CHANGE, observed_at=now, value="change", fingerprint="fp-deliver")

    dispatcher = NotificationDispatcher(repository=repo, default_sender=DummySuccessSender())
    runner = PipelineRunner(
        repository=repo,
        auto_notify=True,
        auto_deliver=True,
        notify_channel="webhook",
        dispatcher=dispatcher,
    )
    result = runner.process_signal(signal)

    assert result["notification"] is not None
    assert result["delivery_result"] is not None
    assert result["delivery_result"].success is True
    assert result["notification"].status == "delivered"


def test_pipeline_injected_dependencies_flow_through():
    repo = Repository(":memory:")
    entity = repo.create_entity(canonical_key="e2e-6", name="E2E App 6", entity_type="service")
    correlator = EventCorrelator(repository=repo, auto_investigate=False)
    enricher = NotificationEnricher(repository=repo)
    dispatcher = NotificationDispatcher(repository=repo, default_sender=DummySuccessSender())

    runner = PipelineRunner(
        repository=repo,
        correlator=correlator,
        enricher=enricher,
        dispatcher=dispatcher,
        auto_notify=True,
        auto_deliver=True,
        notify_channel="slack",
    )
    signal = Signal(id=1, entity_id=entity.id, signal_type=SignalType.CONTENT_CHANGE, observed_at=datetime.now(timezone.utc), value="y")
    result = runner.process_signal(signal)

    assert result["event"] is not None
    assert result["notification"] is not None
    assert result["notification"].channel == "slack"
    assert result["delivery_result"] is not None
    assert result["delivery_result"].success is True
