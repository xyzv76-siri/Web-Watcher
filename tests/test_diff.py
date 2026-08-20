"""Tests for diff computation."""

import pytest
from web_watcher.diff import DiffResult, compute_diff


class TestDiffResult:

    def test_unchanged_factory(self):
        d = DiffResult.unchanged("abc", "abc")
        assert d.changed is False
        assert d.before == "abc"
        assert d.after == "abc"
        assert d.summary == "No change"

    def test_changed_factory_defaults(self):
        d = DiffResult.changed("abc", "def")
        assert d.changed is True
        assert d.before == "abc"
        assert d.after == "def"
        assert d.summary == "Content changed"
        assert d.regions == []

    def test_changed_factory_custom(self):
        d = DiffResult.changed("abc", "def", summary="Price updated", regions=["line 1"])
        assert d.summary == "Price updated"
        assert d.regions == ["line 1"]


class TestComputeDiff:

    def test_same_content_returns_unchanged(self):
        d = compute_diff("Hello World", "Hello World")
        assert d.changed is False
        assert d.summary == "No change"

    def test_different_content_returns_changed(self):
        d = compute_diff("Hello World", "Hello Universe")
        assert d.changed is True
        assert "Changed:" in d.summary

    def test_empty_before_returns_changed(self):
        d = compute_diff("", "Hello")
        assert d.changed is True
        assert "<empty>" in d.summary

    def test_empty_after_returns_changed(self):
        d = compute_diff("Hello", "")
        assert d.changed is True
        assert "<empty>" in d.summary

    def test_both_empty_returns_unchanged(self):
        d = compute_diff("", "")
        assert d.changed is False

    def test_whitespace_only_difference_returns_changed(self):
        d = compute_diff("Hello", "Hello ")
        assert d.changed is True

    def test_regions_include_lengths(self):
        d = compute_diff("short", "much longer content here")
        assert d.changed is True
        assert any("before_len=" in r for r in d.regions)

    def test_metadata_contains_lengths(self):
        d = compute_diff("abc", "defgh")
        assert d.metadata["before_len"] == 3
        assert d.metadata["after_len"] == 5

    def test_deterministic_output(self):
        d1 = compute_diff("Hello World", "Hello Universe")
        d2 = compute_diff("Hello World", "Hello Universe")
        assert d1.summary == d2.summary
        assert d1.regions == d2.regions
