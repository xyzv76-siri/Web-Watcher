import pytest
from unittest.mock import MagicMock
from datetime import datetime
from pathlib import Path
from web_watcher.cli import handle_inspect
from web_watcher.fetch import FetchStatus
from web_watcher.models import Target, TargetStatus


HTML_SAMPLE = """
<div class="pricing">
    <div class="plan">Pro</div>
    <div class="price">$99.00</div>
</div>
"""


class Args:
    rule = "config/rules_inspect_test.yaml"
    url = None
    html_file = None
    inspect_extractor = None
    verbose = False


def test_inspect_with_local_html(capsys, tmp_path):
    rule_file = tmp_path / "rule.yaml"
    rule_file.write_text(
        """
version: "1.0"
rules:
  - id: inspect_test
    name: Inspect Test Rule
    target:
      url: https://example.com/pricing
      interval: 15m
      timeout: 10.0
    extractors:
      - name: price
        selector_type: css
        selector: div.pricing .price
        transforms:
          - strip_tags
          - to_float
    triggers:
      - type: field_change
        field: price
        importance: important
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
"""
    )
    html_file = tmp_path / "sample.html"
    html_file.write_text(HTML_SAMPLE)

    args = Args()
    args.rule = str(rule_file)
    args.html_file = str(html_file)

    rc = handle_inspect(args)
    assert rc == 0

    out = capsys.readouterr().out
    assert "Inspect Rule [inspect_test]" in out
    assert "HTML Source: local file" in out
    assert "status=first_observation" in out
    assert "normalized_value: 99.0" in out


def test_inspect_scope_miss(capsys, tmp_path):
    rule_file = tmp_path / "rule.yaml"
    rule_file.write_text(
        """
version: "1.0"
rules:
  - id: inspect_scope_miss
    name: Inspect Scope Miss
    target:
      url: https://example.com/pricing
      interval: 15m
      timeout: 10.0
    extractors:
      - name: price
        selector_type: css
        selector: div.pricing
        scope_selector: .non-existent
        transforms:
          - strip_tags
          - to_float
    triggers:
      - type: field_change
        field: price
        importance: important
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
"""
    )
    html_file = tmp_path / "sample.html"
    html_file.write_text(HTML_SAMPLE)

    args = Args()
    args.rule = str(rule_file)
    args.html_file = str(html_file)

    rc = handle_inspect(args)
    assert rc == 0

    out = capsys.readouterr().out
    assert "status=first_observation" in out
    assert "outcome=ExecutionOutcome.SELECTOR_NOT_FOUND" in out
    assert "status: not_found" in out
    assert "scope_miss: True" in out


def test_inspect_missing_input_returns_error(capsys, tmp_path):
    rule_file = tmp_path / "rule.yaml"
    rule_file.write_text(
        """
version: "1.0"
rules:
  - id: inspect_test
    name: Inspect Test Rule
    target:
      url: https://example.com/pricing
      interval: 15m
      timeout: 10.0
    extractors: []
    triggers: []
    routing:
      channels:
        - console
      cooldown: 300s
    status: enabled
    tags: []
"""
    )

    args = Args()
    args.rule = str(rule_file)
    args.url = None
    args.html_file = None

    rc = handle_inspect(args)
    assert rc == 1

    out = capsys.readouterr().out
    assert "[ERROR] Provide either --url or --html-file." in out
