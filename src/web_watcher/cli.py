"""CLI interface for Web Watcher (Phase 12-C Final)."""

import argparse
import logging
import sys
import time
import urllib.request
from pathlib import Path
from typing import List, Optional

from .channel_senders import WebhookSender
from .event_correlator import EventCorrelator
from .exporter import AuditExporter
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
    )
    manager = RetentionManager(repo=repo, policy=policy)
    summary = manager.enforce()

    action = "Would delete" if summary["dry_run"] else "Deleted"
    print(f"{action} {summary['deleted_events']} event(s) and {summary['deleted_notifications']} notification(s).")
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

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
