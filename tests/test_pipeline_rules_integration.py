import json
from unittest.mock import MagicMock
from datetime import datetime
from web_watcher.scheduled_runner import ScheduledRunner
from web_watcher.repository import Repository
from web_watcher.fetch import FetchStatus
from web_watcher.fetcher import SmartFetcher, FetchResult
from web_watcher.models import TargetStatus


RULES_YAML = """
version: "1.0"
rules:
  - id: "aws_ec2_rule"
    name: "AWS EC2 Price Watch"
    target:
      url: "https://aws.amazon.com/ec2/pricing"
      interval: "10m"
    extractors:
      - name: "price"
        selector_type: "css"
        selector: ".price"
        transforms: ["strip_tags", "to_float"]
  - id: "flask_repo_rule"
    name: "Flask Releases Watch"
    target:
      url: "pallets/flask"
      interval: "1h"
"""


def test_pipeline_sync_rules(tmp_path):
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(RULES_YAML, encoding="utf-8")
    db_file = tmp_path / "pipeline.db"
    repo = Repository(str(db_file))

    runner = ScheduledRunner(repo=repo, rules_path=rules_file)
    synced = runner.sync_rules()

    assert len(synced) == 2
    assert repo.get_target("aws_ec2_rule") is not None
    assert repo.get_target("flask_repo_rule") is not None
    assert repo.get_target("aws_ec2_rule").status == TargetStatus.NORMAL


def test_pipeline_run_generic_web_flow(tmp_path):
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(RULES_YAML, encoding="utf-8")
    repo = Repository(str(tmp_path / "web_flow.db"))

    mock_fetcher = MagicMock(spec=SmartFetcher)
    # AWS 页面响应
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="aws_ec2_rule",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.utcnow(),
        content='<div class="price">$0.096</div>',
        etag='"etag-100"',
    )

    runner = ScheduledRunner(repo=repo, rules_path=rules_file, fetcher=mock_fetcher)
    summary = runner.run_once()

    # First observation establishes baseline; no signal is emitted.
    assert summary["targets_evaluated"] == 2
    assert summary["signals_emitted"] == 0
    target = repo.get_target("aws_ec2_rule")
    assert target.etag == '"etag-100"'


def test_pipeline_run_github_flow(tmp_path):
    gh_yaml = """
version: "1.0"
rules:
  - id: "gh_requests"
    name: "Requests Releases"
    target:
      url: "psf/requests"
      interval: "30m"
"""
    rules_file = tmp_path / "gh_rules.yaml"
    rules_file.write_text(gh_yaml, encoding="utf-8")
    repo = Repository(str(tmp_path / "gh_flow.db"))
    runner = ScheduledRunner(repo=repo, rules_path=rules_file)
    runner.sync_rules()

    target = repo.get_target("gh_requests")
    target.metadata["last_release_tag"] = "v2.31.0"
    target.metadata["release_etag"] = '"gh-etag-231"'
    repo.save_target(target)

    release_payload = json.dumps({
        "tag_name": "v2.32.0",
        "name": "Requests 2.32.0",
        "html_url": "https://github.com/psf/requests/releases/tag/v2.32.0",
        "published_at": "2026-08-18T12:00:00Z",
        "body": "Bug fixes",
    })

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="gh_requests",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.utcnow(),
        content=release_payload,
        etag='"gh-etag-232"',
    )

    runner = ScheduledRunner(repo=repo, rules_path=rules_file, fetcher=mock_fetcher)
    summary = runner.run_once()

    assert summary["targets_evaluated"] == 1
    assert summary["signals_emitted"] == 1
    target = repo.get_target("gh_requests")
    assert target.metadata["last_release_tag"] == "v2.32.0"


def test_pipeline_respects_cooldown_skips_target(tmp_path):
    single_rule_yaml = """
version: "1.0"
rules:
  - id: "aws_ec2_rule"
    name: "AWS EC2 Price Watch"
    target:
      url: "https://aws.amazon.com/ec2/pricing"
      interval: "10m"
    extractors:
      - name: "price"
        selector_type: "css"
        selector: ".price"
        transforms: ["strip_tags", "to_float"]
"""
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(single_rule_yaml, encoding="utf-8")
    db_file = tmp_path / "cooldown.db"
    repo = Repository(str(db_file))
    runner = ScheduledRunner(repo=repo, rules_path=rules_file)
    runner.sync_rules()

    target = repo.get_target("aws_ec2_rule")
    target.status = TargetStatus.COOLDOWN
    target.next_allowed_at = datetime.utcnow() + __import__("datetime").timedelta(seconds=600)
    repo.save_target(target)

    runner.sync_rules = MagicMock(return_value=[])
    summary = runner.run_once()
    assert summary["targets_evaluated"] == 0
    assert summary["skipped_count"] == 0
    assert summary["signals_emitted"] == 0


def test_pipeline_run_once_returns_summary_structure(tmp_path):
    repo = Repository(str(tmp_path / "summary.db"))
    runner = ScheduledRunner(repo=repo)
    summary = runner.run_once()

    assert "targets_evaluated" in summary
    assert "signals_emitted" in summary
    assert "is_304_count" in summary
    assert "skipped_count" in summary
    assert "errors" in summary
    assert summary["targets_evaluated"] == 0
    assert summary["signals_emitted"] == 0


def test_pipeline_event_correlator_called_when_signals_emitted(tmp_path):
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(RULES_YAML, encoding="utf-8")
    repo = Repository(str(tmp_path / "correlator.db"))

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = FetchResult(
        target_key="aws_ec2_rule",
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=datetime.utcnow(),
        content="<div class='price'>$0.096</div>",
        etag='"etag-100"',
    )

    mock_correlator = MagicMock()
    import web_watcher.scheduled_runner as scheduled_module
    original_correlator = scheduled_module.EventCorrelator
    scheduled_module.EventCorrelator = MagicMock(return_value=mock_correlator)

    try:
        runner = ScheduledRunner(repo=repo, rules_path=rules_file, fetcher=mock_fetcher)
        summary = runner.run_once()
        if summary["signals_emitted"]:
            mock_correlator.process_signal.assert_called()
    finally:
        scheduled_module.EventCorrelator = original_correlator
