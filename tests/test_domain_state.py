from datetime import datetime, timezone

from web_watcher.models import Entity, Signal, Event, Notification, FetchState
from web_watcher.repository import Repository


def test_domain_models_are_constructible():
    now = datetime.now(timezone.utc)

    entity = Entity(
        id=1,
        canonical_key="github:openai/example",
        name="example",
        entity_type="github_repository",
    )

    signal = Signal(
        id=1,
        entity_id=1,
        signal_type="release",
        observed_at=now,
        fingerprint="release:v1.0.0",
    )

    event = Event(
        id=1,
        entity_id=1,
        event_type="major_release",
        status="new",
        importance="important",
        created_at=now,
        updated_at=now,
    )

    notification = Notification(
        id=1,
        event_id=1,
        channel="telegram",
        status="pending",
        created_at=now,
    )

    fetch_state = FetchState(
        target_key="github:openai/example",
        etag="abc",
    )

    assert entity.entity_type == "github_repository"
    assert signal.signal_type == "release"
    assert event.importance == "important"
    assert notification.status == "pending"
    assert fetch_state.etag == "abc"


def test_entity_can_be_created_and_retrieved(tmp_path):
    repo = Repository(tmp_path / "watcher.db")

    created = repo.create_entity(
        canonical_key="github:example/project",
        name="project",
        entity_type="github_repository",
    )

    found = repo.get_entity_by_key("github:example/project")

    assert created.id is not None
    assert found is not None
    assert found.id == created.id
    assert found.canonical_key == created.canonical_key
    assert found.name == created.name
    assert found.entity_type == created.entity_type

    repo.close()


def test_entity_key_is_unique(tmp_path):
    repo = Repository(tmp_path / "watcher.db")

    repo.create_entity(
        canonical_key="github:example/project",
        name="project",
        entity_type="github_repository",
    )

    try:
        repo.create_entity(
            canonical_key="github:example/project",
            name="project",
            entity_type="github_repository",
        )
    except Exception as exc:
        assert "UNIQUE" in str(exc).upper()
    else:
        raise AssertionError("Duplicate canonical key was accepted")

    repo.close()


def test_schema_contains_core_tables(tmp_path):
    repo = Repository(tmp_path / "watcher.db")

    rows = repo.connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        """
    ).fetchall()

    tables = {row[0] for row in rows}

    expected = {
        "entities",
        "signals",
        "events",
        "event_signals",
        "notifications",
        "fetch_state",
    }

    assert expected.issubset(tables)

    repo.close()