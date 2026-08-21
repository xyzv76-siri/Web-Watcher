import hashlib
from unittest.mock import MagicMock
from datetime import datetime, timedelta
from web_watcher.models import Target, TargetStatus
from web_watcher.fetch import FetchStatus
from web_watcher.fetcher import SmartFetcher, FetchResult
from web_watcher.fetch_policy import FetchPolicy
from web_watcher.rule_models import ExtractorConfig
from web_watcher.generic_web_target import GenericWebTarget
from web_watcher.execution_semantics import ExecutionOutcome


HTML_SAMPLE_V1 = """
<div class="pricing">
    <span class="plan">Pro</span>
    <span class="price">$99.00</span>
</div>
"""

HTML_SAMPLE_V2 = """
<div class="pricing">
    <span class="plan">Pro</span>
    <span class="price">$79.00</span>
</div>
"""


def _make_extractors():
    return [
        ExtractorConfig(
            name="price",
            selector_type="css",
            selector="div.pricing .price",
            transforms=["strip_tags", "to_float"],
        )
    ]


def test_generic_web_target_skipped_when_in_cooldown():
    now = datetime.utcnow()
    target = Target(
        id="t_cooldown",
        url="https://example.com",
        status=TargetStatus.COOLDOWN,
        next_allowed_at=now + timedelta(seconds=600),
    )
    adapter = GenericWebTarget(target=target)
    res = adapter.execute(now=now)

    assert res.allowed is False
    assert len(res.signals_emitted) == 0
    assert "cooldown" in res.reason.lower()


def test_generic_web_target_304_not_modified_short_circuit():
    now = datetime.utcnow()
    target = Target(id="t_304", url="https://example.com", etag='"etag-123"')
    adapter = GenericWebTarget(target=target)

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="t_304",
        status=FetchStatus.NOT_MODIFIED,
        status_code=304,
        fetched_at=now,
        etag='"etag-123"',
    )

    res = adapter.execute(fetcher=mock_fetcher, now=now)

    assert res.allowed is True
    assert res.is_304 is True
    assert len(res.signals_emitted) == 0
    assert target.status == TargetStatus.NORMAL


def test_generic_web_target_initial_fetch_establishes_baseline():
    now = datetime.utcnow()
    target = Target(id="t_init", url="https://example.com/pricing")
    adapter = GenericWebTarget(target=target, extractors=_make_extractors())

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="t_init",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=now,
        content=HTML_SAMPLE_V1,
        etag='"etag-v1"',
    )

    res = adapter.execute(fetcher=mock_fetcher, now=now)

    assert res.allowed is True
    assert res.status_code == 200
    assert len(res.signals_emitted) == 0
    assert res.observation.status == "first_observation"
    assert res.updated_metadata.get("initialized") is True
    assert res.updated_metadata.get("normalized_values", {}).get("price") == "99.0"


def test_generic_web_target_unchanged_content_no_signal():
    now = datetime.utcnow()
    target = Target(
        id="t_unchanged",
        url="https://example.com/pricing",
        metadata={"initialized": True, "normalized_values": {"price": "99.0"}},
    )
    adapter = GenericWebTarget(target=target, extractors=_make_extractors())

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="t_unchanged",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=now,
        content=HTML_SAMPLE_V1,
        etag='"etag-v1"',
    )

    res = adapter.execute(fetcher=mock_fetcher, now=now)

    assert res.allowed is True
    assert res.status_code == 200
    assert len(res.signals_emitted) == 0
    assert res.outcome == ExecutionOutcome.SUCCESS_UNCHANGED
    assert "unchanged" in res.reason.lower() or "identical" in res.reason.lower()


def test_generic_web_target_content_changed_emits_signal():
    now = datetime.utcnow()
    target = Target(
        id="t_changed",
        url="https://example.com/pricing",
        metadata={"initialized": True, "normalized_values": {"price": "99.0"}},
    )
    adapter = GenericWebTarget(target=target, extractors=_make_extractors())

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="t_changed",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=now,
        content=HTML_SAMPLE_V2,
        etag='"etag-v2"',
    )

    res = adapter.execute(fetcher=mock_fetcher, now=now)

    assert res.allowed is True
    assert res.status_code == 200
    assert len(res.signals_emitted) == 1
    assert res.extracted_values["price"] == 79.0
    assert res.outcome == ExecutionOutcome.SUCCESS_CHANGED


def test_generic_web_target_non_200_status_no_signal():
    now = datetime.utcnow()
    target = Target(id="t_500", url="https://example.com")
    adapter = GenericWebTarget(target=target)

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="t_500",
        status=FetchStatus.HTTP_ERROR,
        status_code=500,
        fetched_at=now,
        content="Internal Server Error",
        error=None,
    )

    res = adapter.execute(fetcher=mock_fetcher, now=now)

    assert res.allowed is True
    assert res.status_code == 500
    assert len(res.signals_emitted) == 0
    # Package A contract: adapter must not mutate target; state is returned in result
    assert res.new_status == TargetStatus.BACKOFF


def test_generic_web_target_custom_headers_passed_to_fetcher():
    now = datetime.utcnow()
    target = Target(id="t_headers", url="https://example.com")
    adapter = GenericWebTarget(
        target=target,
        custom_headers={"X-Custom-Header": "test-value"},
    )

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="t_headers",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=now,
        content=HTML_SAMPLE_V1,
        etag='"etag-v1"',
    )

    adapter.execute(fetcher=mock_fetcher, now=now)

    mock_fetcher.fetch.assert_called_once()
    call_kwargs = mock_fetcher.fetch.call_args.kwargs
    assert call_kwargs["custom_headers"]["X-Custom-Header"] == "test-value"
    assert call_kwargs["url"] == "https://example.com"


HTML_SCOPE_V1 = """
<div class="pricing">
    <div class="plan">Pro</div>
    <div class="price">$99.00</div>
    <aside class="ads">Buy now!</aside>
</div>
"""

HTML_SCOPE_V2 = """
<div class="pricing">
    <div class="plan">Pro</div>
    <div class="price">$79.00</div>
    <aside class="ads">Buy now!</aside>
</div>
"""


def _make_scope_extractors():
    return [
        ExtractorConfig(
            name="price",
            selector_type="css",
            selector="div.pricing",
            scope_selector=".price",
            transforms=["strip_tags", "to_float"],
        )
    ]


def test_generic_web_target_repo_saves_target_and_signal():
    now = datetime.utcnow()
    target = Target(id="t_repo", url="https://example.com")
    adapter = GenericWebTarget(target=target, extractors=_make_extractors())

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="t_repo",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=now,
        content=HTML_SAMPLE_V1,
        etag='"etag-v1"',
    )

    mock_repo = MagicMock()
    mock_repo.save_target.return_value = None
    mock_repo.save_signal.return_value = None

    res = adapter.execute(fetcher=mock_fetcher, repo=mock_repo, now=now)

    assert res.allowed is True
    # Package A contract: adapter must not persist directly
    mock_repo.save_target.assert_not_called()
    mock_repo.save_signal.assert_not_called()
    # First observation establishes baseline without emitting a signal
    assert len(res.signals_emitted) == 0
    assert res.updated_etag == '"etag-v1"'
    assert res.observation.status == "first_observation"


def test_generic_web_target_scope_selector_narrows_content():
    now = datetime.utcnow()
    target = Target(
        id="t_scope",
        url="https://example.com/pricing",
        metadata={"initialized": True, "normalized_values": {"price": "99.0"}},
    )
    adapter = GenericWebTarget(target=target, extractors=_make_scope_extractors())

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="t_scope",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=now,
        content=HTML_SCOPE_V2,
        etag='"etag-v2"',
    )

    res = adapter.execute(fetcher=mock_fetcher, now=now)

    assert res.allowed is True
    assert res.status_code == 200
    # Only the scoped .price element should be compared; ads should be ignored.
    assert len(res.signals_emitted) == 1
    assert res.extracted_values["price"] == 79.0
    assert res.outcome == ExecutionOutcome.SUCCESS_CHANGED


def test_generic_web_target_scope_selector_miss_blocks_signal():
    now = datetime.utcnow()
    target = Target(
        id="t_scope_miss",
        url="https://example.com/pricing",
        metadata={"initialized": True, "normalized_values": {"price": "99.0"}},
    )
    adapter = GenericWebTarget(
        target=target,
        extractors=[
            ExtractorConfig(
                name="price",
                selector_type="css",
                selector="div.pricing",
                scope_selector=".non-existent",
            )
        ],
    )

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="t_scope_miss",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=now,
        content=HTML_SCOPE_V2,
        etag='"etag-v2"',
    )

    res = adapter.execute(fetcher=mock_fetcher, now=now)

    assert res.allowed is True
    assert res.status_code == 200
    # Scope miss must NOT silently fall back; it should be treated as extraction failure.
    assert len(res.signals_emitted) == 0
    assert res.outcome == ExecutionOutcome.SELECTOR_NOT_FOUND
    assert "scope" in res.reason.lower() or "selector" in res.reason.lower()


def test_generic_web_target_without_scope_keeps_existing_behavior():
    now = datetime.utcnow()
    target = Target(
        id="t_no_scope",
        url="https://example.com/pricing",
        metadata={"initialized": True, "normalized_values": {"price": "99.0"}},
    )
    adapter = GenericWebTarget(target=target, extractors=_make_extractors())

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="t_no_scope",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=now,
        content=HTML_SCOPE_V2,
        etag='"etag-v2"',
    )

    res = adapter.execute(fetcher=mock_fetcher, now=now)

    assert res.allowed is True
    assert res.status_code == 200
    assert len(res.signals_emitted) == 1
    assert res.outcome == ExecutionOutcome.SUCCESS_CHANGED


def test_generic_web_target_trigger_emits_signal_within_window():
    from web_watcher.rule_models import WatcherRule, TargetConfig, TriggerConfig, RoutingConfig

    now = datetime.utcnow()
    old_ts = now - timedelta(minutes=10)
    target = Target(
        id="t_trigger",
        url="https://example.com/pricing",
        metadata={
            "initialized": True,
            "normalized_values": {"price": "99.0"},
            "observation_timestamp": old_ts.isoformat(),
        },
    )
    rule = WatcherRule(
        id="rule_trigger",
        name="Trigger Rule",
        target=TargetConfig(url="https://example.com/pricing"),
        extractors=_make_extractors(),
        triggers=[TriggerConfig(type="numeric_delta", field="price", condition="abs_delta > 0.01", time_window_minutes=30)],
        routing=RoutingConfig(),
    )
    adapter = GenericWebTarget(target=target, extractors=rule.extractors, rule=rule)

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="t_trigger",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=now,
        content=HTML_SAMPLE_V2,
        etag='"etag-v2"',
    )

    res = adapter.execute(fetcher=mock_fetcher, now=now)

    assert res.allowed is True
    assert len(res.signals_emitted) == 1
    assert "Triggered" in res.reason


def test_generic_web_target_trigger_does_not_emit_signal_outside_window():
    from web_watcher.rule_models import WatcherRule, TargetConfig, TriggerConfig, RoutingConfig

    now = datetime.utcnow()
    old_ts = now - timedelta(minutes=40)
    target = Target(
        id="t_trigger",
        url="https://example.com/pricing",
        metadata={
            "initialized": True,
            "normalized_values": {"price": "99.0"},
            "observation_timestamp": old_ts.isoformat(),
        },
    )
    rule = WatcherRule(
        id="rule_trigger",
        name="Trigger Rule",
        target=TargetConfig(url="https://example.com/pricing"),
        extractors=_make_extractors(),
        triggers=[TriggerConfig(type="numeric_delta", field="price", condition="abs_delta > 0.01", time_window_minutes=30)],
        routing=RoutingConfig(),
    )
    adapter = GenericWebTarget(target=target, extractors=rule.extractors, rule=rule)

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="t_trigger",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=now,
        content=HTML_SAMPLE_V2,
        etag='"etag-v2"',
    )

    res = adapter.execute(fetcher=mock_fetcher, now=now)

    assert res.allowed is True
    assert len(res.signals_emitted) == 0
    assert "Trigger conditions not met" in res.reason
