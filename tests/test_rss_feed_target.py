import hashlib
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from web_watcher.models import Target, TargetStatus
from web_watcher.rss_feed_target import RSSFeedTarget, FeedEntry, TargetExecutionResult


RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <item>
      <title>First Entry</title>
      <link>https://example.com/1</link>
      <guid>https://example.com/1</guid>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
      <description>First content</description>
    </item>
    <item>
      <title>Second Entry</title>
      <link>https://example.com/2</link>
      <guid>https://example.com/2</guid>
      <pubDate>Tue, 02 Jan 2024 00:00:00 +0000</pubDate>
      <description>Second content</description>
    </item>
  </channel>
</rss>
"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom</title>
  <entry>
    <title>Atom Entry</title>
    <link href="https://example.com/atom/1"/>
    <id>https://example.com/atom/1</id>
    <updated>2024-01-01T00:00:00Z</updated>
    <content>Atom content</content>
  </entry>
</feed>
"""


def test_parse_rss_returns_entries():
    entries = RSSFeedTarget.parse_feed(RSS_SAMPLE)
    assert len(entries) == 2
    assert entries[0].title == "First Entry"
    assert entries[0].link == "https://example.com/1"
    assert entries[1].title == "Second Entry"


def test_parse_atom_returns_entries():
    entries = RSSFeedTarget.parse_feed(ATOM_SAMPLE)
    assert len(entries) == 1
    assert entries[0].title == "Atom Entry"
    assert entries[0].link == "https://example.com/atom/1"


def test_parse_invalid_xml_returns_empty():
    entries = RSSFeedTarget.parse_feed("not xml")
    assert entries == []


def test_execute_emits_signal_for_new_entries():
    target = Target(
        id="test-feed",
        url="https://example.com/feed.rss",
        interval="60s",
        status=TargetStatus.NORMAL,
        metadata={},
    )
    adapter = RSSFeedTarget(target=target)

    fetch_res = MagicMock()
    fetch_res.status_code = 200
    fetch_res.status = MagicMock()
    fetch_res.status.value = "success"
    fetch_res.content = RSS_SAMPLE
    fetch_res.etag = None
    fetch_res.last_modified = None
    fetch_res.error = None

    fetcher = MagicMock()
    fetcher.fetch.return_value = fetch_res

    policy = MagicMock()
    policy.prepare_request.return_value = MagicMock(allowed=True, headers={}, host="example.com")
    policy.evaluate_response.return_value = MagicMock(
        allowed=True,
        status_code=200,
        new_status=TargetStatus.NORMAL,
        should_emit_signal=True,
        updated_etag=None,
        updated_last_modified=None,
        consecutive_failures=0,
        next_allowed_at=None,
        reason="OK",
    )
    policy.host_rate_limiter = None

    result: TargetExecutionResult = adapter.execute(fetcher=fetcher, policy=policy)

    assert result.outcome.value == "success_changed"
    assert len(result.signals_emitted) == 2
    first_id = "https://example.com/1"
    assert result.updated_metadata["feed_entries"][first_id]["title"] == "First Entry"

    sig0 = result.signals_emitted[0]
    payload = sig0.payload if hasattr(sig0, "payload") else sig0
    assert payload["change_type"] == "new_entry"


def test_execute_no_duplicate_signals_on_unchanged_feed():
    target = Target(
        id="test-feed",
        url="https://example.com/feed.rss",
        interval="60s",
        status=TargetStatus.NORMAL,
        metadata={
            "feed_entries": {
                "https://example.com/1": {
                    "id": "https://example.com/1",
                    "title": "First Entry",
                    "link": "https://example.com/1",
                    "published_at": "Mon, 01 Jan 2024 00:00:00 +0000",
                    "content": "First content",
                    "content_hash": hashlib.sha256("First Entry\x1fhttps://example.com/1\x1fFirst content".encode()).hexdigest(),
                }
            }
        },
    )
    adapter = RSSFeedTarget(target=target)

    fetch_res = MagicMock()
    fetch_res.status_code = 200
    fetch_res.status = MagicMock()
    fetch_res.status.value = "success"
    fetch_res.content = RSS_SAMPLE
    fetch_res.etag = None
    fetch_res.last_modified = None
    fetch_res.error = None

    fetcher = MagicMock()
    fetcher.fetch.return_value = fetch_res

    policy = MagicMock()
    policy.prepare_request.return_value = MagicMock(allowed=True, headers={}, host="example.com")
    policy.evaluate_response.return_value = MagicMock(
        allowed=True,
        status_code=200,
        new_status=TargetStatus.NORMAL,
        should_emit_signal=True,
        updated_etag=None,
        updated_last_modified=None,
        consecutive_failures=0,
        next_allowed_at=None,
        reason="OK",
    )
    policy.host_rate_limiter = None

    result: TargetExecutionResult = adapter.execute(fetcher=fetcher, policy=policy)

    assert result.outcome.value == "success_changed"
    assert len(result.signals_emitted) == 1
    sig0 = result.signals_emitted[0]
    payload = sig0.payload if hasattr(sig0, "payload") else sig0
    assert payload["change_type"] == "new_entry"
