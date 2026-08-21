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
        help="SMTP authentication password",
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

    # 12. registry subcommand (Rule Registry v1)
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


def _build_dispatcher(
    repo: Repository,
    webhook_url: Optional[str],
    config: AppConfig,
    email_sender: Optional[EmailSender] = None,
) -> NotificationDispatcher:
    dispatcher = NotificationDispatcher(
        repository=repo,
        max_retries=config.default_max_retries,
        base_backoff_sec=config.default_base_backoff_sec,
        poll_interval=config.default_poll_interval,
        batch_size=config.default_batch_size,
    )
    if webhook_url:
        dispatcher.register_sender("webhook", WebhookSender(webhook_url=webhook_url))
    if email_sender is not None:
        dispatcher.register_sender("email", email_sender)
    return dispatcher


def handle_notify(args: argparse.Namespace, config: AppConfig) -> int:
    db_path = getattr(args, "db_path", config.db_path)
    batch_size = getattr(args, "batch_size", config.default_batch_size)
    interval = getattr(args, "interval", config.default_poll_interval)
    webhook_url = getattr(args, "webhook_url", None)
    run_once = getattr(args, "once", False)
    show_history = getattr(args, "notify_history", False)
    history_limit = getattr(args, "notify_history_limit", 20)
    history_status = getattr(args, "notify_history_status", None)
    history_channel = getattr(args, "notify_history_channel", None)
    do_retry = getattr(args, "notify_retry", False)
    retry_limit = getattr(args, "notify_retry_limit", 10)
    show_stats = getattr(args, "notify_stats", False)

    email_sender = None
    if getattr(args, "smtp_host", None):
        email_sender = EmailSender(
            smtp_host=args.smtp_host,
            smtp_port=getattr(args, "smtp_port", 25),
            smtp_user=getattr(args, "smtp_user", None),
            smtp_password=getattr(args, "smtp_password", None),
            use_tls=getattr(args, "smtp_use_tls", False),
            use_ssl=getattr(args, "smtp_use_ssl", False),
            from_addr=getattr(args, "email_from", None),
            to_addrs=getattr(args, "email_to", None) or [],
        )

    repo = Repository(db_path)

    # Handle history with filters
    if show_history:
        notifications = repo.list_notifications(
            status=history_status,
            channel=history_channel,
            limit=history_limit,
        )
        if not notifications:
            print("No notification history found.")
            return 0

        print(f"{'ID':<6} {'Event':<8} {'Channel':<12} {'Status':<14} {'Created At':<26} {'Sent At'}")
        print("-" * 90)
        for n in notifications:
            sent_str = n.sent_at.isoformat() if n.sent_at else "-"
            print(f"{n.id:<6} {n.event_id:<8} {n.channel:<12} {n.status:<14} {n.created_at.isoformat():<26} {sent_str}")
        return 0

    # Handle retry
    if do_retry:
        pending = repo.list_notifications(status="failed", limit=retry_limit)
        if not pending:
            print("No failed notifications to retry.")
            return 0

        dispatcher = _build_dispatcher(repo, webhook_url, config, email_sender=email_sender)
        retry_count = 0
        for n in pending:
            try:
                # Re-queue by resetting status to pending
                repo.connection.execute(
                    "UPDATE notifications SET status = 'pending', dispatch_until = NULL, dispatch_token = NULL WHERE id = ?",
                    (n.id,),
                )
                repo.connection.commit()
                retry_count += 1
            except Exception as e:
                print(f"Failed to retry notification {n.id}: {e}")

        print(f"Retried {retry_count} notification(s). Run 'notify --once' to dispatch them.")
        return 0

    # Handle stats
    if show_stats:
        cursor = repo.connection.execute("""
            SELECT status, channel, COUNT(*) as cnt, 
                   MIN(created_at) as first_seen, 
                   MAX(created_at) as last_seen,
                   SUM(CASE WHEN sent_at IS NOT NULL THEN 1 ELSE 0 END) as sent_count
            FROM notifications 
            GROUP BY status, channel 
            ORDER BY status, channel
        """)
        rows = cursor.fetchall()

        if not rows:
            print("No notification statistics available.")
            return 0

        print(f"{'Status':<14} {'Channel':<12} {'Count':<8} {'Sent':<8} {'First Seen':<26} {'Last Seen'}")
        print("-" * 90)
        for row in rows:
            status, channel, cnt, first_seen, last_seen, sent_count = row
            print(f"{status:<14} {channel:<12} {cnt:<8} {sent_count:<8} {first_seen:<26} {last_seen}")
        return 0

    # Default: run dispatcher
    dispatcher = _build_dispatcher(repo, webhook_url, config, email_sender=email_sender)

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
    include_tags = getattr(args, "include_tags", None) or []
    exclude_tags = getattr(args, "exclude_tags", None) or []

    repo = Repository(db_path)
    
    # Use ScheduledRunner for actual execution with tag filtering
    from .scheduled_runner import ScheduledRunner
    runner = ScheduledRunner(
        repo=repo,
        config=config,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
    )
    
    if run_once:
        summary = runner.run_once()
        rules_filtered = summary.get("rules_filtered", 0)
        if rules_filtered:
            print(f"Filtered {rules_filtered} rules by tags")
        reload_info = summary.get("reload")
        if reload_info and not reload_info.get("error"):
            print(f"Reloaded {reload_info.get('reloaded', 0)} rules ({reload_info.get('elapsed_seconds', 0)}s)")
        elif reload_info and reload_info.get("error"):
            print(f"Reload error: {reload_info['error']}")
        print(f"Pipeline run completed: {summary.get('targets_evaluated', 0)} targets evaluated")
        return 0
    
    print(f"Starting continuous pipeline run (interval={interval}s, auto_investigate={auto_inv})... Press Ctrl+C to stop.")
    try:
        while True:
            runner.run_once()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nPipeline run stopped by user.")
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
    include_tags = getattr(args, "include_tags", None) or []
    exclude_tags = getattr(args, "exclude_tags", None) or []

    repo = Repository(db_path)
    
    # Use ScheduledRunner for actual execution with tag filtering
    from .scheduled_runner import ScheduledRunner
    runner = ScheduledRunner(
        repo=repo,
        config=config,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
    )
    
    print(f"Starting web-watcher daemon (interval={interval}s, auto_investigate={auto_inv}, db={db_path})...")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            summary = runner.run_once()
            reload_info = summary.get("reload")
            if reload_info and not reload_info.get("error"):
                print(f"[reload] {reload_info.get('reloaded', 0)} rules reloaded ({reload_info.get('elapsed_seconds', 0)}s)")
            elif reload_info and reload_info.get("error"):
                print(f"[reload] error: {reload_info['error']}")
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


def _truncate(text: str, limit: int = 200) -> str:
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def handle_inspect(args: argparse.Namespace) -> int:
    try:
        ruleset = RuleParser.parse_file(args.rule)
    except Exception as e:
        print(f"[ERROR] Failed to parse rule file: {e}")
        return 1

    if not ruleset.rules:
        print("[ERROR] No rules found in rule file.")
        return 1

    rule = ruleset.rules[0]
    print(f"=== Inspect Rule [{rule.id}]: {rule.name} ===\n")

    html_content = ""
    if args.html_file:
        html_content = Path(args.html_file).read_text(encoding="utf-8")
        print(f"HTML Source: local file ({len(html_content)} bytes)")
    elif args.url:
        print(f"Fetching URL: {rule.target.url}")
        req = urllib.request.Request(args.url, headers=rule.target.headers or {"User-Agent": "WebWatcher/1.0"})
        with urllib.request.urlopen(req, timeout=rule.target.timeout) as resp:
            html_content = resp.read().decode("utf-8", errors="ignore")
        print(f"HTTP Status: {resp.status}")
        print(f"HTML Source: remote ({len(html_content)} bytes)")
    else:
        print("[ERROR] Provide either --url or --html-file.")
        return 1

    target = Target(
        id=rule.id,
        url=rule.target.url,
        status=TargetStatus.NORMAL,
        interval=rule.target.interval,
    )
    adapter = GenericWebTarget(
        target=target,
        extractors=rule.extractors,
        rule_status=rule.status,
    )

    now = datetime.utcnow()
    fetch_result = FetchResult(
        target_key=rule.id,
        status=FetchStatus.SUCCESS,
        status_code=200,
        fetched_at=now,
        content=html_content,
        etag=None,
        last_modified=None,
    )

    print(f"\n--- Fetch ---")
    print(f"status={fetch_result.status.value}")
    print(f"status_code={fetch_result.status_code}")
    print(f"etag={fetch_result.etag}")
    print(f"last_modified={fetch_result.last_modified}")
    print(f"error={fetch_result.error}")
    print()

    mock_fetcher = MagicMock(spec=SmartFetcher)
    mock_fetcher.fetch.return_value = fetch_result

    result = adapter.execute(fetcher=mock_fetcher, now=now)

    print(f"--- Observation ---")
    print(f"status={result.observation.status}")
    print(f"reason={result.reason}")
    print(f"outcome={result.outcome}")
    print(f"signals_emitted={len(result.signals_emitted)}")
    print()

    extractor_filter = getattr(args, "inspect_extractor", None)
    verbose = getattr(args, "verbose", False)
    limit = None if verbose else 200

    print("--- Extractors ---")
    for name, extracted in result.observation.extracted_results.items():
        if extractor_filter and name != extractor_filter:
            continue
        cfg = next((ext for ext in rule.extractors if ext.name == name), None)
        print(f"[{name}]")
        print(f"  selector: {getattr(cfg, 'selector', None)}")
        print(f"  scope_selector: {getattr(cfg, 'scope_selector', None)}")
        print(f"  status: {extracted.status.value}")
        print(f"  raw_value: {_truncate(extracted.raw_value, limit)}")
        print(f"  normalized_value: {_truncate(result.observation.normalized_values.get(name), limit)}")
        print(f"  previous_value: {_truncate(result.observation.previous_values.get(name), limit)}")

        diff = result.observation.diffs.get(name)
        if diff:
            print(f"  changed: {diff.changed}")
            print(f"  diff_summary: {diff.summary}")
            if verbose:
                print(f"  before: {diff.before}")
                print(f"  after: {diff.after}")
            else:
                print(f"  before: {_truncate(diff.before, limit)}")
                print(f"  after: {_truncate(diff.after, limit)}")

        evidence = result.observation.evidence.get("extractor_results", {}).get(name, {})
        if evidence:
            print(f"  scope_miss: {evidence.get('scope_miss')}")
            print(f"  scope_matched_count: {evidence.get('scope_matched_count')}")
            print(f"  scope_merged_count: {evidence.get('scope_merged_count')}")
        print()

    # Watch mode: continuous monitoring
    watch_mode = getattr(args, "inspect_watch", False)
    if watch_mode:
        watch_interval = getattr(args, "inspect_watch_interval", 5.0)
        max_iterations = getattr(args, "inspect_watch_max_iterations", None)
        iteration = 0

        print(f"=== Watch Mode: {rule.id} ({rule.name}) ===")
        print(f"Interval: {watch_interval}s")
        if max_iterations:
            print(f"Max iterations: {max_iterations}")
        print("Press Ctrl+C to stop.\n")

        import time as time_mod
        previous_values = dict(result.observation.normalized_values) if result.observation.normalized_values else {}

        try:
            while True:
                iteration += 1
                if max_iterations and iteration > max_iterations:
                    print(f"\nReached max iterations ({max_iterations}). Stopping.")
                    break

                print(f"[{datetime.now().isoformat()}] Iteration {iteration}")

                # Fetch fresh content
                if args.url:
                    req = urllib.request.Request(args.url, headers=rule.target.headers or {"User-Agent": "WebWatcher/1.0"})
                    with urllib.request.urlopen(req, timeout=rule.target.timeout) as resp:
                        html_content = resp.read().decode("utf-8", errors="ignore")
                elif args.html_file:
                    html_content = Path(args.html_file).read_text(encoding="utf-8")
                else:
                    print("[ERROR] Provide either --url or --html-file.")
                    return 1

                fetch_result = FetchResult(
                    target_key=rule.id,
                    status=FetchStatus.SUCCESS,
                    status_code=200,
                    fetched_at=datetime.utcnow(),
                    content=html_content,
                    etag=None,
                    last_modified=None,
                )
                mock_fetcher.fetch.return_value = fetch_result

                result = adapter.execute(fetcher=mock_fetcher, now=datetime.utcnow())

                # Show changes
                changed = False
                for name, extracted in result.observation.extracted_results.items():
                    new_val = result.observation.normalized_values.get(name)
                    old_val = previous_values.get(name)

                    if old_val != new_val:
                        changed = True
                        print(f"  [{name}] CHANGED")
                        print(f"    before: {_truncate(old_val, 200)}")
                        print(f"    after:  {_truncate(new_val, 200)}")

                        diff = result.observation.diffs.get(name)
                        if diff:
                            print(f"    diff_summary: {diff.summary}")

                if not changed:
                    print("  No changes detected.")

                print()

                previous_values = dict(result.observation.normalized_values) if result.observation.normalized_values else {}

                time_mod.sleep(watch_interval)
        except KeyboardInterrupt:
            print("\nWatch mode stopped by user.")
        return 0

    return 0


def handle_template(args: argparse.Namespace, config: AppConfig) -> int:
    db_path = getattr(args, "db_path", config.db_path)
    repo = Repository(db_path)

    if args.template_command == "list":
        print("Built-in presets:\n")
        for preset in list_presets():
            print(f"  {preset.name:20s} {preset.description}")

        user_presets = repo.list_user_presets()
        if user_presets:
            print("\nUser presets:\n")
            for p in user_presets:
                print(f"  {p['name']:20s} {p['description']}")
        return 0

    if args.template_command == "show":
        # Try user preset first, then built-in
        user_preset = repo.get_user_preset(args.preset)
        if user_preset:
            print(f"User Preset: {user_preset['name']}")
            print(f"Description: {user_preset['description']}\n")
            print("YAML Content:")
            print(user_preset['yaml_content'])
            return 0

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
        elif args.preset == "product_page":
            example_url = "https://example.com/product/123"
        elif args.preset == "news_article":
            example_url = "https://example.com/news/article"
        elif args.preset == "status_page":
            example_url = "https://status.example.com"
        elif args.preset == "changelog":
            example_url = "https://github.com/owner/repo/blob/main/CHANGELOG.md"

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

    if args.template_command == "save":
        yaml_content = Path(args.yaml_file).read_text(encoding="utf-8")
        if repo.save_user_preset(args.name, yaml_content, args.description):
            print(f"[OK] Preset '{args.name}' saved.")
            return 0
        else:
            print(f"[ERROR] Failed to save preset '{args.name}'.")
            return 1

    if args.template_command == "export":
        preset = repo.get_user_preset(args.name)
        if not preset:
            print(f"[ERROR] User preset not found: {args.name}")
            return 1
        Path(args.output).write_text(preset["yaml_content"], encoding="utf-8")
        print(f"[OK] Preset '{args.name}' exported to {args.output}")
        return 0

    if args.template_command == "import":
        yaml_content = Path(args.yaml_file).read_text(encoding="utf-8")
        if repo.save_user_preset(args.name, yaml_content, args.description):
            print(f"[OK] Preset '{args.name}' imported.")
            return 0
        else:
            print(f"[ERROR] Failed to import preset '{args.name}'.")
            return 1

    if args.template_command == "delete":
        if repo.delete_user_preset(args.name):
            print(f"[OK] Preset '{args.name}' deleted.")
            return 0
        else:
            print(f"[ERROR] User preset not found: {args.name}")
            return 1

    print("[ERROR] Unknown template command. Use: list, show, apply, save, export, import, delete")
    return 1


def handle_reload(args: argparse.Namespace, config: AppConfig) -> int:
    db_path = getattr(args, "db_path", config.db_path)
    rules_path = getattr(args, "reload_rules_path", None) or os.getenv("WEB_WATCHER_RULES") or getattr(config, "rules_path", None) or "config/rules.yaml"
    include_tags = getattr(args, "reload_include_tags", None) or []
    exclude_tags = getattr(args, "reload_exclude_tags", None) or []

    repo = Repository(db_path)
    from .scheduled_runner import ScheduledRunner
    runner = ScheduledRunner(
        repo=repo,
        config=config,
        rules_path=rules_path,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
    )

    # Check if rules file changed first
    changed = runner._check_rules_changed()
    if not changed:
        print(f"Rules file unchanged: {rules_path}")
        return 0

    print(f"Reloading rules from: {rules_path}")
    stats = runner.reload_rules(
        include_tags=include_tags,
        exclude_tags=exclude_tags,
    )

    if stats.get("error"):
        print(f"[ERROR] Reload failed: {stats['error']}")
        return 1

    print(f"[OK] Reloaded {stats.get('reloaded', 0)} rules")
    if stats.get("filtered"):
        print(f"    Filtered: {stats['filtered']}")
    if stats.get("skipped"):
        print(f"    Skipped: {stats['skipped']}")
    if stats.get("synced_targets"):
        print(f"    Synced targets: {stats['synced_targets']}")
    print(f"    Elapsed: {stats.get('elapsed_seconds', 0)}s")
    return 0


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
        print(f"{'ID':<20} {'Name':<30} {'Status':<10} {'Tags':<20} {'Target URL'}")
        print("-" * 110)
        for rule in rules:
            tags_str = ", ".join(rule.tags or [])
            print(f"{rule.id:<20} {rule.name:<30} {rule.status:<10} {tags_str:<20} {rule.target.url}")
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
        tags = ", ".join(rule.tags or [])
        print(f"Tags:        {tags if tags else '-'}")
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


def handle_registry(args: argparse.Namespace, config: AppConfig) -> int:
    db_path = getattr(args, "db_path", config.db_path)
    repo = Repository(db_path)
    from .rule_registry import RuleRegistry
    registry = RuleRegistry(repo)

    command = getattr(args, "registry_command", None)

    if command == "list":
        group = getattr(args, "registry_group", None)
        enabled_only = getattr(args, "registry_enabled", False)
        disabled_only = getattr(args, "registry_disabled", False)

        enabled_filter = None
        if enabled_only and not disabled_only:
            enabled_filter = True
        elif disabled_only and not enabled_only:
            enabled_filter = False

        rules = registry.list_rules(group_name=group, enabled=enabled_filter)
        if not rules:
            print("No registered rules found.")
            return 0

        print(f"{'Rule ID':<20} {'Enabled':<8} {'Priority':<10} {'Group':<20}")
        print("-" * 70)
        for r in rules:
            enabled_str = "yes" if r["enabled"] else "no"
            group_str = r["group_name"] or "-"
            print(f"{r['rule_id']:<20} {enabled_str:<8} {r['priority']:<10} {group_str:<20}")
        return 0

    if command == "show":
        rule_id = args.rule_id
        r = registry.get(rule_id)
        if not r:
            print(f"[ERROR] Rule not found in registry: {rule_id}")
            return 1
        print(f"Rule ID:    {r['rule_id']}")
        print(f"Enabled:    {'yes' if r['enabled'] else 'no'}")
        print(f"Priority:   {r['priority']}")
        print(f"Group:      {r['group_name'] or '-'}")
        if r["metadata"]:
            print("Metadata:")
            for k, v in r["metadata"].items():
                print(f"  {k}: {v}")
        print(f"Created At: {r['created_at']}")
        print(f"Updated At: {r['updated_at']}")
        return 0

    if command == "enable":
        rule_id = args.rule_id
        r = registry.enable(rule_id)
        if not r:
            print(f"[ERROR] Failed to enable rule: {rule_id}")
            return 1
        print(f"[OK] Rule '{rule_id}' enabled.")
        return 0

    if command == "disable":
        rule_id = args.rule_id
        r = registry.disable(rule_id)
        if not r:
            print(f"[ERROR] Failed to disable rule: {rule_id}")
            return 1
        print(f"[OK] Rule '{rule_id}' disabled.")
        return 0

    if command == "priority":
        rule_id = args.rule_id
        priority = args.priority
        r = registry.set_priority(rule_id, priority)
        if not r:
            print(f"[ERROR] Failed to set priority for rule: {rule_id}")
            return 1
        print(f"[OK] Rule '{rule_id}' priority set to {priority}.")
        return 0

    if command == "group":
        rule_id = args.rule_id
        group_name = args.group_name
        r = registry.set_group(rule_id, group_name)
        if not r:
            print(f"[ERROR] Failed to set group for rule: {rule_id}")
            return 1
        print(f"[OK] Rule '{rule_id}' assigned to group '{group_name}'.")
        return 0

    if command == "remove":
        rule_id = args.rule_id
        removed = registry.remove(rule_id)
        if not removed:
            print(f"[ERROR] Rule not found in registry: {rule_id}")
            return 1
        print(f"[OK] Rule '{rule_id}' removed from registry.")
        return 0

    print("[ERROR] Unknown registry command. Use: list, show, enable, disable, priority, group, remove")
    return 1


def handle_targets(args: argparse.Namespace, config: AppConfig) -> int:
    db_path = getattr(args, "db_path", config.db_path)
    repo = Repository(db_path)
    command = getattr(args, "targets_command", None)
    target_tags = getattr(args, "target_tags", None)

    if command == "list":
        targets = repo.list_targets(tags=target_tags, require_all=False)
        if not targets:
            print("No targets found.")
            return 0
        print(f"{'ID':<20} {'URL':<50} {'Status':<12} {'Tags'}")
        print("-" * 110)
        for t in targets:
            tags_str = ", ".join(t.tags or [])
            print(f"{t.id:<20} {t.url:<50} {t.status.value:<12} {tags_str}")
        return 0

    if command in ("batch-enable", "batch-disable", "batch-delete", "batch-retag"):
        batch_tags = getattr(args, "batch_tags", None) or []
        batch_group = getattr(args, "batch_group", None)

        # Get targets matching criteria
        targets = repo.list_targets(tags=batch_tags if batch_tags else None, require_all=False)
        if batch_group:
            # Filter by group from metadata
            targets = [t for t in targets if (t.metadata or {}).get("group") == batch_group]

        if not targets:
            print("No targets match the specified criteria.")
            return 0

        print(f"Found {len(targets)} target(s) matching criteria.")

        if command == "batch-enable":
            for t in targets:
                t.status = TargetStatus.NORMAL
                repo.save_target(t)
            print(f"[OK] Enabled {len(targets)} target(s).")
            return 0

        if command == "batch-disable":
            for t in targets:
                t.status = TargetStatus.DISABLED
                repo.save_target(t)
            print(f"[OK] Disabled {len(targets)} target(s).")
            return 0

        if command == "batch-delete":
            for t in targets:
                repo.delete_target(t.id)
            print(f"[OK] Deleted {len(targets)} target(s).")
            return 0

        if command == "batch-retag":
            add_tags = getattr(args, "add_tags", None) or []
            remove_tags = getattr(args, "remove_tags", None) or []

            if not add_tags and not remove_tags:
                print("[ERROR] Must specify --add-tag or --remove-tag.")
                return 1

            updated = 0
            for t in targets:
                current_tags = set(t.tags or [])
                if add_tags:
                    current_tags.update(add_tags)
                if remove_tags:
                    current_tags.difference_update(remove_tags)
                t.tags = list(current_tags)
                repo.save_target(t)
                updated += 1

            print(f"[OK] Updated tags for {updated} target(s).")
            return 0

    print("[ERROR] Unknown targets command. Use: list, batch-enable, batch-disable, batch-delete, batch-retag")
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
    if rule.tags:
        lines.append(f"    tags: {_yaml_list(rule.tags)}")
    return "\n".join(lines) + "\n"


def _yaml_list(items: List[str]) -> str:
    return "[" + ", ".join(items) + "]"


def handle_cross_target(args: argparse.Namespace, config: AppConfig) -> int:
    import json
    from pathlib import Path
    from web_watcher.repository import Repository
    from web_watcher.scheduled_runner import ScheduledRunner

    db_path = getattr(args, "db_path", None) or "web_watcher.db"
    repo = Repository(db_path)
    runner = ScheduledRunner(repo=repo, config=config, rules_path=getattr(args, "rules_path", None))

    command = getattr(args, "cross_target_command", None)

    if command == "rules":
        path = Path(getattr(args, "rules_path", None) or os.getenv("WEB_WATCHER_RULES") or getattr(config, "rules_path", None) or "config/rules.yaml")
        rules = runner._load_cross_target_rules_from_yaml(path)
        print(f"Loaded {len(rules)} cross_target rule(s) from {path}")
        for r in rules:
            print(f"  - {r.name}: entities={r.entity_ids}, window={r.window_seconds}s, min_signals={r.min_signals}, importance={r.importance_boost}")
        return 0

    if command == "events":
        limit = getattr(args, "limit", 20)
        status_filter = getattr(args, "status", None)
        rule_filter = getattr(args, "rule", None)
        entity_filter = getattr(args, "entity", None)

        rows = repo.connection.execute(
            """
            SELECT e.id, e.entity_id, e.event_type, e.status, e.importance, e.created_at, e.updated_at, e.metadata_json
            FROM events e
            WHERE e.event_type = ?
            ORDER BY e.created_at DESC
            LIMIT ?
            """,
            ("cross_target", limit),
        ).fetchall()
        print(f"Recent cross_target events (up to {limit}):")
        for row in rows:
            meta = {}
            if row["metadata_json"]:
                try:
                    meta = json.loads(row["metadata_json"]) or {}
                except Exception:
                    pass
            rule_name = meta.get("rule_name", "")
            entity_ids = meta.get("entity_ids", [])
            if status_filter and row["status"] != status_filter:
                continue
            if rule_filter and rule_name != rule_filter:
                continue
            if entity_filter and entity_filter not in [str(x) for x in entity_ids]:
                continue
            print(f"  event_id={row['id']} status={row['status']} importance={row['importance']} rule={rule_name} entities={entity_ids} created_at={row['created_at']}")
        return 0

    print("Usage: python -m web_watcher.cli cross-target {rules|events}")
    return 0


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

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
