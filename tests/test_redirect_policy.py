"""Tests for explicit redirect policy (FR-05)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from web_watcher.fetch import FetchResult, FetchStatus
from web_watcher.fetch_policy import FetchPolicy
from web_watcher.models import Target, TargetStatus
from web_watcher.generic_web_target import GenericWebTarget
from web_watcher.fetcher import SmartFetcher


def test_fetch_policy_301_redirect(tmp_path):
    policy = FetchPolicy()
    target = Target(
        id="t1",
        url="https://example.com/old",
        interval="15m",
        status=TargetStatus.NORMAL,
    )
    evaluation = policy.evaluate_response(
        target=target,
        status_code=301,
        headers={"location": "https://example.com/new"},
        error=None,
        now=datetime.now(timezone.utc),
    )
    assert evaluation.new_status == TargetStatus.NORMAL
    assert evaluation.should_emit_signal is False
    assert "301" in evaluation.reason
    assert "example.com/new" in evaluation.reason


def test_fetch_policy_302_redirect(tmp_path):
    policy = FetchPolicy()
    target = Target(
        id="t2",
        url="https://example.com/temp",
        interval="15m",
        status=TargetStatus.NORMAL,
    )
    evaluation = policy.evaluate_response(
        target=target,
        status_code=302,
        headers={"location": "https://example.com/other"},
        error=None,
        now=datetime.now(timezone.utc),
    )
    assert evaluation.new_status == TargetStatus.NORMAL
    assert evaluation.should_emit_signal is False
    assert "302" in evaluation.reason


def test_fetch_policy_redirect_without_location(tmp_path):
    policy = FetchPolicy()
    target = Target(
        id="t3",
        url="https://example.com/x",
        interval="15m",
        status=TargetStatus.NORMAL,
    )
    evaluation = policy.evaluate_response(
        target=target,
        status_code=301,
        headers={},
        error=None,
        now=datetime.now(timezone.utc),
    )
    assert evaluation.new_status == TargetStatus.NORMAL
    assert evaluation.should_emit_signal is False
    assert "301" in evaluation.reason


def test_smart_fetcher_captures_redirect(tmp_path):
    fetcher = SmartFetcher()
    try:
        # Mock the session.get to return a redirect response
        mock_response = MagicMock()
        mock_response.status_code = 301
        mock_response.headers = {"Location": "https://example.com/new", "Content-Type": "text/html"}
        mock_response.text = ""
        fetcher.session.get = MagicMock(return_value=mock_response)

        result = fetcher.fetch("https://example.com/old")
        assert result.status == FetchStatus.REDIRECT
        assert result.status_code == 301
        assert result.metadata.get("redirect_url") == "https://example.com/new"
        assert result.metadata.get("redirect_status") == 301
    finally:
        fetcher.close()


def test_generic_web_target_301_populates_updated_url(tmp_path):
    target = Target(
        id="t4",
        url="https://example.com/old",
        interval="15m",
        status=TargetStatus.NORMAL,
    )
    web_target = GenericWebTarget(target=target)

    # Mock fetcher to return redirect
    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="t4",
        status=FetchStatus.REDIRECT,
        status_code=301,
        fetched_at=datetime.now(timezone.utc),
        content=None,
        metadata={"redirect_url": "https://example.com/new", "redirect_status": 301},
    )

    policy = FetchPolicy()
    result = web_target.execute(fetcher=mock_fetcher, policy=policy)

    assert result.updated_url == "https://example.com/new"
    assert result.status_code == 301
    assert result.signals_emitted == []
