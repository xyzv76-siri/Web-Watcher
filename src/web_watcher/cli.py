"""CLI interface for Web Watcher (Phase 12-C Final)."""

import argparse
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import List, Optional

import yaml

from .channel_senders import WebhookSender
from .event_correlator import EventCorrelator
from .exporter import AuditExporter, parse_since
from .investigation_worker import InvestigationWorker
from .notification_dispatcher import NotificationDispatcher
from .pipeline_runner import PipelineRunner
from .repository import Repository
from .doctor import SystemDoctor
from .retention import RetentionManager, RetentionPolicy
from .config import get_config, AppConfig
from .rule_parser import RuleParser
from .rule_evaluator import RuleEvaluator
from .rule_models import WatcherRule
from .presets import get_preset, list_presets

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
        "--entity-id",
        dest="export_entity_ids",
        action="append",
        type=int,
        default=None,
        help="Filter by entity ID (repeatable)",
    )
    export_parser.add_argument(
        "--event-type",
        dest="export_event_types",
        action="append",
        default=None,
        help="Filter by event type (repeatable)",
    )
    export_parser.add_argument(
        "--importance",
        dest="export_importances",
        action="append",
        default=None,
        help="Filter by importance (repeatable)",
    )
    export_parser.add_argument(
        "--status",
        dest="export_statuses",
        action="append",
        default=None,
        help="Filter by event status (repeatable)",
    )
    export_parser.add_argument(
        "--channel",
        dest="export_channels",
        action="append",
        default=None,
        help="Filter by notification channel (repeatable)",
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

    # 7. retention subcommand (Phase 16-B)
    retention_parser = subparsers.add_parser(
        "retention",
        help="Enforce data retention policy",
    )
    retention_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview deletion counts without removing data",
    )
    retention_parser.add_argument(
        "--max-age-days",
        "--days",
        dest="max_age_days",
        type=int,
        default=None,
        help="Override retention max age in days (default: from AppConfig)",
    )
    retention_parser.add_argument(
        "--entity-id",
        dest="retention_entity_ids",
        action="append",
        type=int,
        default=None,
        help="Filter by entity ID (repeatable)",
    )
    retention_parser.add_argument(
        "--event-type",
        dest="retention_event_types",
        action="append",
        default=None,
        help="Filter by event type (repeatable)",
    )
    retention_parser.add_argument(
        "--importance",
        dest="retention_importances",
        action="append",
        default=None,
        help="Filter by importance (repeatable)",
    )
    retention_parser.add_argument(
        "--status",
        dest="retention_statuses",
        action="append",
        default=None,
        help="Filter by event status (repeatable)",
    )
    retention_parser.add_argument(
        "--channel",
        dest="retention_channels",
        action="append",
        default=None,
        help="Filter by notification channel (repeatable)",
    )
    retention_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )

    # 8. test-rule subcommand (Phase 17-C)
    test_rule_parser = subparsers.add_parser(
        "test-rule",
        help="Test and debug a YAML rule file",
    )
    test_rule_parser.add_argument("rule_file", help="Path to YAML rule file")
    test_rule_parser.add_argument("--html-file", default=None, help="Local HTML file to evaluate against")
    test_rule_parser.add_argument("--url", default=None, help="Remote URL to fetch and evaluate")

    # 9. template subcommand (Preset)
    template_parser = subparsers.add_parser(
        "template",
        help="Generate monitoring rules from presets",
    )
    template_subparsers = template_parser.add_subparsers(dest="template_command", help="Template commands")

    # template list
    template_list_parser = template_subparsers.add_parser(
        "list",
        help="List available presets",
    )

    # template show
    template_show_parser = template_subparsers.add_parser(
        "show",
        help="Show preset details",
    )
    template_show_parser.add_argument("preset", help="Preset name")

    # template apply
    template_apply_parser = template_subparsers.add_parser(
        "apply",
        help="Generate a rules YAML from a preset",
    )
    template_apply_parser.add_argument("preset", help="Preset name")
    template_apply_parser.add_argument("--url", required=True, help="Target URL")
    template_apply_parser.add_argument("--repo", default=None, help="GitHub owner/repo (for github_* presets)")
    template_apply_parser.add_argument("--selector", default=None, help="CSS selector (for blog_post/price presets)")
    template_apply_parser.add_argument("--interval", default=None, help="Monitoring interval (e.g. 15m, 1h)")
    template_apply_parser.add_argument("--channel", default=None, help="Notification channel (default: console)")
    template_apply_parser.add_argument("--cooldown", default=None, help="Cooldown duration (e.g. 300s)")
    template_apply_parser.add_argument("--rule-id", default=None, help="Rule ID override")
    template_apply_parser.add_argument("--name", default=None, help="Rule name override")
    template_apply_parser.add_argument("--output", "-o", default=None, help="Output file path (default: stdout)")

    # 10. rules subcommand (Observability v1)
    rules_parser = subparsers.add_parser(
        "rules",
        help="Inspect and manage YAML rules",
    )
    rules_subparsers = rules_parser.add_subparsers(dest="rules_command", help="Rules commands")
    rules_parser.add_argument(
        "--rules",
        dest="rules_path",
        type=str,
        default=None,
        help="Path to YAML rules file (default: WEB_WATCHER_RULES or config/rules.yaml)",
    )

    # rules list
    rules_list_parser = rules_subparsers.add_parser(
        "list",
        help="List all rules with id, name, status, and target URL",
    )

    # rules show
    rules_show_parser = rules_subparsers.add_parser(
        "show",
        help="Show details of a specific rule",
    )
    rules_show_parser.add_argument("rule_id", help="Rule ID to show")

    # rules enable
    rules_enable_parser = rules_subparsers.add_parser(
        "enable",
        help="Enable a rule",
    )
    rules_enable_parser.add_argument("rule_id", help="Rule ID to enable")

    # rules disable
    rules_disable_parser = rules_subparsers.add_parser(
        "disable",
        help="Disable a rule",
    )
    rules_disable_parser.add_argument("rule_id", help="Rule ID to disable")

    # 11. notify history subcommand (Observability v1)
    notify_parser.add_argument(
        "--history",
        dest="notify_history",
        action="store_true",
        help="Show recent notification delivery history",
    )
    notify_parser.add_argument(
        "--history-limit",
        dest="notify_history_limit",
        type=int,
        default=20,
        help="Maximum number of history records to show (default: 20)",
    )

    return parser


def handle_worker(args: argparse.Namespace, config: AppConfig) -> int:
    db_path = getattr(args, "db_path", config.db_path)
    batch_size = getattr(args, "batch_size", config.default_batch_size)
    interval = getattr(args, "interval", config.default_poll_interval)
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


def _build_dispatcher(repo: Repository, webhook_url: Optional[str], config: AppConfig) -> NotificationDispatcher:
    dispatcher = NotificationDispatcher(
        repository=repo,
        max_retries=config.default_max_retries,
        base_backoff_sec=config.default_base_backoff_sec,
        poll_interval=config.default_poll_interval,
        batch_size=config.default_batch_size,
    )
    if webhook_url:
        dispatcher.register_sender("webhook", WebhookSender(webhook_url=webhook_url))
    return dispatcher


def handle_notify(args: argparse.Namespace, config: AppConfig) -> int:
    db_path = getattr(args, "db_path", config.db_path)
    batch_size = getattr(args, "batch_size", config.default_batch_size)
    interval = getattr(args, "interval", config.default_poll_interval)
    webhook_url = getattr(args, "webhook_url", None)
    run_once = getattr(args, "once", False)
    show_history = getattr(args, "notify_history", False)
    history_limit = getattr(args, "notify_history_limit", 20)

    if show_history:
        repo = Repository(db_path)
        cursor = repo.connection.execute(
            "SELECT id, event_id, channel, status, created_at, sent_at, payload FROM notifications ORDER BY created_at DESC LIMIT ?",
            (history_limit,),
        )
        rows = cursor.fetchall()
        if not rows:
            print("No notification history found.")
            return 0

        print(f"{'ID':<6} {'Event':<8} {'Channel':<12} {'Status':<14} {'Created At':<26} {'Sent At'}")
        print("-" * 90)
        for row in rows:
            nid, event_id, channel, status, created_at, sent_at, payload = row
            sent_str = sent_at if sent_at else "-"
            print(f"{nid:<6} {event_id:<8} {channel:<12} {status:<14} {created_at:<26} {sent_str}")
        return 0

    repo = Repository(db_path)
    dispatcher = _build_dispatcher(repo, webhook_url, config)

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


def handle_run(args: argparse.Namespace, config: AppConfig) -> int:
    db_path = getattr(args, "db_path", config.db_path)
    interval = getattr(args, "interval", config.default_poll_interval)
    run_once = getattr(args, "once", False)
    auto_inv = getattr(args, "auto_investigate", False)
    auto_deliver = getattr(args, "auto_deliver", False)
    webhook_url = getattr(args, "webhook_url", None)
    channel = getattr(args, "channel", "webhook")

    repo = Repository(db_path)
    dispatcher = _build_dispatcher(repo, webhook_url, config) if auto_deliver else None

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
        config=config,
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


def handle_daemon(args: argparse.Namespace, config: AppConfig) -> int:
    db_path = getattr(args, "db_path", config.db_path)
    interval = getattr(args, "interval", config.default_poll_interval)
    auto_inv = getattr(args, "auto_investigate", False)
    auto_deliver = getattr(args, "auto_deliver", False)
    webhook_url = getattr(args, "webhook_url", None)
    channel = getattr(args, "channel", "webhook")

    repo = Repository(db_path)
    dispatcher = _build_dispatcher(repo, webhook_url, config) if auto_deliver else None

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
        config=config,
    )
    print(f"Starting web-watcher daemon (interval={interval}s, auto_investigate={auto_inv}, db={db_path})...")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nDaemon stopped by user.")
    return 0


def handle_export(args: argparse.Namespace, config: AppConfig) -> int:
    db_path = getattr(args, "db_path", config.db_path)
    fmt = getattr(args, "format", "markdown")
    since = getattr(args, "since", "24h")
    output = getattr(args, "output", None)

    repo = Repository(db_path)
    exporter = AuditExporter(repo)

    since_dt = parse_since(since) if isinstance(since, str) else since
    data = exporter.collect_data(
        since=since_dt,
        entity_ids=getattr(args, "export_entity_ids", None),
        event_types=getattr(args, "export_event_types", None),
        importances=getattr(args, "export_importances", None),
        statuses=getattr(args, "export_statuses", None),
        channels=getattr(args, "export_channels", None),
    )
    events = data["events"]
    notifications = data["notifications"]

    if fmt == "html":
        lines = [
            "<!DOCTYPE html>",
            "<html lang=\"zh-CN\">",
            "<head>",
            "  <meta charset=\"UTF-8\">",
            "  <title>Web Watcher Audit Report</title>",
            "  <style>",
            "    body { font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif; margin: 40px; color: #333; }",
            "    h1 { border-bottom: 2px solid #eaecef; padding-bottom: 10px; }",
            "    .meta { background: #f6f8fa; padding: 12px; border-radius: 6px; margin-bottom: 20px; }",
            "    table { width: 100%; border-collapse: collapse; margin-top: 16px; }",
            "    th, td { border: 1px solid #dfe2e5; padding: 8px 12px; text-align: left; }",
            "    th { background-color: #f6f8fa; }",
            "    .badge { background: #0366d6; color: white; padding: 2px 6px; border-radius: 3px; font-size: 12px; }",
            "  </style>",
            "</head>",
            "<body>",
            "  <h1>Web Watcher 离线审计报告</h1>",
            f"  <div class=\"meta\">",
            f"    <p><strong>生成时间:</strong> {data['generated_at'].strftime('%Y-%m-%d %H:%M:%S')} UTC</p>",
            f"    <p><strong>时间跨度:</strong> {since or 'All'} | <strong>事件数:</strong> {len(events)} | <strong>通知数:</strong> {len(notifications)}</p>",
            "  </div>",
            "  <h2>事件列表</h2>",
            "  <table>",
            "    <thead>",
            "      <tr><th>Event ID</th><th>Title</th><th>Importance</th><th>Status</th></tr>",
            "    </thead>",
            "    <tbody>",
        ]

        if not events:
            lines.append("      <tr><td colspan=\"4\">暂无事件记录</td></tr>")
        else:
            for ev in events:
                ev_id = html.escape(str(getattr(ev, "id", getattr(ev, "event_id", "N/A"))))
                payload = getattr(ev, "payload", {}) or {}
                title = html.escape(str(payload.get("title") or getattr(ev, "title", getattr(ev, "event_type", "Event"))))
                importance = html.escape(str(getattr(ev, "importance", "unknown")))
                status = html.escape(str(getattr(ev, "status", "unknown")))
                lines.append(f"      <tr><td><code>{ev_id}</code></td><td>{title}</td><td><span class=\"badge\">{importance}</span></td><td>{status}</td></tr>")

        lines.append("    </tbody>")
        lines.append("  </table>")
        lines.append("</body>")
        lines.append("</html>")
        content = "\n".join(lines)
    else:
        lines = [
            "# Web Watcher 审计报告",
            "",
            f"- **生成时间 (UTC)**: {data['generated_at'].strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **时间范围 (Since)**: {since or 'All'}",
            f"- **事件总数**: {len(events)}",
            f"- **通知记录数**: {len(notifications)}",
            "",
            "## 事件明细与处置记录",
            "",
        ]

        if not events:
            lines.append("_在指定时间段内未发现事件记录。_")
        else:
            for ev in events:
                ev_id = getattr(ev, "id", getattr(ev, "event_id", "N/A"))
                payload = getattr(ev, "payload", {}) or {}
                title = payload.get("title") or getattr(ev, "title", getattr(ev, "event_type", "Event"))
                status = getattr(ev, "status", "unknown")
                importance = getattr(ev, "importance", "info")
                lines.append(f"### 事件: {title} (`{ev_id}`)")
                lines.append(f"- **状态**: `{status}` | **重要级别**: `{importance}`")
                desc = payload.get("body") or getattr(ev, "description", None)
                if desc:
                    lines.append(f"- **描述**: {desc}")

                rel_notifs = [
                    n for n in notifications
                    if str(getattr(n, "event_id", "")) == str(ev_id)
                ]
                if rel_notifs:
                    lines.append("- **外发通知**:")
                    for n in rel_notifs:
                        ch = getattr(n, "channel", "unknown")
                        st = getattr(n, "status", "unknown")
                        n_payload = getattr(n, "payload", {}) or {}
                        rec = n_payload.get("recipient") or getattr(n, "recipient", "N/A")
                        lines.append(f"  - [{ch.upper()}] 状态: `{st}` | 目标: `{rec}`")
                lines.append("")

        content = "\n".join(lines)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[OK] Audit report exported to {output}")
    else:
        print(content)
    return 0


def handle_doctor(args: argparse.Namespace, config: AppConfig) -> int:
    db_path = getattr(args, "db_path", config.db_path)
    verbose = getattr(args, "verbose", False)

    repo = None
    try:
        repo = Repository(db_path)
    except Exception:
        pass

    doctor = SystemDoctor(repo=repo, db_path=db_path, config=config)
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


def handle_retention(args: argparse.Namespace, config: AppConfig) -> int:
    db_path = getattr(args, "db_path", config.db_path)
    dry_run = getattr(args, "dry_run", False)
    max_age_days = getattr(args, "max_age_days", None)

    repo = Repository(db_path)
    policy = RetentionPolicy(
        max_age_days=max_age_days if max_age_days is not None else config.retention_max_age_days,
        dry_run=dry_run,
        entity_ids=getattr(args, "retention_entity_ids", None),
        event_types=getattr(args, "retention_event_types", None),
        importances=getattr(args, "retention_importances", None),
        statuses=getattr(args, "retention_statuses", None),
        channels=getattr(args, "retention_channels", None),
    )
    manager = RetentionManager(repo=repo, policy=policy)
    summary = manager.enforce()

    action = "Would delete" if summary["dry_run"] else "Deleted"
    filters = summary.get("filters", {})
    filter_parts = []
    for key, value in filters.items():
        if value:
            filter_parts.append(f"{key}={value}")
    filter_str = f" [filters: {', '.join(filter_parts)}]" if filter_parts else ""
    print(f"{action} {summary['deleted_events']} event(s) and {summary['deleted_notifications']} notification(s).{filter_str}")
    return 0


def handle_test_rule(args: argparse.Namespace) -> int:
    try:
        ruleset = RuleParser.parse_file(args.rule_file)
    except Exception as e:
        print(f"[ERROR] Failed to parse rule file: {e}")
        return 1

    print(f"Loaded {len(ruleset.rules)} rule(s) from {args.rule_file}:\n")
    for rule in ruleset.rules:
        print(f"=== Rule [{rule.id}]: {rule.name} ===")
        html_content = ""
        if args.html_file:
            html_content = Path(args.html_file).read_text(encoding="utf-8")
        elif args.url:
            req = urllib.request.Request(args.url, headers=rule.target.headers or {"User-Agent": "WebWatcher/1.0"})
            with urllib.request.urlopen(req, timeout=rule.target.timeout) as resp:
                html_content = resp.read().decode("utf-8", errors="ignore")
        else:
            print(f"  Target URL: {rule.target.url} (no HTML sample provided, skipping live extraction)")
            continue

        result = RuleEvaluator.evaluate(rule, html_content)
        print("  Extracted Values:")
        for k, v in result.extracted_values.items():
            print(f"    - {k}: {v} ({type(v).__name__})")
        print()
    return 0


def handle_template(args: argparse.Namespace, config: AppConfig) -> int:
    if args.template_command == "list":
        print("Available presets:\n")
        for preset in list_presets():
            print(f"  {preset.name:20s} {preset.description}")
        return 0

    if args.template_command == "show":
        try:
            preset = get_preset(args.preset)
        except KeyError as e:
            print(f"[ERROR] {e}")
            return 1
        print(f"Preset: {preset.name}")
        print(f"Description: {preset.description}\n")

        example_url = "https://example.com"
        overrides: Dict[str, Any] = {}
        if args.preset == "github_release":
            example_url = "https://github.com/owner/repo"
            overrides["repo"] = "owner/repo"
        elif args.preset == "blog_post":
            overrides["selector"] = "h1"
        elif args.preset == "price":
            example_url = "https://example.com/product/123"
            overrides["selector"] = ".price"
        elif args.preset == "noise_reduction":
            overrides["selector"] = "body"

        example_rule = preset.generate(example_url, **overrides)
        print("Example rule (illustrative):")
        print(_rule_to_yaml(example_rule))
        return 0

    if args.template_command == "apply":
        try:
            preset = get_preset(args.preset)
        except KeyError as e:
            print(f"[ERROR] {e}")
            return 1

        overrides: Dict[str, Any] = {}
        if args.repo:
            overrides["repo"] = args.repo
        if args.selector:
            overrides["selector"] = args.selector
        if args.interval:
            overrides["interval"] = args.interval
        if args.channel:
            overrides["channel"] = args.channel
        if args.cooldown:
            overrides["cooldown"] = args.cooldown
        if args.rule_id:
            overrides["rule_id"] = args.rule_id
        if args.name:
            overrides["name"] = args.name

        try:
            rule = preset.generate(args.url, **overrides)
        except ValueError as e:
            print(f"[ERROR] {e}")
            return 1

        yaml_content = _rule_to_yaml(rule)
        if args.output:
            Path(args.output).write_text(yaml_content, encoding="utf-8")
            print(f"[OK] Generated rule written to {args.output}")
        else:
            print(yaml_content)
        return 0

    print("[ERROR] Unknown template command. Use: list, show, apply")
    return 1


def handle_rules(args: argparse.Namespace, config: AppConfig) -> int:
    rules_path = getattr(args, "rules_path", None) or os.getenv("WEB_WATCHER_RULES") or "config/rules.yaml"
    path = Path(rules_path)

    if not path.exists():
        print(f"[ERROR] Rules file not found: {path}")
        return 1

    try:
        ruleset = RuleParser.parse_file(path)
    except Exception as e:
        print(f"[ERROR] Failed to parse rules file: {e}")
        return 1

    rules = ruleset.rules
    command = getattr(args, "rules_command", None)

    if command == "list":
        if not rules:
            print("No rules found.")
            return 0
        print(f"{'ID':<20} {'Name':<30} {'Status':<10} {'Target URL'}")
        print("-" * 100)
        for rule in rules:
            print(f"{rule.id:<20} {rule.name:<30} {rule.status:<10} {rule.target.url}")
        return 0

    if command == "show":
        rule_id = args.rule_id
        rule = next((r for r in rules if r.id == rule_id), None)
        if not rule:
            print(f"[ERROR] Rule not found: {rule_id}")
            return 1
        print(f"ID:          {rule.id}")
        print(f"Name:        {rule.name}")
        print(f"Status:      {rule.status}")
        print(f"Target URL:  {rule.target.url}")
        print(f"Interval:    {rule.target.interval}")
        print(f"Timeout:     {rule.target.timeout}s")
        if rule.target.headers:
            print(f"Headers:     {rule.target.headers}")
        if rule.extractors:
            print("\nExtractors:")
            for ext in rule.extractors:
                print(f"  - {ext.name} ({ext.selector_type}): {ext.selector}")
                if ext.transforms:
                    print(f"    transforms: {ext.transforms}")
                if ext.scope_selector:
                    print(f"    scope_selector: {ext.scope_selector}")
        if rule.triggers:
            print("\nTriggers:")
            for trg in rule.triggers:
                print(f"  - type={trg.type}, field={trg.field}, importance={trg.importance}")
                if trg.condition:
                    print(f"    condition: {trg.condition}")
        if rule.routing.channels:
            print(f"\nRouting:     channels={rule.routing.channels}, cooldown={rule.routing.cooldown}")
        return 0

    if command in ("enable", "disable"):
        rule_id = args.rule_id
        new_status = "enabled" if command == "enable" else "disabled"
        rule = next((r for r in rules if r.id == rule_id), None)
        if not rule:
            print(f"[ERROR] Rule not found: {rule_id}")
            return 1
        if rule.status == new_status:
            print(f"Rule '{rule_id}' is already {new_status}.")
            return 0

        # Update YAML file
        try:
            content = path.read_text(encoding="utf-8")
            data = yaml.safe_load(content)
            for raw_rule in data.get("rules", []):
                if raw_rule.get("id") == rule_id:
                    raw_rule["status"] = new_status
                    break
            path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False), encoding="utf-8")
            print(f"[OK] Rule '{rule_id}' status set to '{new_status}'.")
            return 0
        except Exception as e:
            print(f"[ERROR] Failed to update rules file: {e}")
            return 1

    print("[ERROR] Unknown rules command. Use: list, show, enable, disable")
    return 1


def _yaml_value(value: str) -> str:
    needs_quote = any(c in value for c in [":", "#", "[", "]", "{", "}", ",", "&", "*", "!"]) or value.lower() in ("true", "false", "yes", "no", "on", "off", "null", "~") or value == "" or (value[0:1].isdigit() and value.strip() != value)
    if not needs_quote and "'" not in value and '"' not in value:
        return value
    if "'" not in value:
        return f"'{value}'"
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _rule_to_yaml(rule: WatcherRule) -> str:
    """Serialize a WatcherRule to YAML string compatible with RuleParser."""
    lines = [f'version: "1.0"', "rules:"]
    lines.append(f"  - id: {rule.id}")
    lines.append(f"    name: {_yaml_value(rule.name)}")
    lines.append("    target:")
    lines.append(f"      url: {_yaml_value(rule.target.url)}")
    lines.append(f"      interval: {_yaml_value(rule.target.interval)}")
    lines.append(f"      timeout: {rule.target.timeout}")
    if rule.target.headers:
        lines.append("      headers:")
        for k, v in rule.target.headers.items():
            lines.append(f"        {_yaml_value(k)}: {_yaml_value(v)}")

    if rule.extractors:
        lines.append("    extractors:")
        for ext in rule.extractors:
            lines.append(f"      - name: {ext.name}")
            lines.append(f"        selector_type: {ext.selector_type}")
            lines.append(f"        selector: {_yaml_value(ext.selector)}")
            if ext.transforms:
                lines.append(f"        transforms: {_yaml_list(ext.transforms)}")

    if rule.triggers:
        lines.append("    triggers:")
        for trg in rule.triggers:
            lines.append(f"      - type: {trg.type}")
            lines.append(f"        field: {trg.field}")
            if trg.condition:
                lines.append(f"        condition: {_yaml_value(trg.condition)}")
            lines.append(f"        importance: {trg.importance}")
            if trg.title_template:
                lines.append(f"        title_template: {_yaml_value(trg.title_template)}")
            if trg.body_template:
                lines.append(f"        body_template: {_yaml_value(trg.body_template)}")

    lines.append("    routing:")
    lines.append(f"      channels: {_yaml_list(rule.routing.channels)}")
    lines.append(f"      cooldown: {_yaml_value(rule.routing.cooldown)}")
    lines.append(f"    status: {_yaml_value(rule.status)}")
    return "\n".join(lines) + "\n"


def _yaml_list(items: List[str]) -> str:
    return "[" + ", ".join(items) + "]"


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    config = get_config()

    if args.command == "worker":
        return handle_worker(args, config)
    if args.command == "notify":
        return handle_notify(args, config)
    if args.command == "run":
        return handle_run(args, config)
    if args.command == "daemon":
        return handle_daemon(args, config)
    if args.command == "export":
        return handle_export(args, config)
    if args.command == "doctor":
        return handle_doctor(args, config)
    if args.command == "retention":
        return handle_retention(args, config)
    if args.command == "test-rule":
        return handle_test_rule(args)
    if args.command == "template":
        return handle_template(args, config)
    if args.command == "rules":
        return handle_rules(args, config)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
