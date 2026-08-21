"""CLI interface for Web Watcher (Phase 12-C Final)."""

import argparse
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock

import yaml

from .channel_senders import WebhookSender, EmailSender
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
from .generic_web_target import GenericWebTarget
from .models import Target, TargetStatus
from .fetcher import SmartFetcher, FetchResult
from .fetch_policy import FetchPolicy
from .fetch import FetchStatus

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="web-watcher",
        description="Web Watcher: autonomous web monitoring, investigation, and notification engine",
    )
    parser.add_argument("--version", action="version", version="web-watcher 1.0.6")
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
        "--smtp-host",
        type=str,
        default=None,
        help="SMTP server host for email channel delivery",
    )
    notify_parser.add_argument(
        "--smtp-port",
        type=int,
        default=25,
        help="SMTP server port (default: 25)",
    )
    notify_parser.add_argument(
        "--smtp-user",
        type=str,
        default=None,
        help="SMTP authentication username",
    )
    notify_parser.add_argument(
        "--smtp-password",
        type=str,
        default=None,
        help="[DEPRECATED] SMTP authentication password. Using CLI flags exposes the password to process listings and shell history. Prefer the WEB_WATCHER_SMTP_PASSWORD environment variable.",
    )
    notify_parser.add_argument(
        "--smtp-use-tls",
        action="store_true",
        help="Enable STARTTLS for SMTP",
    )
    notify_parser.add_argument(
        "--smtp-use-ssl",
        action="store_true",
        help="Enable SSL/TLS for SMTP (implicit TLS)",
    )
    notify_parser.add_argument(
        "--email-from",
        type=str,
        default=None,
        help="From address for email notifications (default: smtp-user or web-watcher@localhost)",
    )
    notify_parser.add_argument(
        "--email-to",
        type=str,
        nargs="+",
        default=None,
        help="Recipient address(es) for email notifications",
    )
    notify_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )
    notify_parser.add_argument(
        "--telegram-bot-token",
        dest="telegram_bot_token",
        default=None,
        help="Telegram Bot Token (for telegram channel)",
    )
    notify_parser.add_argument(
        "--telegram-chat-id",
        dest="telegram_chat_id",
        default=None,
        help="Telegram Chat ID (for telegram channel)",
    )

    # notify history
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
    notify_parser.add_argument(
        "--history-status",
        dest="notify_history_status",
        type=str,
        default=None,
        help="Filter history by status (pending/sent/failed/retry_pending)",
    )
    notify_parser.add_argument(
        "--history-channel",
        dest="notify_history_channel",
        type=str,
        default=None,
        help="Filter history by channel (console/webhook/slack/lark/dingtalk)",
    )

    # notify retry
    notify_parser.add_argument(
        "--retry",
        dest="notify_retry",
        action="store_true",
        help="Retry failed notifications",
    )
    notify_parser.add_argument(
        "--retry-limit",
        dest="notify_retry_limit",
        type=int,
        default=10,
        help="Maximum number of failed notifications to retry (default: 10)",
    )

    # notify stats
    notify_parser.add_argument(
        "--stats",
        dest="notify_stats",
        action="store_true",
        help="Show notification delivery statistics",
    )

    # 2.1 cross_target subcommand
    cross_target_parser = subparsers.add_parser(
        "cross-target",
        help="View and manage cross-target groups, events, and rules",
    )
    cross_target_subparsers = cross_target_parser.add_subparsers(dest="cross_target_command", help="Cross-target commands")
    ct_rules_parser = cross_target_subparsers.add_parser("rules", help="Show loaded cross_target rules")
    ct_rules_parser.add_argument("--db", "--db-path", dest="db_path", type=str, default="web_watcher.db", help="Path to SQLite database")
    ct_rules_parser.add_argument("--rules", dest="rules_path", type=str, default=None, help="Path to rules YAML")
    ct_events_parser = cross_target_subparsers.add_parser("events", help="Show recent cross_target events")
    ct_events_parser.add_argument("--db", "--db-path", dest="db_path", type=str, default="web_watcher.db", help="Path to SQLite database")
    ct_events_parser.add_argument("--limit", type=int, default=20, help="Max events to show (default: 20)")
    ct_events_parser.add_argument("--status", type=str, default=None, help="Filter by event status")
    ct_events_parser.add_argument("--rule", type=str, default=None, help="Filter by rule name")
    ct_events_parser.add_argument("--entity", type=str, default=None, help="Filter by entity id")

    # 3. run subcommand (pipeline execution)
    run_parser = subparsers.add_parser(
        "run",
        help="Execute monitoring pipeline cycle",
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

    # 8.1 inspect subcommand (Debug / Inspection Mode v1)
    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Run a single rule through the full pipeline and show detailed debug output",
    )
    inspect_parser.add_argument("--rule", required=True, help="Path to YAML rule file")
    inspect_parser.add_argument("--url", default=None, help="Remote URL to fetch and evaluate")
    inspect_parser.add_argument("--html-file", default=None, help="Local HTML file to evaluate against")
    inspect_parser.add_argument("--extractor", dest="inspect_extractor", default=None, help="Only inspect this extractor name")
    inspect_parser.add_argument("--verbose", action="store_true", help="Show full diff/evidence instead of truncating")

    # inspect watch (Watch Mode)
    inspect_parser.add_argument(
        "--watch",
        dest="inspect_watch",
        action="store_true",
        help="Continuously watch the URL and show changes in real-time",
    )
    inspect_parser.add_argument(
        "--watch-interval",
        dest="inspect_watch_interval",
        type=float,
        default=5.0,
        help="Polling interval in seconds for watch mode (default: 5.0)",
    )
    inspect_parser.add_argument(
        "--watch-max-iterations",
        dest="inspect_watch_max_iterations",
        type=int,
        default=None,
        help="Maximum number of watch iterations (default: unlimited)",
    )

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
    template_apply_parser.add_argument("--repo", default=None, help="GitHub owner/repo (for github_release preset)")
    template_apply_parser.add_argument("--selector", default=None, help="CSS selector override (optional for most presets)")
    template_apply_parser.add_argument("--interval", default=None, help="Monitoring interval (e.g. 15m, 1h)")
    template_apply_parser.add_argument("--channel", default=None, help="Notification channel (default: console)")
    template_apply_parser.add_argument("--cooldown", default=None, help="Cooldown duration (e.g. 300s)")
    template_apply_parser.add_argument("--rule-id", default=None, help="Rule ID override")
    template_apply_parser.add_argument("--name", default=None, help="Rule name override")
    template_apply_parser.add_argument("--output", "-o", default=None, help="Output file path (default: stdout)")

    # template save (user custom preset)
    template_save_parser = template_subparsers.add_parser(
        "save",
        help="Save current rule as a user custom preset",
    )
    template_save_parser.add_argument("name", help="Preset name")
    template_save_parser.add_argument("--description", default="", help="Preset description")
    template_save_parser.add_argument("--yaml-file", required=True, help="Path to YAML rule file to save as preset")
    template_save_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )

    # template export (user preset -> YAML file)
    template_export_parser = template_subparsers.add_parser(
        "export",
        help="Export a user preset to YAML file",
    )
    template_export_parser.add_argument("name", help="Preset name")
    template_export_parser.add_argument("--output", "-o", required=True, help="Output YAML file path")
    template_export_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )

    # template import (YAML file -> user preset)
    template_import_parser = template_subparsers.add_parser(
        "import",
        help="Import a YAML file as a user preset",
    )
    template_import_parser.add_argument("name", help="Preset name")
    template_import_parser.add_argument("--yaml-file", required=True, help="Path to YAML rule file")
    template_import_parser.add_argument("--description", default="", help="Preset description")
    template_import_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )

    # template delete (user preset)
    template_delete_parser = template_subparsers.add_parser(
        "delete",
        help="Delete a user preset",
    )
    template_delete_parser.add_argument("name", help="Preset name")
    template_delete_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )

    # 10. targets subcommand (Target Grouping / Tags)
    targets_parser = subparsers.add_parser(
        "targets",
        help="Inspect and manage targets",
    )
    targets_subparsers = targets_parser.add_subparsers(dest="targets_command", help="Targets commands")
    targets_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )

    # targets list
    targets_list_parser = targets_subparsers.add_parser(
        "list",
        help="List targets with optional tag filter",
    )
    targets_list_parser.add_argument(
        "--tag",
        dest="target_tags",
        action="append",
        default=None,
        help="Filter by tag (repeatable; OR semantics)",
    )

    # targets batch-enable
    targets_batch_enable_parser = targets_subparsers.add_parser(
        "batch-enable",
        help="Batch enable targets by tag or group",
    )
    targets_batch_enable_parser.add_argument(
        "--tag",
        dest="batch_tags",
        action="append",
        default=None,
        help="Enable targets with this tag (repeatable; OR semantics)",
    )
    targets_batch_enable_parser.add_argument(
        "--group",
        dest="batch_group",
        default=None,
        help="Enable targets in this group",
    )

    # targets batch-disable
    targets_batch_disable_parser = targets_subparsers.add_parser(
        "batch-disable",
        help="Batch disable targets by tag or group",
    )
    targets_batch_disable_parser.add_argument(
        "--tag",
        dest="batch_tags",
        action="append",
        default=None,
        help="Disable targets with this tag (repeatable; OR semantics)",
    )
    targets_batch_disable_parser.add_argument(
        "--group",
        dest="batch_group",
        default=None,
        help="Disable targets in this group",
    )

    # targets batch-delete
    targets_batch_delete_parser = targets_subparsers.add_parser(
        "batch-delete",
        help="Batch delete targets by tag or group",
    )
    targets_batch_delete_parser.add_argument(
        "--tag",
        dest="batch_tags",
        action="append",
        default=None,
        help="Delete targets with this tag (repeatable; OR semantics)",
    )
    targets_batch_delete_parser.add_argument(
        "--group",
        dest="batch_group",
        default=None,
        help="Delete targets in this group",
    )

    # targets batch-retag
    targets_batch_retag_parser = targets_subparsers.add_parser(
        "batch-retag",
        help="Batch add/remove tags from targets by tag or group",
    )
    targets_batch_retag_parser.add_argument(
        "--tag",
        dest="batch_tags",
        action="append",
        default=None,
        help="Targets with this tag (repeatable; OR semantics)",
    )
    targets_batch_retag_parser.add_argument(
        "--group",
        dest="batch_group",
        default=None,
        help="Targets in this group",
    )
    targets_batch_retag_parser.add_argument(
        "--add-tag",
        dest="add_tags",
        action="append",
        default=None,
        help="Add this tag (repeatable)",
    )
    targets_batch_retag_parser.add_argument(
        "--remove-tag",
        dest="remove_tags",
        action="append",
        default=None,
        help="Remove this tag (repeatable)",
    )

    # 11. reload subcommand (Hot Reload)
    reload_parser = subparsers.add_parser(
        "reload",
        help="Hot reload rules from YAML file",
    )
    reload_parser.add_argument(
        "--rules",
        dest="reload_rules_path",
        type=str,
        default=None,
        help="Path to YAML rules file (default: WEB_WATCHER_RULES or config/rules.yaml)",
    )
    reload_parser.add_argument(
        "--include-tag",
        dest="reload_include_tags",
        action="append",
        default=None,
        help="Only reload rules with this tag (repeatable)",
    )
    reload_parser.add_argument(
        "--exclude-tag",
        dest="reload_exclude_tags",
        action="append",
        default=None,
        help="Exclude rules with this tag from reload (repeatable)",
    )
    reload_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )

    # 13. digest subcommand (Digest v1)
    digest_parser = subparsers.add_parser(
        "digest",
        help="Generate a non-realtime digest/report of recent events",
    )
    digest_parser.add_argument(
        "preset",
        nargs="?",
        choices=["daily", "weekly"],
        default=None,
        help="Preset time window: daily (last 24h) or weekly (last 7d)",
    )
    digest_parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Start of time window (ISO timestamp, e.g. 2026-08-20T00:00:00Z)",
    )
    digest_parser.add_argument(
        "--until",
        type=str,
        default=None,
        help="End of time window (ISO timestamp, default: now)",
    )
    digest_parser.add_argument(
        "--min-importance",
        dest="digest_min_importance",
        default="interesting",
        choices=["ignore", "interesting", "important", "critical"],
        help="Minimum importance level to include (default: interesting)",
    )
    digest_parser.add_argument(
        "--channel",
        dest="digest_channel",
        default="console",
        choices=["console", "webhook", "email", "slack", "lark", "dingtalk"],
        help="Delivery channel for the digest (default: console)",
    )
    digest_parser.add_argument(
        "--webhook-url",
        dest="digest_webhook_url",
        default=None,
        help="Webhook URL (for webhook channel)",
    )
    digest_parser.add_argument(
        "--smtp-host",
        dest="digest_smtp_host",
        default=None,
        help="SMTP host (for email channel)",
    )
    digest_parser.add_argument(
        "--smtp-port",
        dest="digest_smtp_port",
        type=int,
        default=25,
        help="SMTP port (default: 25)",
    )
    digest_parser.add_argument(
        "--smtp-user",
        dest="digest_smtp_user",
        default=None,
        help="SMTP username (for email channel)",
    )
    digest_parser.add_argument(
        "--smtp-password",
        dest="digest_smtp_password",
        default=None,
        help="[DEPRECATED] SMTP password (for email channel). Prefer WEB_WATCHER_SMTP_PASSWORD env var.",
    )
    digest_parser.add_argument(
        "--smtp-use-tls",
        dest="digest_smtp_use_tls",
        action="store_true",
        help="Enable STARTTLS for SMTP",
    )
    digest_parser.add_argument(
        "--smtp-use-ssl",
        dest="digest_smtp_use_ssl",
        action="store_true",
        help="Enable SSL/TLS for SMTP",
    )
    digest_parser.add_argument(
        "--email-from",
        dest="digest_email_from",
        default=None,
        help="From address for email channel",
    )
    digest_parser.add_argument(
        "--email-to",
        dest="digest_email_to",
        nargs="+",
        default=None,
        help="Recipient address(es) for email channel",
    )
    digest_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )
    digest_parser.add_argument(
        "--telegram-bot-token",
        dest="telegram_bot_token",
        default=None,
        help="Telegram Bot Token (for telegram channel)",
    )
    digest_parser.add_argument(
        "--telegram-chat-id",
        dest="telegram_chat_id",
        default=None,
        help="Telegram Chat ID (for telegram channel)",
    )

    # 12. webui subcommand (Web UI v1)
    webui_parser = subparsers.add_parser(
        "webui",
        help="Start lightweight local monitoring dashboard",
    )
    webui_parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)",
    )
    webui_parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port number (default: 8080)",
    )
    webui_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )

    # 13. registry subcommand (Rule Registry v1)
    registry_parser = subparsers.add_parser(
        "registry",
        help="Inspect and manage rule runtime registry",
    )
    registry_subparsers = registry_parser.add_subparsers(dest="registry_command", help="Registry commands")
    registry_parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=str,
        default="web_watcher.db",
        help="Path to SQLite database file (default: web_watcher.db)",
    )

    # registry list
    registry_list_parser = registry_subparsers.add_parser(
        "list",
        help="List registered rules",
    )
    registry_list_parser.add_argument(
        "--group",
        dest="registry_group",
        default=None,
        help="Filter by group name",
    )
    registry_list_parser.add_argument(
        "--enabled",
        dest="registry_enabled",
        action="store_true",
        default=None,
        help="Only show enabled rules",
    )
    registry_list_parser.add_argument(
        "--disabled",
        dest="registry_disabled",
        action="store_true",
        default=None,
        help="Only show disabled rules",
    )

    # registry show
    registry_show_parser = registry_subparsers.add_parser(
        "show",
        help="Show details of a registered rule",
    )
    registry_show_parser.add_argument("rule_id", help="Rule ID to show")

    # registry enable
    registry_enable_parser = registry_subparsers.add_parser(
        "enable",
        help="Enable a registered rule",
    )
    registry_enable_parser.add_argument("rule_id", help="Rule ID to enable")

    # registry disable
    registry_disable_parser = registry_subparsers.add_parser(
        "disable",
        help="Disable a registered rule",
    )
    registry_disable_parser.add_argument("rule_id", help="Rule ID to disable")

    # registry priority
    registry_priority_parser = registry_subparsers.add_parser(
        "priority",
        help="Set execution priority for a registered rule",
    )
    registry_priority_parser.add_argument("rule_id", help="Rule ID")
    registry_priority_parser.add_argument("priority", type=int, help="Priority value (higher runs first)")

    # registry group
    registry_group_parser = registry_subparsers.add_parser(
        "group",
        help="Assign a registered rule to a group",
    )
    registry_group_parser.add_argument("rule_id", help="Rule ID")
    registry_group_parser.add_argument("group_name", help="Group name")

    # registry remove
    registry_remove_parser = registry_subparsers.add_parser(
        "remove",
        help="Remove a rule from the registry",
    )
    registry_remove_parser.add_argument("rule_id", help="Rule ID to remove")

    # 11. rules subcommand (Observability v1)
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

    return parser


# Handlers are imported from cli_handlers module
from .cli_handlers import (
    handle_worker,
    handle_notify,
    handle_digest,
    handle_run,
    handle_daemon,
    handle_export,
    handle_doctor,
    handle_retention,
    handle_test_rule,
    handle_inspect,
    handle_template,
    handle_reload,
    handle_rules,
    handle_registry,
    handle_targets,
    handle_cross_target,
    handle_webui,
    _truncate,
    _yaml_value,
    _rule_to_yaml,
    _yaml_list,
)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    config = get_config()

    if args.command == "worker":
        return handle_worker(args, config)
    if args.command == "notify":
        return handle_notify(args, config)
    if args.command == "cross-target":
        return handle_cross_target(args, config)
    if args.command == "digest":
        return handle_digest(args, config)
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
    if args.command == "inspect":
        return handle_inspect(args)
    if args.command == "template":
        return handle_template(args, config)
    if args.command == "rules":
        return handle_rules(args, config)
    if args.command == "targets":
        return handle_targets(args, config)
    if args.command == "reload":
        return handle_reload(args, config)
    if args.command == "registry":
        return handle_registry(args, config)
    if args.command == "webui":
        return handle_webui(args, config)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
