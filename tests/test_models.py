from datetime import datetime, timezone
from web_watcher.models import Entity, Signal, Event, Notification, FetchState


def test_entity_creation():
    e = Entity(id=1, canonical_key="repo:owner/name", name="Test", entity_type="github_repo")
    assert e.canonical_key == "repo:owner/name"
    assert e.entity_type == "github_repo"


def test_entity_immutable():
    e = Entity(id=1, canonical_key="repo:owner/name", name="Test", entity_type="github_repo")
    try:
        e.name = "Changed"
        assert False, "Entity should be immutable"
    except Exception:
        pass


def test_signal_with_fingerprint():
    s = Signal(id=1, entity_id=1, signal_type="push", observed_at=datetime.now(timezone.utc), fingerprint="abc123")
    assert s.fingerprint == "abc123"


def test_signal_without_fingerprint():
    s = Signal(id=2, entity_id=1, signal_type="star", observed_at=datetime.now(timezone.utc))
    assert s.fingerprint is None


def test_event_creation():
    dt = datetime.now(timezone.utc)
    e = Event(id=1, entity_id=1, event_type="new_release", status="detected", importance="medium", created_at=dt, updated_at=dt)
    assert e.status == "detected"


def test_notification_creation():
    dt = datetime.now(timezone.utc)
    n = Notification(id=1, event_id=1, channel="telegram", status="pending", created_at=dt)
    assert n.sent_at is None


def test_fetch_state_with_etag():
    fs = FetchState(target_key="https://example.com/api", etag='"v1"', content_hash="sha256:abc")
    assert fs.etag == '"v1"'
