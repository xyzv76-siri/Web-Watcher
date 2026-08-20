import json
from unittest.mock import MagicMock
from datetime import datetime
from web_watcher.models import Target, TargetStatus
from web_watcher.fetch import FetchStatus
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
        target_key="gh_flask",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.utcnow(),
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
    assert res.updated_metadata["last_release_tag"] == "v2.1.0"
    assert res.updated_metadata["release_etag"] == '"rel-etag-210"'


def test_github_target_no_release_signal_when_unchanged():
    target = Target(id="gh_flask", url="pallets/flask", metadata={"last_release_tag": "v2.1.0", "release_etag": '"rel-etag-210"'})
    adapter = GitHubTarget(target=target, watch_types=["releases"])

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="gh_flask",
        status=FetchStatus.NOT_MODIFIED,
        status_code=304,
        fetched_at=datetime.utcnow(),
        content=None,
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
        target_key="gh_flask",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.utcnow(),
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
        target_key="gh_flask",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.utcnow(),
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
        target_key="gh_save",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.utcnow(),
        content=json.dumps(RELEASE_PAYLOAD),
        etag='"rel-etag-210"',
    )

    mock_repo = MagicMock()
    mock_repo.save_target.return_value = None
    mock_repo.save_signal.return_value = None

    res = adapter.execute(fetcher=mock_fetcher, repo=mock_repo)

    assert res.allowed is True
    assert len(res.signals_emitted) == 1
    # Package A contract: adapter must not persist directly
    mock_repo.save_target.assert_not_called()
    mock_repo.save_signal.assert_not_called()
    # Adapter returns observation-only state for scheduler to commit
    assert res.updated_metadata.get("release_etag") == '"rel-etag-210"'
    assert res.outcome == "success_changed"


def test_github_target_mixed_watch_types_emits_both_signals():
    target = Target(
        id="gh_mixed",
        url="pallets/flask",
        metadata={"last_release_tag": "v2.0.0", "last_stars": 3499},
    )
    adapter = GitHubTarget(target=target, watch_types=["releases", "stars"])

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.side_effect = [
        FetchResult(
            target_key="gh_mixed",
            status=FetchStatus.SUCCESS,
            status_code=200,
            fetched_at=datetime.utcnow(),
            content=json.dumps(RELEASE_PAYLOAD),
            etag='"rel-etag-210"',
        ),
        FetchResult(
            target_key="gh_mixed",
            status=FetchStatus.SUCCESS,
            status_code=200,
            fetched_at=datetime.utcnow(),
            content=json.dumps(REPO_META_PAYLOAD),
            etag='"repo-etag-3500"',
        ),
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
        target_key="gh_token",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.utcnow(),
        content=json.dumps(RELEASE_PAYLOAD),
        etag='"rel-etag-210"',
    )

    adapter.execute(fetcher=mock_fetcher)

    mock_fetcher.fetch.assert_called_once()
    call_kwargs = mock_fetcher.fetch.call_args.kwargs
    assert call_kwargs["custom_headers"]["Authorization"] == "Bearer ghp_token_123"


def test_github_target_release_source_url_in_payload():
    target = Target(id="gh_src", url="pallets/flask", metadata={"last_release_tag": "v2.0.0"})
    adapter = GitHubTarget(target=target, watch_types=["releases"])

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="gh_src",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.utcnow(),
        content=json.dumps(RELEASE_PAYLOAD),
        etag='"rel-etag-210"',
    )

    res = adapter.execute(fetcher=mock_fetcher)

    assert len(res.signals_emitted) == 1
    sig = res.signals_emitted[0]
    payload = sig.payload if hasattr(sig, "payload") else sig
    assert payload["source"] == "https://api.github.com/repos/pallets/flask/releases/latest"
    assert payload["fetched_at"] is not None


def test_github_target_stars_source_url_in_payload():
    target = Target(id="gh_stars_src", url="pallets/flask", metadata={"last_stars": 3499})
    adapter = GitHubTarget(target=target, watch_types=["stars"], star_delta_threshold=1)

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="gh_stars_src",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.utcnow(),
        content=json.dumps(REPO_META_PAYLOAD),
        etag='"repo-etag-3500"',
    )

    res = adapter.execute(fetcher=mock_fetcher)

    assert len(res.signals_emitted) == 1
    sig = res.signals_emitted[0]
    payload = sig.payload if hasattr(sig, "payload") else sig
    assert payload["source"] == "https://api.github.com/repos/pallets/flask"
    assert payload["fetched_at"] is not None


def test_github_target_release_first_observation_no_signal():
    target = Target(id="gh_first_rel", url="pallets/flask", metadata={})
    adapter = GitHubTarget(target=target, watch_types=["releases"])

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="gh_first_rel",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.utcnow(),
        content=json.dumps(RELEASE_PAYLOAD),
        etag='"rel-etag-210"',
    )

    res = adapter.execute(fetcher=mock_fetcher)

    assert res.allowed is True
    assert len(res.signals_emitted) == 0
    assert res.updated_metadata.get("last_release_tag") == "v2.1.0"
    assert res.updated_metadata.get("release_etag") == '"rel-etag-210"'


def test_github_target_stars_first_observation_no_signal():
    target = Target(id="gh_first_stars", url="pallets/flask", metadata={})
    adapter = GitHubTarget(target=target, watch_types=["stars"], star_delta_threshold=1)

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="gh_first_stars",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.utcnow(),
        content=json.dumps(REPO_META_PAYLOAD),
        etag='"repo-etag-3500"',
    )

    res = adapter.execute(fetcher=mock_fetcher)

    assert res.allowed is True
    assert len(res.signals_emitted) == 0
    assert res.updated_metadata.get("last_stars") == 3500
    assert res.updated_metadata.get("repo_etag") == '"repo-etag-3500"'


def test_github_target_release_missing_tag_name_no_signal():
    target = Target(id="gh_no_tag", url="pallets/flask", metadata={"last_release_tag": "v2.0.0"})
    adapter = GitHubTarget(target=target, watch_types=["releases"])

    payload = dict(RELEASE_PAYLOAD)
    del payload["tag_name"]

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="gh_no_tag",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.utcnow(),
        content=json.dumps(payload),
        etag='"rel-etag-210"',
    )

    res = adapter.execute(fetcher=mock_fetcher)

    assert res.allowed is True
    assert len(res.signals_emitted) == 0
    assert res.outcome == "success_unchanged"


def test_github_target_malformed_json_release_transform_error():
    target = Target(id="gh_malformed", url="pallets/flask", metadata={"last_release_tag": "v2.0.0"})
    adapter = GitHubTarget(target=target, watch_types=["releases"])

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="gh_malformed",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.utcnow(),
        content="not json",
        etag='"rel-etag-210"',
    )

    res = adapter.execute(fetcher=mock_fetcher)

    assert res.allowed is True
    assert len(res.signals_emitted) == 0
    assert res.outcome == "transform_error"


def test_github_target_per_target_etag_isolation():
    target_a = Target(id="gh_a", url="openai/gpt-4o", metadata={})
    target_b = Target(id="gh_b", url="pallets/flask", metadata={})

    adapter_a = GitHubTarget(target=target_a, watch_types=["stars"])
    adapter_b = GitHubTarget(target=target_b, watch_types=["stars"])

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.side_effect = [
        FetchResult(
            target_key="gh_a",
            status=FetchStatus.SUCCESS,
            status_code=200,
            fetched_at=datetime.utcnow(),
            content=json.dumps({"stargazers_count": 100}),
            etag='"etag-a"',
        ),
        FetchResult(
            target_key="gh_b",
            status=FetchStatus.SUCCESS,
            status_code=200,
            fetched_at=datetime.utcnow(),
            content=json.dumps({"stargazers_count": 200}),
            etag='"etag-b"',
        ),
    ]

    res_a = adapter_a.execute(fetcher=mock_fetcher)
    res_b = adapter_b.execute(fetcher=mock_fetcher)

    assert res_a.updated_metadata.get("repo_etag") == '"etag-a"'
    assert res_b.updated_metadata.get("repo_etag") == '"etag-b"'
    assert res_a.updated_metadata.get("last_stars") == 100
    assert res_b.updated_metadata.get("last_stars") == 200


def test_github_target_restart_etag_stable():
    """Simulate restart: ETag and last stars survive through metadata."""
    target = Target(
        id="gh_restart",
        url="pallets/flask",
        metadata={
            "last_stars": 3500,
            "repo_etag": '"repo-etag-3500"',
            "last_release_tag": "v2.1.0",
            "release_etag": '"rel-etag-210"',
        },
    )
    adapter = GitHubTarget(target=target, watch_types=["releases", "stars"], star_delta_threshold=1)

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.side_effect = [
        FetchResult(
            target_key="gh_restart",
            status=FetchStatus.NOT_MODIFIED,
            status_code=304,
            fetched_at=datetime.utcnow(),
            content=None,
            etag='"rel-etag-210"',
        ),
        FetchResult(
            target_key="gh_restart",
            status=FetchStatus.NOT_MODIFIED,
            status_code=304,
            fetched_at=datetime.utcnow(),
            content=None,
            etag='"repo-etag-3500"',
        ),
    ]

    res = adapter.execute(fetcher=mock_fetcher)

    assert res.allowed is True
    assert len(res.signals_emitted) == 0
    assert res.is_304 is True
    assert res.updated_metadata.get("repo_etag") == '"repo-etag-3500"'
    assert res.updated_metadata.get("release_etag") == '"rel-etag-210"'
    assert res.updated_metadata.get("last_stars") == 3500
    assert res.updated_metadata.get("last_release_tag") == "v2.1.0"


def test_github_target_authentication_isolation():
    """Token is per-target; two targets with different tokens get different headers."""
    target_a = Target(id="gh_auth_a", url="openai/gpt-4o")
    target_b = Target(id="gh_auth_b", url="pallets/flask", metadata={"token": "ghp_token_b"})

    adapter_a = GitHubTarget(target=target_a, token="ghp_token_a", watch_types=["stars"])
    adapter_b = GitHubTarget(target=target_b, token="ghp_token_b", watch_types=["stars"])

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="gh_auth_a",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.utcnow(),
        content=json.dumps({"stargazers_count": 100}),
        etag='"etag-a"',
    )

    adapter_a.execute(fetcher=mock_fetcher)
    call_a = mock_fetcher.fetch.call_args.kwargs["custom_headers"]["Authorization"]

    mock_fetcher.fetch.reset_mock()
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="gh_auth_b",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.utcnow(),
        content=json.dumps({"stargazers_count": 200}),
        etag='"etag-b"',
    )

    adapter_b.execute(fetcher=mock_fetcher)
    call_b = mock_fetcher.fetch.call_args.kwargs["custom_headers"]["Authorization"]

    assert call_a == "Bearer ghp_token_a"
    assert call_b == "Bearer ghp_token_b"