"""Tests for deterministic content hashing."""

import hashlib

import pytest

from web_watcher.content_hash import sha256_of


class TestSha256Of:

    def test_returns_hex_string(self):
        digest = sha256_of("hello")
        assert isinstance(digest, str)
        assert len(digest) == 64
        # Valid hex
        int(digest, 16)

    def test_deterministic_same_input_same_hash(self):
        h1 = sha256_of("hello world")
        h2 = sha256_of("hello world")
        assert h1 == h2

    def test_different_input_different_hash(self):
        h1 = sha256_of("hello")
        h2 = sha256_of("world")
        assert h1 != h2

    def test_matches_known_sha256(self):
        expected = hashlib.sha256(b"hello").hexdigest()
        assert sha256_of("hello") == expected

    def test_empty_string_produces_valid_hash(self):
        digest = sha256_of("")
        assert len(digest) == 64
        assert digest == hashlib.sha256(b"").hexdigest()

    def test_unicode_input(self):
        digest = sha256_of("Hello 世界 🌍")
        assert len(digest) == 64
        assert digest == hashlib.sha256("Hello 世界 🌍".encode("utf-8")).hexdigest()

    def test_no_timestamp_in_hash(self):
        # Running twice with different wall-clock times must produce
        # identical hashes.
        h1 = sha256_of("same content")
        h2 = sha256_of("same content")
        assert h1 == h2

    def test_raises_on_non_string(self):
        with pytest.raises(TypeError):
            sha256_of(b"bytes")
        with pytest.raises(TypeError):
            sha256_of(None)
        with pytest.raises(TypeError):
            sha256_of(123)
