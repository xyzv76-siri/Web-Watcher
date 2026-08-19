import hashlib
from unittest.mock import MagicMock
from datetime import datetime, timedelta
from web_watcher.models import Target, TargetStatus
from web_watcher.fetch import FetchStatus
from web_watcher.fetcher import SmartFetcher, FetchResult
from web_watcher.fetch_policy import FetchPolicy
from web_watcher.rule_models import ExtractorConfig
from web_watcher.generic_web_target import GenericWebTarget


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


def test_generic_web_target_initial_fetch_emits_signal():
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
    assert len(res.signals_emitted) == 1
    assert res.extracted_values["price"] == 99.0
    assert target.content_hash is not None
    assert target.etag == '"etag-v1"'


def test_generic_web_target_unchanged_content_no_signal():
    now = datetime.utcnow()
    target = Target(
        id="t_unchanged",
        url="https://example.com/pricing",
        content_hash=hashlib.sha256(HTML_SAMPLE_V1.encode("utf-8")).hexdigest(),
        metadata={"initialized": True},
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
    assert "identical" in res.reason.lower()


def test_generic_web_target_content_changed_emits_signal():
    now = datetime.utcnow()
    target = Target(
        id="t_changed",
        url="https://example.com/pricing",
        content_hash=hashlib.sha256(HTML_SAMPLE_V1.encode("utf-8")).hexdigest(),
        metadata={"initialized": True},
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
    assert target.content_hash == hashlib.sha256(HTML_SAMPLE_V2.encode("utf-8")).hexdigest()


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
    assert target.status == TargetStatus.BACKOFF


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
    assert len(res.signals_emitted) == 1
    mock_repo.save_target.assert_called_once_with(target)
    mock_repo.save_signal.assert_called_once()
