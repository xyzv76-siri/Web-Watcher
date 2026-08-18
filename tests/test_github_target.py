import json
from unittest.mock import MagicMock
from datetime import datetime
from web_watcher.models import Target, TargetStatus
from web_watcher.fetcher import SmartFetcher, FetchResult
from web_watcher.github_target import GitHubTarget, parse_github_repo


RELEASE_PAYLOAD = {
    "tag_name": "v2.1.0",
    "name": "Flask 2.1.0 Released",
    "html_url": "https://github.com/pallets/flask/releases/tag/v2.1.0",
    "published_at": "2026-08-18T10:00:00Z",
    "body": "Changelog details",
}

REPO_META_PAYLOAD = {
    "stargazers_count": 3500,
}


def test_parse_github_repo():
    assert parse_github_repo("https://github.com/pallets/flask") == ("pallets", "flask")
    assert parse_github_repo("https://github.com/psf/requests.git") == ("psf", "requests")
    assert parse_github_repo("torvalds/linux") == ("torvalds", "linux")


def test_github_target_release_published_signal():
    target = Target(id="gh_flask", url="pallets/flask", metadata={"last_release_tag": "v2.0.0"})
    adapter = GitHubTarget(target=target, watch_types=["releases"])

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        status_code=200,
        content=json.dumps(RELEASE_PAYLOAD),
        etag='"rel-etag-210"',
    )

    res = adapter.execute(fetcher=mock_fetcher)

    assert res.allowed is True
    assert len(res.signals_emitted) == 1
    sig = res.signals_emitted[0]
    payload = sig.payload if hasattr(sig, "payload") else sig
    assert payload["tag_name"] == "v2.1.0"
    assert payload["owner"] == "pallets"
    assert target.metadata["last_release_tag"] == "v2.1.0"
    assert target.metadata["release_etag"] == '"rel-etag-210"'


def test_github_target_no_release_signal_when_unchanged():
    target = Target(id="gh_flask", url="pallets/flask", metadata={"last_release_tag": "v2.1.0", "release_etag": '"rel-etag-210"'})
    adapter = GitHubTarget(target=target, watch_types=["releases"])

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        status_code=304,
        is_304_not_modified=True,
        etag='"rel-etag-210"',
    )

    res = adapter.execute(fetcher=mock_fetcher)

    assert res.allowed is True
    assert len(res.signals_emitted) == 0
    assert res.is_304 is True


def test_github_target_star_delta_signal():
    target = Target(id="gh_flask", url="pallets/flask", metadata={"last_stars": 3499})
    adapter = GitHubTarget(target=target, watch_types=["stars"], star_delta_threshold=1)

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        status_code=200,
        content=json.dumps(REPO_META_PAYLOAD),
        etag='"repo-etag-3500"',
    )

    res = adapter.execute(fetcher=mock_fetcher)

    assert res.allowed is True
    assert len(res.signals_emitted) == 1
    sig = res.signals_emitted[0]
    payload = sig.payload if hasattr(sig, "payload") else sig
    assert payload["delta"] == 1
    assert payload["new_stars"] == 3500


def test_github_target_star_delta_below_threshold():
    target = Target(id="gh_flask", url="pallets/flask", metadata={"last_stars": 3500})
    adapter = GitHubTarget(target=target, watch_types=["stars"], star_delta_threshold=5)

    repo_payload = dict(REPO_META_PAYLOAD)
    repo_payload["stargazers_count"] = 3502
    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        status_code=200,
        content=json.dumps(repo_payload),
        etag='"repo-etag-3502"',
    )

    res = adapter.execute(fetcher=mock_fetcher)

    assert res.allowed is True
    assert len(res.signals_emitted) == 0


def test_github_target_in_cooldown_is_skipped():
    now = datetime.utcnow()
    target = Target(
        id="gh_cooldown",
        url="pallets/flask",
        status=TargetStatus.COOLDOWN,
        next_allowed_at=now + __import__("datetime").timedelta(seconds=600),
    )
    adapter = GitHubTarget(target=target, watch_types=["releases"])
    res = adapter.execute(now=now)

    assert res.allowed is False
    assert len(res.signals_emitted) == 0


def test_github_target_repo_saves_target_and_signal():
    target = Target(id="gh_save", url="pallets/flask", metadata={"last_release_tag": "v2.0.0"})
    adapter = GitHubTarget(target=target, watch_types=["releases"])

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        status_code=200,
        content=json.dumps(RELEASE_PAYLOAD),
        etag='"rel-etag-210"',
    )

    mock_repo = MagicMock()
    mock_repo.save_target.return_value = None
    mock_repo.save_signal.return_value = None

    res = adapter.execute(fetcher=mock_fetcher, repo=mock_repo)

    assert res.allowed is True
    assert len(res.signals_emitted) == 1
    mock_repo.save_target.assert_called_once_with(target)
    mock_repo.save_signal.assert_called_once()


def test_github_target_mixed_watch_types_emits_both_signals():
    target = Target(
        id="gh_mixed",
        url="pallets/flask",
        metadata={"last_release_tag": "v2.0.0", "last_stars": 3499},
    )
    adapter = GitHubTarget(target=target, watch_types=["releases", "stars"])

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.side_effect = [
        FetchResult(status_code=200, content=json.dumps(RELEASE_PAYLOAD), etag='"rel-etag-210"'),
        FetchResult(status_code=200, content=json.dumps(REPO_META_PAYLOAD), etag='"repo-etag-3500"'),
    ]

    res = adapter.execute(fetcher=mock_fetcher)

    assert res.allowed is True
    assert len(res.signals_emitted) == 2
    sigs = [s.payload if hasattr(s, "payload") else s for s in res.signals_emitted]
    assert any(p.get("tag_name") == "v2.1.0" for p in sigs)
    assert any(p.get("new_stars") == 3500 for p in sigs)


def test_github_target_token_added_to_headers():
    target = Target(id="gh_token", url="pallets/flask")
    adapter = GitHubTarget(target=target, token="ghp_token_123", watch_types=["releases"])

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        status_code=200,
        content=json.dumps(RELEASE_PAYLOAD),
        etag='"rel-etag-210"',
    )

    adapter.execute(fetcher=mock_fetcher)

    mock_fetcher.fetch.assert_called_once()
    call_kwargs = mock_fetcher.fetch.call_args.kwargs
    assert call_kwargs["custom_headers"]["Authorization"] == "Bearer ghp_token_123"
