"""Tests for deterministic observation fingerprinting."""

import pytest
from web_watcher.web_fingerprint import observation_fingerprint, selector_config_fingerprint


class TestObservationFingerprint:

    def test_same_input_produces_same_fingerprint(self):
        fp1 = observation_fingerprint("target-1", "Hello World")
        fp2 = observation_fingerprint("target-1", "Hello World")
        assert fp1 == fp2
        assert len(fp1) == 64  # SHA-256 hex

    def test_different_content_produces_different_fingerprint(self):
        fp1 = observation_fingerprint("target-1", "Hello World")
        fp2 = observation_fingerprint("target-1", "Hello Universe")
        assert fp1 != fp2

    def test_different_target_produces_different_fingerprint(self):
        fp1 = observation_fingerprint("target-1", "Hello World")
        fp2 = observation_fingerprint("target-2", "Hello World")
        assert fp1 != fp2

    def test_selector_fingerprint_included(self):
        fp1 = observation_fingerprint("target-1", "Hello", "css|div.price")
        fp2 = observation_fingerprint("target-1", "Hello", "xpath|//div[@class='price']")
        assert fp1 != fp2

    def test_none_selector_fingerprint_stable(self):
        fp1 = observation_fingerprint("target-1", "Hello", None)
        fp2 = observation_fingerprint("target-1", "Hello", None)
        assert fp1 == fp2

    def test_empty_content_stable(self):
        fp1 = observation_fingerprint("target-1", "")
        fp2 = observation_fingerprint("target-1", "")
        assert fp1 == fp2

    def test_hex_format(self):
        fp = observation_fingerprint("target-1", "test")
        assert all(c in "0123456789abcdef" for c in fp)

    def test_no_timestamp_or_random_in_fingerprint(self):
        # The same inputs must always produce the same output regardless of when called.
        fp1 = observation_fingerprint("t", "c")
        import time
        time.sleep(0.01)
        fp2 = observation_fingerprint("t", "c")
        assert fp1 == fp2


class TestSelectorConfigFingerprint:

    def test_same_selector_same_fingerprint(self):
        fp1 = selector_config_fingerprint("css", "div.price")
        fp2 = selector_config_fingerprint("css", "div.price")
        assert fp1 == fp2

    def test_different_type_different_fingerprint(self):
        fp1 = selector_config_fingerprint("css", "div.price")
        fp2 = selector_config_fingerprint("xpath", "//div[@class='price']")
        assert fp1 != fp2

    def test_different_selector_different_fingerprint(self):
        fp1 = selector_config_fingerprint("css", "div.price")
        fp2 = selector_config_fingerprint("css", "div.title")
        assert fp1 != fp2

    def test_hex_format(self):
        fp = selector_config_fingerprint("css", "div.price")
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)
