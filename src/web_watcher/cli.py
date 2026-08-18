"""CLI interface for Web Watcher (Phase 12-C Final)."""

import argparse
import logging
import sys
import time
from typing import List, Optional

from .channel_senders import WebhookSender
from .event_correlator import EventCorrelator
from .exporter import AuditExporter
from .investigation_worker import InvestigationWorker
from .notification_dispatcher import NotificationDispatcher
from .pipeline_runner import PipelineRunner
from .repository import Repository
from .doctor import SystemDoctor

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="web-watcher",
        description="Web Watcher: autonomous web monitoring, investigation, and notification engine",
    )
    parser.add_argument("--version", action="version", version="web-watcher 0.1.0")
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # 1. worker subcommand (investigation background worker)
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

    # 2. notify subcommand (notification delivery worker)
    notify_parser = subparsers.add_parser(
        "notify",
        help="Run autonomous background notification delivery worker",
    )
    notify_parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single batch notification dispatch iteration and exit",
    )
    notify_parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds (default: 1.0)",
    )
    notify_parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of pending notifications to fetch per batch (default: 10)",
    )
    notify_parser.add_argument(
        "--webhook-url",
        type=str,
        default=None,
        help="Optional webhook endpoint URL for webhook channel delivery",
    )
    notify_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )

    # 3. run subcommand (pipeline execution)
    run_parser = subparsers.add_parser(
        "run",
        help="Execute monitoring pipeline cycle",
    )
    run_parser.add_argument(
        "--once",
        action="store_true",
        help="Execute a single pipeline cycle and exit",
    )
    run_parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Polling interval in seconds for continuous mode (default: 5.0)",
    )
    run_parser.add_argument(
        "--enable-auto-investigate",
        "--auto-investigate",
        dest="auto_investigate",
        action="store_true",
        help="Enable automatic investigation dispatch on important events",
    )
    run_parser.add_argument(
        "--enable-auto-deliver",
        "--auto-deliver",
        dest="auto_deliver",
        action="store_true",
        help="Enable immediate notification delivery dispatch upon creation",
    )
    run_parser.add_argument(
        "--webhook-url",
        type=str,
        default=None,
        help="Optional webhook URL for delivery",
    )
    run_parser.add_argument(
        "--channel",
        type=str,
        default="webhook",
        help="Notification delivery channel (default: webhook)",
    )
    run_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )

    # 4. daemon subcommand (long-running service)
    daemon_parser = subparsers.add_parser(
        "daemon",
        help="Run monitoring daemon loop",
    )
    daemon_parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Daemon loop polling interval in seconds (default: 5.0)",
    )
    daemon_parser.add_argument(
        "--enable-auto-investigate",
        "--auto-investigate",
        dest="auto_investigate",
        action="store_true",
        help="Enable automatic investigation dispatch on important events",
    )
    daemon_parser.add_argument(
        "--enable-auto-deliver",
        "--auto-deliver",
        dest="auto_deliver",
        action="store_true",
        help="Enable immediate notification delivery dispatch upon creation",
    )
    daemon_parser.add_argument(
        "--webhook-url",
        type=str,
        default=None,
        help="Optional webhook URL for delivery",
    )
    daemon_parser.add_argument(
        "--channel",
        type=str,
        default="webhook",
        help="Notification delivery channel (default: webhook)",
    )
    daemon_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )

    # 5. export subcommand (Phase 13-C)
    export_parser = subparsers.add_parser(
        "export",
        help="Export offline audit report (Markdown/HTML)",
    )
    export_parser.add_argument(
        "--format",
        "-f",
        choices=["markdown", "html", "md"],
        default="markdown",
        help="Export format (default: markdown)",
    )
    export_parser.add_argument(
        "--since",
        "-s",
        default="24h",
        help="Time filter (e.g. 24h, 7d, 30m)",
    )
    export_parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output file path (default: print to stdout)",
    )
    export_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )

    # 6. doctor subcommand (Phase 14-B)
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Run system health self-check and diagnostics",
    )
    doctor_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed self-check output",
    )
    doctor_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )

    return parser


def handle_worker(args: argparse.Namespace) -> int:
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


def _build_dispatcher(repo: Repository, webhook_url: Optional[str]) -> NotificationDispatcher:
    dispatcher = NotificationDispatcher(repository=repo)
    if webhook_url:
        dispatcher.register_sender("webhook", WebhookSender(webhook_url=webhook_url))
    return dispatcher


def handle_notify(args: argparse.Namespace) -> int:
    db_path = getattr(args, "db_path", "web_watcher.db")
    batch_size = getattr(args, "batch_size", 10)
    interval = getattr(args, "interval", 1.0)
    webhook_url = getattr(args, "webhook_url", None)
    run_once = getattr(args, "once", False)

    repo = Repository(db_path)
    dispatcher = _build_dispatcher(repo, webhook_url)

    if run_once:
        count = dispatcher.run_once()
        print(f"Notification dispatcher finished batch run. Processed {count} notification(s).")
        return 0

    print(f"Starting NotificationDispatcher (interval={interval}s, batch_size={batch_size}, db={db_path})...")
    print("Press Ctrl+C to stop.")
    try:
        dispatcher.run_forever()
    except KeyboardInterrupt:
        dispatcher.stop()
        print("\nDispatcher stopped by user.")
    return 0


def handle_run(args: argparse.Namespace) -> int:
    db_path = getattr(args, "db_path", "web_watcher.db")
    interval = getattr(args, "interval", 5.0)
    run_once = getattr(args, "once", False)
    auto_inv = getattr(args, "auto_investigate", False)
    auto_deliver = getattr(args, "auto_deliver", False)
    webhook_url = getattr(args, "webhook_url", None)
    channel = getattr(args, "channel", "webhook")

    repo = Repository(db_path)
    dispatcher = _build_dispatcher(repo, webhook_url) if auto_deliver else None

    correlator = EventCorrelator(
        repository=repo,
        auto_investigate=auto_inv,
    )
    runner = PipelineRunner(
        repository=repo,
        correlator=correlator,
        dispatcher=dispatcher,
        auto_notify=True,
        auto_deliver=auto_deliver,
        notify_channel=channel,
    )
    print(f"Pipeline run initialized (auto_investigate={auto_inv}, auto_deliver={auto_deliver}, db={db_path}).")
    if run_once:
        print("Completed single pipeline cycle.")
        return 0

    print(f"Starting continuous pipeline run (interval={interval}s, auto_investigate={auto_inv})... Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nPipeline run stopped by user.")
    return 0


def handle_daemon(args: argparse.Namespace) -> int:
    db_path = getattr(args, "db_path", "web_watcher.db")
    interval = getattr(args, "interval", 5.0)
    auto_inv = getattr(args, "auto_investigate", False)
    auto_deliver = getattr(args, "auto_deliver", False)
    webhook_url = getattr(args, "webhook_url", None)
    channel = getattr(args, "channel", "webhook")

    repo = Repository(db_path)
    dispatcher = _build_dispatcher(repo, webhook_url) if auto_deliver else None

    correlator = EventCorrelator(
        repository=repo,
        auto_investigate=auto_inv,
    )
    runner = PipelineRunner(
        repository=repo,
        correlator=correlator,
        dispatcher=dispatcher,
        auto_notify=True,
        auto_deliver=auto_deliver,
        notify_channel=channel,
    )
    print(f"Starting web-watcher daemon (interval={interval}s, auto_investigate={auto_inv}, db={db_path})...")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nDaemon stopped by user.")
    return 0


def handle_export(args: argparse.Namespace) -> int:
    db_path = getattr(args, "db_path", "web_watcher.db")
    fmt = getattr(args, "format", "markdown")
    since = getattr(args, "since", "24h")
    output = getattr(args, "output", None)

    repo = Repository(db_path)
    exporter = AuditExporter(repo)

    if fmt == "html":
        content = exporter.export_html(since)
    else:
        content = exporter.export_markdown(since)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[OK] Audit report exported to {output}")
    else:
        print(content)
    return 0


def handle_doctor(args: argparse.Namespace) -> int:
    db_path = getattr(args, "db_path", "web_watcher.db")
    verbose = getattr(args, "verbose", False)

    repo = None
    try:
        repo = Repository(db_path)
    except Exception:
        pass

    doctor = SystemDoctor(repo=repo, db_path=db_path)
    results = doctor.run_all()
    report = doctor.render_report(results)
    if verbose:
        for r in results:
            print(f"[{r.status}] {r.name}: {r.message}")
            if r.details:
                for k, v in r.details.items():
                    print(f"    {k}: {v}")
    print(report)
    return 1 if any(r.status == "FAIL" for r in results) else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.command == "worker":
        return handle_worker(args)
    if args.command == "notify":
        return handle_notify(args)
    if args.command == "run":
        return handle_run(args)
    if args.command == "daemon":
        return handle_daemon(args)
    if args.command == "export":
        return handle_export(args)
    if args.command == "doctor":
        return handle_doctor(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
