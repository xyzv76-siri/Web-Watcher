"""Unit tests for CLI --version flag (Phase 14-C)."""

import pytest
from web_watcher.cli import build_parser


def test_cli_version_flag():
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--version"])
    assert exc_info.value.code == 0
