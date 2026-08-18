"""CLI interface for Web Watcher."""

import argparse
import logging
import sys
from typing import List, Optional

from .investigation_worker import InvestigationWorker
from .repository import Repository

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="web-watcher",
        description="Web Watcher: autonomous web monitoring and investigation engine",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Worker subcommand (K.8)
    worker_parser = subparsers.add_parser(
        "worker",
        help="Run autonomous background investigation worker",
    )
    worker_parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single batch polling iteration and exit",
    )
    worker_parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds (default: 1.0)",
    )
    worker_parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of uninvestigated events to fetch per batch (default: 10)",
    )
    worker_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )

    return parser


def handle_worker(args: argparse.Namespace) -> int:
    """Handles execution of the worker subcommand."""
    db_path = getattr(args, "db_path", "web_watcher.db")
    batch_size = getattr(args, "batch_size", 10)
    interval = getattr(args, "interval", 1.0)
    run_once = getattr(args, "once", False)

    repo = Repository(db_path)
    worker = InvestigationWorker(
        repository=repo,
        batch_size=batch_size,
        poll_interval=interval,
    )

    if run_once:
        count = worker.run_once()
        print(f"Investigation worker finished batch run. Processed {count} event(s).")
        return 0

    print(f"Starting InvestigationWorker (interval={interval}s, batch_size={batch_size}, db={db_path})...")
    print("Press Ctrl+C to stop.")
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        worker.stop()
        print("\nWorker stopped by user.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.command == "worker":
        return handle_worker(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
