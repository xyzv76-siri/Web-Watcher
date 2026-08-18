"""Unit tests for CLI worker subcommand."""

from unittest.mock import MagicMock, patch
import pytest

from web_watcher.cli import build_parser, handle_worker, main
from web_watcher.config import AppConfig


def test_build_parser_worker_defaults():
    parser = build_parser()
    args = parser.parse_args(["worker"])
    assert args.command == "worker"
    assert args.once is False
    assert args.interval == 1.0
    assert args.batch_size == 10
    assert args.db_path == "web_watcher.db"


def test_build_parser_worker_custom_flags():
    parser = build_parser()
    args = parser.parse_args(["worker", "--once", "--interval", "3.5", "--batch-size", "25", "--db", ":memory:"])
    assert args.command == "worker"
    assert args.once is True
    assert args.interval == 3.5
    assert args.batch_size == 25
    assert args.db_path == ":memory:"


@patch("web_watcher.cli.InvestigationWorker")
@patch("web_watcher.cli.Repository")
def test_handle_worker_once_mode(mock_repo_cls, mock_worker_cls, capsys):
    mock_worker = MagicMock()
    mock_worker.run_once.return_value = 3
    mock_worker_cls.return_value = mock_worker

    parser = build_parser()
    args = parser.parse_args(["worker", "--once", "--db", ":memory:"])
    exit_code = handle_worker(args, AppConfig())

    assert exit_code == 0
    mock_worker.run_once.assert_called_once()
    mock_worker.run_forever.assert_not_called()
    captured = capsys.readouterr()
    assert "Processed 3 event(s)" in captured.out


@patch("web_watcher.cli.InvestigationWorker")
@patch("web_watcher.cli.Repository")
def test_handle_worker_interrupt_handling(mock_repo_cls, mock_worker_cls, capsys):
    mock_worker = MagicMock()
    mock_worker.run_forever.side_effect = KeyboardInterrupt
    mock_worker_cls.return_value = mock_worker

    parser = build_parser()
    args = parser.parse_args(["worker"])
    exit_code = handle_worker(args, AppConfig())

    assert exit_code == 0
    mock_worker.stop.assert_called_once()
    captured = capsys.readouterr()
    assert "Worker stopped by user" in captured.out


def test_main_help_when_no_args(capsys):
    exit_code = main([])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "usage: web-watcher" in captured.out
