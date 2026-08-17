"""Tests for Phase 11-A K.2 — Investigation evidence contract."""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from web_watcher.investigation_evidence import (
    Evidence,
    EvidenceType,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


NOW = datetime(2026, 8, 17, 12, 0, 0)
NOW_UTC = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)


def _valid_evidence(**kwargs) -> Evidence:
    defaults = dict(
        source="https://example.com/article",
        url="https://example.com/article",
        retrieved_at=NOW,
        claim="the claim under investigation",
        supporting_text="this is the supporting text from the source",
        evidence_type=EvidenceType.PRIMARY,
    )
    defaults.update(kwargs)
    return Evidence(**defaults)


# ===========================================================================
# EvidenceType
# ===========================================================================


class TestEvidenceType:
    """All four evidence types are present and correctly valued."""

    def test_primary_value(self):
        assert EvidenceType.PRIMARY.value == "primary"

    def test_secondary_value(self):
        assert EvidenceType.SECONDARY.value == "secondary"

    def test_historical_value(self):
        assert EvidenceType.HISTORICAL.value == "historical"

    def test_derived_value(self):
        assert EvidenceType.DERIVED.value == "derived"

    def test_all_members_listed(self):
        expected = {
            EvidenceType.PRIMARY,
            EvidenceType.SECONDARY,
            EvidenceType.HISTORICAL,
            EvidenceType.DERIVED,
        }
        assert set(EvidenceType) == expected

    def test_unknown_string_is_not_an_evidence_type(self):
        with pytest.raises(ValueError):
            EvidenceType("unknown_type")

    def test_case_sensitive_mismatch(self):
        with pytest.raises(ValueError):
            EvidenceType("PRIMARY")


# ===========================================================================
# Evidence — construction
# ===========================================================================


class TestEvidenceConstruction:
    """Evidence can be constructed with valid fields and stores them verbatim."""

    def test_construct_with_all_fields(self):
        ev = _valid_evidence()
        assert ev.source == "https://example.com/article"
        assert ev.url == "https://example.com/article"
        assert ev.retrieved_at == NOW
        assert ev.claim == "the claim under investigation"
        assert ev.supporting_text == "this is the supporting text from the source"
        assert ev.evidence_type is EvidenceType.PRIMARY

    def test_each_field_is_stored_exactly(self):
        ev = _valid_evidence(
            source="src",
            url="https://x",
            claim="c",
            supporting_text="s",
            evidence_type=EvidenceType.SECONDARY,
        )
        assert ev.source == "src"
        assert ev.url == "https://x"
        assert ev.claim == "c"
        assert ev.supporting_text == "s"
        assert ev.evidence_type is EvidenceType.SECONDARY


# ===========================================================================
# Evidence — field validation
# ===========================================================================


class TestEvidenceFieldValidation:
    """Each field rejects invalid values."""

    def test_empty_source_is_rejected(self):
        with pytest.raises(ValueError, match="source.*non-empty"):
            _valid_evidence(source="")

    def test_whitespace_only_source_is_rejected(self):
        with pytest.raises(ValueError, match="source.*non-empty"):
            _valid_evidence(source="   ")

    def test_non_str_source_is_rejected(self):
        with pytest.raises(TypeError, match="source.*str"):
            _valid_evidence(source=123)  # type: ignore[arg-type]

    def test_empty_url_is_rejected(self):
        with pytest.raises(ValueError, match="url.*non-empty"):
            _valid_evidence(url="")

    def test_whitespace_only_url_is_rejected(self):
        with pytest.raises(ValueError, match="url.*non-empty"):
            _valid_evidence(url="\t")

    def test_non_str_url_is_rejected(self):
        with pytest.raises(TypeError, match="url.*str"):
            _valid_evidence(url=42)  # type: ignore[arg-type]

    def test_empty_claim_is_rejected(self):
        with pytest.raises(ValueError, match="claim.*non-empty"):
            _valid_evidence(claim="")

    def test_whitespace_only_claim_is_rejected(self):
        with pytest.raises(ValueError, match="claim.*non-empty"):
            _valid_evidence(claim="  ")

    def test_non_str_claim_is_rejected(self):
        with pytest.raises(TypeError, match="claim.*str"):
            _valid_evidence(claim=None)  # type: ignore[arg-type]

    def test_empty_supporting_text_is_rejected(self):
        with pytest.raises(ValueError, match="supporting_text.*non-empty"):
            _valid_evidence(supporting_text="")

    def test_whitespace_only_supporting_text_is_rejected(self):
        with pytest.raises(ValueError, match="supporting_text.*non-empty"):
            _valid_evidence(supporting_text="\n")

    def test_non_str_supporting_text_is_rejected(self):
        with pytest.raises(TypeError, match="supporting_text.*str"):
            _valid_evidence(supporting_text=[])  # type: ignore[arg-type]

    def test_non_datetime_retrieved_at_is_rejected(self):
        with pytest.raises(TypeError, match="retrieved_at.*datetime"):
            _valid_evidence(retrieved_at="2026-08-17")  # type: ignore[arg-type]

    def test_integer_retrieved_at_is_rejected(self):
        with pytest.raises(TypeError, match="retrieved_at.*datetime"):
            _valid_evidence(retrieved_at=1234567890)  # type: ignore[arg-type]

    def test_non_evidence_type_is_rejected(self):
        with pytest.raises(TypeError, match="evidence_type.*EvidenceType"):
            _valid_evidence(evidence_type="primary")  # type: ignore[arg-type]

    def test_unrelated_enum_is_rejected(self):
        class OtherEnum:
            X = "x"

        with pytest.raises(TypeError, match="evidence_type.*EvidenceType"):
            _valid_evidence(evidence_type=OtherEnum.X)  # type: ignore[arg-type]


# ===========================================================================
# Evidence — datetime handling
# ===========================================================================


class TestEvidenceDatetimeHandling:
    """retrieved_at is preserved verbatim, no mutation, no timezone conversion."""

    def test_naive_datetime_preserved(self):
        ev = _valid_evidence()
        assert ev.retrieved_at is NOW
        assert ev.retrieved_at.tzinfo is None

    def test_utc_datetime_preserved(self):
        ev = _valid_evidence(retrieved_at=NOW_UTC)
        assert ev.retrieved_at == NOW_UTC
        assert ev.retrieved_at.tzinfo is timezone.utc

    def test_input_datetime_not_mutated(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        original_hash = hash(dt)
        ev = _valid_evidence(retrieved_at=dt)
        assert ev.retrieved_at == dt
        assert hash(dt) == original_hash
        # The object is still the same identity
        assert ev.retrieved_at is dt


# ===========================================================================
# Evidence — immutability
# ===========================================================================


class TestEvidenceImmutability:
    """Evidence is immutable — fields cannot be changed after construction."""

    def test_source_cannot_be_changed(self):
        ev = _valid_evidence()
        with pytest.raises(AttributeError):
            ev.source = "changed"  # type: ignore[misc]

    def test_url_cannot_be_changed(self):
        ev = _valid_evidence()
        with pytest.raises(AttributeError):
            ev.url = "https://other"  # type: ignore[misc]

    def test_claim_cannot_be_changed(self):
        ev = _valid_evidence()
        with pytest.raises(AttributeError):
            ev.claim = "new claim"  # type: ignore[misc]

    def test_supporting_text_cannot_be_changed(self):
        ev = _valid_evidence()
        with pytest.raises(AttributeError):
            ev.supporting_text = "new text"  # type: ignore[misc]

    def test_retrieved_at_cannot_be_changed(self):
        ev = _valid_evidence()
        with pytest.raises(AttributeError):
            ev.retrieved_at = NOW_UTC  # type: ignore[misc]

    def test_evidence_type_cannot_be_changed(self):
        ev = _valid_evidence()
        with pytest.raises(AttributeError):
            ev.evidence_type = EvidenceType.SECONDARY  # type: ignore[misc]


# ===========================================================================
# Evidence — equality and hash
# ===========================================================================


class TestEvidenceEquality:
    """Evidence supports equality and is hashable by default."""

    def test_identical_fields_are_equal(self):
        ev1 = _valid_evidence()
        ev2 = _valid_evidence()
        assert ev1 == ev2

    def test_different_source_inequality(self):
        ev1 = _valid_evidence(source="a")
        ev2 = _valid_evidence(source="b")
        assert ev1 != ev2

    def test_different_url_inequality(self):
        ev1 = _valid_evidence(url="https://a")
        ev2 = _valid_evidence(url="https://b")
        assert ev1 != ev2

    def test_different_claim_inequality(self):
        ev1 = _valid_evidence(claim="c1")
        ev2 = _valid_evidence(claim="c2")
        assert ev1 != ev2

    def test_different_supporting_text_inequality(self):
        ev1 = _valid_evidence(supporting_text="s1")
        ev2 = _valid_evidence(supporting_text="s2")
        assert ev1 != ev2

    def test_different_retrieved_at_inequality(self):
        ev1 = _valid_evidence(retrieved_at=datetime(2026, 1, 1))
        ev2 = _valid_evidence(retrieved_at=datetime(2026, 1, 2))
        assert ev1 != ev2

    def test_different_evidence_type_inequality(self):
        ev1 = _valid_evidence(evidence_type=EvidenceType.PRIMARY)
        ev2 = _valid_evidence(evidence_type=EvidenceType.SECONDARY)
        assert ev1 != ev2

    def test_hashable_and_hash_stable(self):
        ev = _valid_evidence()
        h = hash(ev)
        assert hash(ev) == h
        # Can be used in a set
        s = {ev}
        assert len(s) == 1
        # Second identical Evidence resolves to same set entry
        s.add(_valid_evidence())
        assert len(s) == 1

    def test_hash_must_match_equality(self):
        ev1 = _valid_evidence()
        ev2 = _valid_evidence()
        assert ev1 == ev2
        assert hash(ev1) == hash(ev2)

    def test_copy_preserves_equality(self):
        ev = _valid_evidence()
        ev2 = copy.copy(ev)
        assert ev == ev2
        assert hash(ev) == hash(ev2)