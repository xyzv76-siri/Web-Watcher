"""Phase 8 — Event Correlation tests.

Deterministic only. No AI, LLM, network, or wall-clock dependencies.
Tests verify:
    1. Event creation via repository
    2. Signal attachment to Event
    3. find_open_event_for_entity with/without time cutoff
    4. Correlation: same entity, within window → joins Event
    5. Correlation: same entity, outside window → new Event
    6. Correlation: different entities → separate Events
    7. Closed Event must not receive new Signals
    8. Configurable correlation window
    9. Deterministic event_type derivation
    10. Correlator.injectable clock (no real wall-clock time)
    11. Existing Phase 7 tests remain green
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from web_watcher.event_correlator import (
    CorrelationConfig,
    EventCorrelator,
    _derive_event_type,
)
from web_watcher.models import Entity, Event, Signal
from web_watcher.repository import Repository
from web_watcher.event_status import EventStatus
from web_watcher.event_types import EventType
from web_watcher.importance import Importance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(year=2026, month=8, day=17, hour=10, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def _sig(
    id_=1,
    entity_id=1,
    signal_type="content_change",
    observed_at=None,
    value=None,
    fingerprint=None,
):
    return Signal(
        id=id_,
        entity_id=entity_id,
        signal_type=signal_type,
        observed_at=observed_at or _ts(),
        value=value or "hash-a",
        fingerprint=fingerprint or "fp-a",
    )


def _entity(repo, key="github:octocat/Hello-World"):
    return repo.get_or_create_entity(key, "Hello-World", "github_repository")


def _entity_id(repo, key="github:octocat/Hello-World"):
    return _entity(repo, key).id


# ===========================================================================
# A. Repository event operations
# ===========================================================================


class TestRepositoryEvents:

    def test_create_event_returns_event(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        ev = repo.create_event(
            entity_id=eid,
            event_type=EventType.CONTENT_CHANGE,
            status=EventStatus.OPEN,
            importance=Importance.INTERESTING,
            created_at=_ts(2026, 8, 17, 10),
        )
        assert ev.id is not None
        assert ev.entity_id == eid
        assert ev.event_type == EventType.CONTENT_CHANGE
        assert ev.status == EventStatus.OPEN
        assert ev.importance == Importance.INTERESTING
        repo.close()

    def test_get_event_returns_existing(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        created = repo.create_event(
            entity_id=eid, event_type="t", status="open", created_at=_ts()
        )
        fetched = repo.get_event(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.entity_id == eid
        repo.close()

    def test_get_event_missing_returns_none(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        assert repo.get_event(999) is None
        repo.close()

    def test_update_event_changes_status(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        ev = repo.create_event(entity_id=eid, event_type="t", created_at=_ts())
        updated = repo.update_event(ev.id, status="closed", updated_at=_ts(2026, 8, 17, 11))
        assert updated is not None
        assert updated.status == "closed"
        assert updated.updated_at == _ts(2026, 8, 17, 11)
        repo.close()

    def test_update_event_missing_returns_none(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        assert repo.update_event(999, status="closed") is None
        repo.close()

    def test_attach_signal_to_event(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        ev = repo.create_event(entity_id=eid, event_type="t", created_at=_ts())
        sig = repo.create_signal(
            entity_id=eid,
            signal_type="content_change",
            observed_at=_ts(),
            value="h",
            fingerprint="fp-attach",
        )
        assert sig is not None
        result = repo.attach_signal_to_event(ev.id, sig.id)
        assert result is True
        signals = repo.get_event_signals(ev.id)
        assert len(signals) == 1
        assert signals[0].id == sig.id
        repo.close()

    def test_attach_signal_to_event_duplicate_returns_false(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        ev = repo.create_event(entity_id=eid, event_type="t", created_at=_ts())
        sig = repo.create_signal(
            entity_id=eid, signal_type="content_change",
            observed_at=_ts(), value="h", fingerprint="fp-dup",
        )
        assert sig is not None
        assert repo.attach_signal_to_event(ev.id, sig.id) is True
        assert repo.attach_signal_to_event(ev.id, sig.id) is False
        repo.close()

    def test_get_event_signals_ordered(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        ev = repo.create_event(entity_id=eid, event_type="t", created_at=_ts())
        s1 = repo.create_signal(eid, "a", _ts(2026, 8, 17, 9), value="x", fingerprint="fp-1")
        s2 = repo.create_signal(eid, "a", _ts(2026, 8, 17, 11), value="y", fingerprint="fp-2")
        repo.attach_signal_to_event(ev.id, s1.id)
        repo.attach_signal_to_event(ev.id, s2.id)

        signals = repo.get_event_signals(ev.id)
        assert len(signals) == 2
        assert signals[0].observed_at < signals[1].observed_at
        repo.close()

    def test_find_open_event_for_entity(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        ev = repo.create_event(
            entity_id=eid, event_type="t", status="open", created_at=_ts(2026, 8, 17, 10)
        )
        found = repo.find_open_event_for_entity(entity_id=eid)
        assert found is not None
        assert found.id == ev.id
        repo.close()

    def test_find_open_event_respects_cutoff(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        old_ev = repo.create_event(
            entity_id=eid, event_type="t", status="open", created_at=_ts(2026, 8, 1, 0)
        )
        new_ev = repo.create_event(
            entity_id=eid, event_type="t", status="open", created_at=_ts(2026, 8, 17, 10)
        )
        cutoff = _ts(2026, 8, 10, 0)

        found = repo.find_open_event_for_entity(entity_id=eid, cutoff=cutoff)
        assert found is not None
        assert found.id == new_ev.id
        assert found.id != old_ev.id
        repo.close()

    def test_find_open_event_skips_closed(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        closed = repo.create_event(
            entity_id=eid, event_type="t", status="closed", created_at=_ts()
        )
        open_ = repo.create_event(
            entity_id=eid, event_type="t", status="open", created_at=_ts(2026, 8, 17, 11)
        )
        found = repo.find_open_event_for_entity(entity_id=eid)
        assert found is not None
        assert found.id == open_.id
        assert found.id != closed.id
        repo.close()

    def test_find_open_event_different_entity(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid_a = _entity_id(repo, "github:a/repo")
        eid_b = _entity_id(repo, "github:b/repo")
        ev_a = repo.create_event(
            entity_id=eid_a, event_type="t", status="open", created_at=_ts()
        )
        ev_b = repo.create_event(
            entity_id=eid_b, event_type="t", status="open", created_at=_ts()
        )
        found = repo.find_open_event_for_entity(entity_id=eid_b)
        assert found is not None
        assert found.id == ev_b.id
        repo.close()

    def test_find_open_event_no_match_returns_none(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        ev = repo.create_event(
            entity_id=eid, event_type="t", status="open", created_at=_ts(2026, 1, 1)
        )
        found = repo.find_open_event_for_entity(
            entity_id=eid, cutoff=_ts(2026, 8, 1)
        )
        assert found is None
        repo.close()


# ===========================================================================
# B. Correlation — same entity, within window → joins Event
# ===========================================================================


class TestCorrelationWithinWindow:

    def test_two_signals_same_entity_within_window_join_one_event(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        fixed_now = _ts(2026, 8, 17, 12)
        config = CorrelationConfig(correlation_window_seconds=24 * 3600)
        correlator = EventCorrelator(
            repository=repo,
            config=config,
            now_factory=lambda: fixed_now,
        )

        sig1 = _sig(id_=1, entity_id=eid, observed_at=_ts(2026, 8, 17, 10), fingerprint="fp-1")
        sig2 = _sig(id_=2, entity_id=eid, observed_at=_ts(2026, 8, 17, 11), fingerprint="fp-2")

        plan1 = correlator.process_signal(sig1)
        repo.commit_plan(plan1)
        plan2 = correlator.process_signal(sig2)
        repo.commit_plan(plan2)

        assert len(plan1.events_to_create) == 1
        # Second signal joins the existing event
        assert plan2.merged_event_id is not None
        signals = repo.get_event_signals(plan2.merged_event_id)
        assert len(signals) == 2
        repo.close()


# ===========================================================================
# C. Correlation — same entity, outside window → new Event
# ===========================================================================


class TestCorrelationOutsideWindow:

    def test_two_signals_same_entity_outside_window_create_two_events(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        fixed_now = _ts(2026, 8, 17, 12)
        config = CorrelationConfig(correlation_window_seconds=24 * 3600)
        correlator = EventCorrelator(
            repository=repo,
            config=config,
            now_factory=lambda: fixed_now,
        )

        sig1 = _sig(id_=1, entity_id=eid, observed_at=_ts(2026, 8, 1, 10), fingerprint="fp-1")
        sig2 = _sig(id_=2, entity_id=eid, observed_at=_ts(2026, 8, 17, 11), fingerprint="fp-2")

        plan1 = correlator.process_signal(sig1)
        repo.commit_plan(plan1)
        plan2 = correlator.process_signal(sig2)
        repo.commit_plan(plan2)

        assert len(plan1.events_to_create) == 1
        assert len(plan2.events_to_create) == 1
        evts = repo.connection.execute(
            "SELECT id FROM events WHERE entity_id = ? ORDER BY created_at", (eid,)
        ).fetchall()
        assert len(evts) == 2
        repo.close()


# ===========================================================================
# D. Correlation — different entities → separate Events
# ===========================================================================


class TestCorrelationDifferentEntities:

    def test_signals_different_entities_never_merge(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid_a = _entity_id(repo, "github:a/repo")
        eid_b = _entity_id(repo, "github:b/repo")
        fixed_now = _ts(2026, 8, 17, 12)
        correlator = EventCorrelator(
            repository=repo,
            now_factory=lambda: fixed_now,
        )

        sig_a = _sig(id_=1, entity_id=eid_a, observed_at=_ts(2026, 8, 17, 10), fingerprint="fp-a")
        sig_b = _sig(id_=2, entity_id=eid_b, observed_at=_ts(2026, 8, 17, 11), fingerprint="fp-b")

        plan_a = correlator.process_signal(sig_a)
        repo.commit_plan(plan_a)
        plan_b = correlator.process_signal(sig_b)
        repo.commit_plan(plan_b)

        assert len(plan_a.events_to_create) == 1
        assert len(plan_b.events_to_create) == 1
        evts = repo.connection.execute(
            "SELECT entity_id FROM events ORDER BY created_at"
        ).fetchall()
        assert len(evts) == 2
        assert evts[0]["entity_id"] == eid_a
        assert evts[1]["entity_id"] == eid_b

        # Signal A is NOT in Event B
        evt_b = repo.find_open_event_for_entity(eid_b, event_type="content_change")
        assert evt_b is not None
        sigs_in_b = repo.get_event_signals(evt_b.id)
        assert all(s.id != sig_a.id for s in sigs_in_b)
        repo.close()


# ===========================================================================
# E. Closed Event must not receive new Signals
# ===========================================================================


class TestClosedEvent:

    def test_closed_event_does_not_receive_new_signals(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        fixed_now = _ts(2026, 8, 17, 12)
        correlator = EventCorrelator(
            repository=repo,
            now_factory=lambda: fixed_now,
        )

        sig1 = _sig(id_=1, entity_id=eid, observed_at=_ts(2026, 8, 17, 10), fingerprint="fp-1")
        plan1 = correlator.process_signal(sig1)
        repo.commit_plan(plan1)

        # Get the created event and close it
        e = repo.find_open_event_for_entity(eid, event_type="content_change")
        assert e is not None
        correlator.close_event(e.id)

        # New signal for same entity — should create a NEW event
        sig2 = _sig(id_=2, entity_id=eid, observed_at=_ts(2026, 8, 17, 11), fingerprint="fp-2")
        plan2 = correlator.process_signal(sig2)
        repo.commit_plan(plan2)

        assert len(plan2.events_to_create) == 1
        new_evt = repo.find_open_event_for_entity(eid, event_type="content_change")
        assert new_evt is not None
        assert new_evt.id != e.id
        signals_in_old = repo.get_event_signals(e.id)
        assert len(signals_in_old) == 1
        signals_in_new = repo.get_event_signals(new_evt.id)
        assert len(signals_in_new) == 1
        repo.close()

    def test_close_event_updates_status(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        ev = repo.create_event(entity_id=eid, event_type="t", created_at=_ts())
        closed = repo.update_event(ev.id, status="closed", updated_at=_ts(2026, 8, 17, 11))
        assert closed.status == "closed"
        repo.close()


# ===========================================================================
# F. Configurable correlation window
# ===========================================================================


class TestConfigurableWindow:

    def test_short_window_splits_signals(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        fixed_now = _ts(2026, 8, 17, 12)
        config = CorrelationConfig(correlation_window_seconds=3600)
        correlator = EventCorrelator(
            repository=repo,
            config=config,
            now_factory=lambda: fixed_now,
        )

        sig1 = _sig(id_=1, entity_id=eid, observed_at=_ts(2026, 8, 17, 10), fingerprint="fp-1")
        sig2 = _sig(id_=2, entity_id=eid, observed_at=_ts(2026, 8, 17, 11, 30), fingerprint="fp-2")

        plan1 = correlator.process_signal(sig1)
        repo.commit_plan(plan1)
        plan2 = correlator.process_signal(sig2)
        repo.commit_plan(plan2)

        assert len(plan1.events_to_create) == 1
        assert len(plan2.events_to_create) == 1
        evts = repo.connection.execute(
            "SELECT id FROM events WHERE entity_id = ? ORDER BY created_at", (eid,)
        ).fetchall()
        assert len(evts) == 2
        repo.close()

    def test_invalid_window_raises(self):
        with pytest.raises(ValueError):
            CorrelationConfig(correlation_window_seconds=0)

    def test_default_window_is_24h(self):
        config = CorrelationConfig()
        assert config.window == timedelta(hours=24)


# ===========================================================================
# G. Deterministic event_type derivation
# ===========================================================================


class TestEventTypeDerivation:

    def test_content_change_signal_maps_to_content_change_event(self):
        sig = Signal(id=1, entity_id=1, signal_type="content_change", observed_at=_ts())
        assert _derive_event_type(sig) == "content_change"

    def test_derivation_is_identity_for_known_signal(self):
        sig = Signal(id=1, entity_id=1, signal_type="stars_changed", observed_at=_ts())
        assert _derive_event_type(sig) == "stars_changed"


# ===========================================================================
# H. Injectable clock — no wall-clock dependency
# ===========================================================================


class TestInjectableClock:

    def test_correlator_uses_injected_now(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        injected = _ts(2026, 6, 15, 8, 0)
        correlator = EventCorrelator(
            repository=repo,
            now_factory=lambda: injected,
        )

        sig = _sig(id_=1, entity_id=eid, observed_at=injected, fingerprint="fp-clock")
        plan = correlator.process_signal(sig)
        repo.commit_plan(plan)

        evts = repo.connection.execute(
            "SELECT created_at FROM events WHERE entity_id = ?", (eid,)
        ).fetchall()
        assert len(evts) == 1
        assert evts[0]["created_at"] == injected.isoformat()
        repo.close()


# ===========================================================================
# I. Importance default
# ===========================================================================


class TestImportanceDefault:

    def test_default_importance_is_medium(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        correlator = EventCorrelator(repository=repo, now_factory=lambda: _ts())
        sig = _sig(id_=1, entity_id=eid, fingerprint="fp-imp")
        plan = correlator.process_signal(sig)
        repo.commit_plan(plan)

        evts = repo.connection.execute(
            "SELECT importance FROM events WHERE entity_id = ?", (eid,)
        ).fetchall()
        assert len(evts) == 1
        assert evts[0]["importance"] == Importance.IMPORTANT.value
        repo.close()

    def test_custom_importance_config(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        config = CorrelationConfig(default_importance="high")
        correlator = EventCorrelator(repository=repo, config=config, now_factory=lambda: _ts())
        sig = _sig(id_=1, entity_id=eid, fingerprint="fp-imp-2")
        plan = correlator.process_signal(sig)
        repo.commit_plan(plan)

        evts = repo.connection.execute(
            "SELECT importance FROM events WHERE entity_id = ?", (eid,)
        ).fetchall()
        assert len(evts) == 1
        assert evts[0]["importance"] == Importance.IMPORTANT.value
        repo.close()


# ===========================================================================
# K. Negative integration tests
# ===========================================================================


class TestNoForbiddenIntegrations:

    def test_no_ai_import_in_correlator(self):
        import importlib
        mod = importlib.import_module("web_watcher.event_correlator")
        imports = [name for name in dir(mod) if not name.startswith("_")]
        forbidden = {"openai", "anthropic", "google", "gemini", "llm", "ai_client"}
        assert not forbidden.intersection(imports)

    def test_no_telegram_import_in_correlator(self):
        import importlib
        mod = importlib.import_module("web_watcher.event_correlator")
        imports = [name for name in dir(mod) if not name.startswith("_")]
        assert "telegram" not in [n.lower() for n in imports]

    def test_no_network_import_in_correlator(self):
        import importlib
        mod = importlib.import_module("web_watcher.event_correlator")
        imports = [name for name in dir(mod) if not name.startswith("_")]
        forbidden = {"requests", "urllib", "httpx", "aiohttp", "http"}
        assert not forbidden.intersection([n.lower() for n in imports])

    def test_no_cron_or_browser_in_correlator(self):
        import importlib
        mod = importlib.import_module("web_watcher.event_correlator")
        imports = [name for name in dir(mod) if not name.startswith("_")]
        forbidden = {"cron", "browser", "selenium", "playwright"}
        assert not forbidden.intersection([n.lower() for n in imports])

    def test_no_github_write_in_correlator(self):
        import importlib
        mod = importlib.import_module("web_watcher.event_correlator")
        imports = [name for name in dir(mod) if not name.startswith("_")]
        assert "github_api" not in [n.lower() for n in imports]
        assert "git" not in [n.lower() for n in imports]


# ===========================================================================
# J. Idempotency
# ===========================================================================


class TestIdempotency:

    def test_same_signal_cannot_attach_twice(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        ev = repo.create_event(entity_id=eid, event_type="content_change", created_at=_ts())
        sig = repo.create_signal(
            entity_id=eid, signal_type="content_change",
            observed_at=_ts(), value="h", fingerprint="fp-1",
        )
        assert sig is not None
        assert repo.attach_signal_to_event(ev.id, sig.id) is True
        assert repo.attach_signal_to_event(ev.id, sig.id) is False  # duplicate
        assert len(repo.get_event_signals(ev.id)) == 1
        repo.close()

    def test_repeated_correlation_is_idempotent(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        fixed_now = _ts(2026, 8, 17, 12)
        correlator = EventCorrelator(
            repository=repo,
            now_factory=lambda: fixed_now,
        )

        sig = _sig(id_=1, entity_id=eid, observed_at=_ts(2026, 8, 17, 10), fingerprint="fp-idem")

        plan1 = correlator.process_signal(sig)
        repo.commit_plan(plan1)
        plan2 = correlator.process_signal(sig)
        repo.commit_plan(plan2)

        # Only one Event was ever created
        evts = repo.connection.execute(
            "SELECT COUNT(*) as cnt FROM events WHERE entity_id = ?", (eid,)
        ).fetchone()
        assert evts["cnt"] == 1
        repo.close()

    def test_event_signal_count_is_correct(self, tmp_path):
        repo = Repository(tmp_path / "watcher.db")
        eid = _entity_id(repo)
        fixed_now = _ts(2026, 8, 17, 12)
        correlator = EventCorrelator(repository=repo, now_factory=lambda: fixed_now)

        sig1 = _sig(id_=1, entity_id=eid, observed_at=_ts(2026, 8, 17, 10), fingerprint="fp-a")
        sig2 = _sig(id_=2, entity_id=eid, observed_at=_ts(2026, 8, 17, 11), fingerprint="fp-b")
        sig3 = _sig(id_=3, entity_id=eid, observed_at=_ts(2026, 8, 17, 11, 30), fingerprint="fp-c")

        for sig in [sig1, sig2, sig3]:
            plan = correlator.process_signal(sig)
            repo.commit_plan(plan)

        evts = repo.connection.execute(
            "SELECT COUNT(*) as cnt FROM events WHERE entity_id = ?", (eid,)
        ).fetchone()
        assert evts["cnt"] == 1
        signals = repo.connection.execute(
            "SELECT COUNT(*) as cnt FROM event_signals"
        ).fetchone()
        assert signals["cnt"] == 3
        repo.close()
