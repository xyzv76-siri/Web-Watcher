"""Tests for content normalization."""

import pytest
from web_watcher.normalizer import normalize_extracted_text, normalize_html_text


class TestNormalizeExtractedText:

    def test_plain_text_unchanged(self):
        assert normalize_extracted_text("Hello World") == "Hello World"

    def test_leading_trailing_whitespace_stripped(self):
        assert normalize_extracted_text("  Hello World  ") == "Hello World"

    def test_multiple_spaces_collapsed(self):
        assert normalize_extracted_text("Hello   World") == "Hello World"

    def test_tabs_and_newlines_collapsed(self):
        assert normalize_extracted_text("Hello\t\tWorld\n\nFoo") == "Hello World Foo"

    def test_empty_string_returns_empty(self):
        assert normalize_extracted_text("") == ""

    def test_whitespace_only_returns_empty(self):
        assert normalize_extracted_text("   \t\n  ") == ""

    def test_preserves_single_space_between_words(self):
        assert normalize_extracted_text("Hello World") == "Hello World"

    def test_chinese_text_whitespace_collapsed(self):
        assert normalize_extracted_text("  你好  世界  ") == "你好 世界"

    def test_mixed_whitespace_preserves_single_spaces(self):
        assert normalize_extracted_text("a \t b\n c") == "a b c"


class TestNormalizeHtmlText:

    def test_strips_tags(self):
        assert normalize_html_text("<p>Hello</p>") == "Hello"

    def test_collapses_whitespace_in_html(self):
        assert normalize_html_text("<div>  Hello   World  </div>") == "Hello World"

    def test_empty_string(self):
        assert normalize_html_text("") == ""

    def test_nested_tags(self):
        assert normalize_html_text("<div><span>Hello</span> <b>World</b></div>") == "Hello World"
