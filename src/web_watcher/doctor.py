"""System health diagnosis and self-check engine."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from web_watcher.config import AppConfig
from web_watcher.repository import Repository

logger = logging.getLogger(__name__)


@dataclass
class DiagnosticResult:
    name: str
    status: str  # "PASS", "WARN", "FAIL"
    message: str
    details: Optional[Dict[str, Any]] = None


class SystemDoctor:
    """System health diagnosis and self-check engine (read-only)."""

    def __init__(
        self,
        repo: Optional[Repository] = None,
        db_path: Optional[str] = None,
        config: Optional[AppConfig] = None,
        metrics: Optional[Any] = None,
    ):
        self.repo = repo
        self.config = config
        self.metrics = metrics
        self.db_path = db_path or (
            getattr(repo, "db_path", None) if repo else None
        ) or (config.db_path if config else "web_watcher.db")

    # ------------------------------------------------------------------
    # Database checks
    # ------------------------------------------------------------------
    def check_database_connection(self) -> DiagnosticResult:
        path = Path(self.db_path)
        if not path.exists():
            return DiagnosticResult(
                name="Database Connection",
                status="WARN",
                message=f"Database file not found at {self.db_path} (will be created on first run)",
            )

        try:
            start = time.perf_counter()
            conn = sqlite3.connect(self.db_path, timeout=3.0)
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode;")
            journal_mode = cursor.fetchone()[0]
            cursor.execute("PRAGMA quick_check;")
            integrity = cursor.fetchone()[0]
            conn.close()
            elapsed_ms = (time.perf_counter() - start) * 1000

            if str(integrity).lower() != "ok":
                return DiagnosticResult(
                    name="Database Integrity",
                    status="FAIL",
                    message=f"Integrity check failed: {integrity}",
                )

            return DiagnosticResult(
                name="Database Connection",
                status="PASS",
                message=f"SQLite healthy (journal_mode: {journal_mode}, latency: {elapsed_ms:.2f}ms)",
                details={"journal_mode": journal_mode, "latency_ms": round(elapsed_ms, 3)},
            )
        except Exception as exc:
            return DiagnosticResult(
                name="Database Connection",
                status="FAIL",
                message=f"Connection error: {exc}",
            )

    def check_schema_version(self) -> DiagnosticResult:
        if not self._db_exists():
            return DiagnosticResult(
                name="Schema Version",
                status="WARN",
                message="Database not present; schema version unknown",
            )

        try:
            conn = sqlite3.connect(self.db_path, timeout=3.0)
            cursor = conn.cursor()
            cursor.execute("PRAGMA user_version;")
            version = cursor.fetchone()[0]
            conn.close()

            if version == 0:
                return DiagnosticResult(
                    name="Schema Version",
                    status="WARN",
                    message="Schema version is 0 (uninitialized or legacy database)",
                )

            return DiagnosticResult(
                name="Schema Version",
                status="PASS",
                message=f"Schema version: {version}",
                details={"version": version},
            )
        except Exception as exc:
            return DiagnosticResult(
                name="Schema Version",
                status="FAIL",
                message=f"Schema version check failed: {exc}",
            )

    def check_required_tables(self) -> DiagnosticResult:
        required = {
            "entities",
            "signals",
            "events",
            "event_signals",
            "notifications",
            "investigation_results",
            "investigation_evidence",
            "fetch_state",
        }

        if not self._db_exists():
            return DiagnosticResult(
                name="Required Tables",
                status="WARN",
                message="Database not present; cannot verify tables",
            )

        try:
            conn = sqlite3.connect(self.db_path, timeout=3.0)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
            existing = {row[0] for row in cursor.fetchall()}
            conn.close()

            missing = sorted(required - existing)
            if missing:
                return DiagnosticResult(
                    name="Required Tables",
                    status="FAIL",
                    message=f"Missing tables: {', '.join(missing)}",
                    details={"missing": missing, "existing": sorted(existing)},
                )

            return DiagnosticResult(
                name="Required Tables",
                status="PASS",
                message=f"All {len(required)} required tables present",
                details={"tables": sorted(required)},
            )
        except Exception as exc:
            return DiagnosticResult(
                name="Required Tables",
                status="FAIL",
                message=f"Table check failed: {exc}",
            )

    def check_required_columns(self) -> DiagnosticResult:
        required_columns = {
            "entities": {"id", "canonical_key", "name", "entity_type", "created_at"},
            "signals": {"id", "entity_id", "signal_type", "observed_at", "value", "fingerprint", "created_at"},
            "events": {"id", "entity_id", "event_type", "status", "importance", "created_at", "updated_at"},
            "event_signals": {"event_id", "signal_id"},
            "notifications": {"id", "event_id", "channel", "status", "created_at", "sent_at", "payload"},
            "investigation_results": {"id", "event_id", "task_type", "status", "summary", "metadata", "created_at"},
            "investigation_evidence": {"id", "investigation_id", "evidence_type", "payload", "created_at"},
            "fetch_state": {"target_key", "etag", "last_modified", "content_hash", "fetched_at"},
        }

        if not self._db_exists():
            return DiagnosticResult(
                name="Required Columns",
                status="WARN",
                message="Database not present; cannot verify columns",
            )

        try:
            conn = sqlite3.connect(self.db_path, timeout=3.0)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
            tables = [row[0] for row in cursor.fetchall()]
            conn.close()

            issues: List[str] = []
            for table in tables:
                if table not in required_columns:
                    continue
                conn = sqlite3.connect(self.db_path, timeout=3.0)
                try:
                    cursor = conn.cursor()
                    cursor.execute(f"PRAGMA table_info({table});")
                    columns = {row[1] for row in cursor.fetchall()}
                    missing = required_columns[table] - columns
                    if missing:
                        issues.append(f"{table}: missing {', '.join(sorted(missing))}")
                finally:
                    conn.close()

            if issues:
                return DiagnosticResult(
                    name="Required Columns",
                    status="FAIL",
                    message="; ".join(issues),
                )

            return DiagnosticResult(
                name="Required Columns",
                status="PASS",
                message="All required columns present",
            )
        except Exception as exc:
            return DiagnosticResult(
                name="Required Columns",
                status="FAIL",
                message=f"Column check failed: {exc}",
            )

    def check_indexes(self) -> DiagnosticResult:
        required_indexes = {
            "idx_signals_entity",
            "idx_signals_observed",
            "idx_events_entity",
            "idx_events_status",
            "idx_notifications_event",
            "idx_notifications_channel",
            "idx_fetch_state_hash",
        }

        if not self._db_exists():
            return DiagnosticResult(
                name="Indexes",
                status="WARN",
                message="Database not present; cannot verify indexes",
            )

        try:
            conn = sqlite3.connect(self.db_path, timeout=3.0)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%';")
            existing = {row[0] for row in cursor.fetchall()}
            conn.close()

            missing = sorted(required_indexes - existing)
            if missing:
                return DiagnosticResult(
                    name="Indexes",
                    status="WARN",
                    message=f"Missing indexes: {', '.join(missing)}",
                    details={"missing": missing},
                )

            return DiagnosticResult(
                name="Indexes",
                status="PASS",
                message=f"All {len(required_indexes)} required indexes present",
            )
        except Exception as exc:
            return DiagnosticResult(
                name="Indexes",
                status="FAIL",
                message=f"Index check failed: {exc}",
            )

    # ------------------------------------------------------------------
    # Vocabulary / enum consistency
    # ------------------------------------------------------------------
    def check_vocabulary(self) -> DiagnosticResult:
        allowed_signal_types = {
            "WEB_CONTENT_CHANGED",
            "WEB_STRUCTURE_CHANGED",
            "WEB_METADATA_CHANGED",
            "GITHUB_RELEASE",
            "GITHUB_STAR",
            "GITHUB_TAG",
        }
        allowed_event_types = {"content_change", "structure_change", "github_release", "github_star", "github_tag", "domain_event"}
        allowed_event_statuses = {"open", "investigating", "resolved", "ignored"}
        allowed_importance = {"low", "medium", "high", "critical"}

        if not self._db_exists():
            return DiagnosticResult(
                name="Vocabulary",
                status="WARN",
                message="Database not present; skipping vocabulary check",
            )

        try:
            conn = sqlite3.connect(self.db_path, timeout=3.0)
            cursor = conn.cursor()

            cursor.execute("SELECT DISTINCT signal_type FROM signals WHERE signal_type IS NOT NULL;")
            signal_types = {row[0] for row in cursor.fetchall()}
            bad_signals = signal_types - allowed_signal_types

            cursor.execute("SELECT DISTINCT event_type FROM events WHERE event_type IS NOT NULL;")
            event_types = {row[0] for row in cursor.fetchall()}
            bad_events = event_types - allowed_event_types

            cursor.execute("SELECT DISTINCT status FROM events WHERE status IS NOT NULL;")
            event_statuses = {row[0] for row in cursor.fetchall()}
            bad_statuses = event_statuses - allowed_event_statuses

            cursor.execute("SELECT DISTINCT importance FROM events WHERE importance IS NOT NULL;")
            importances = {row[0] for row in cursor.fetchall()}
            bad_importance = importances - allowed_importance

            conn.close()

            issues = []
            if bad_signals:
                issues.append(f"unknown signal_types: {', '.join(sorted(bad_signals))}")
            if bad_events:
                issues.append(f"unknown event_types: {', '.join(sorted(bad_events))}")
            if bad_statuses:
                issues.append(f"unknown event_statuses: {', '.join(sorted(bad_statuses))}")
            if bad_importance:
                issues.append(f"unknown importance: {', '.join(sorted(bad_importance))}")

            if issues:
                return DiagnosticResult(
                    name="Vocabulary",
                    status="WARN",
                    message="; ".join(issues),
                    details={
                        "bad_signal_types": sorted(bad_signals),
                        "bad_event_types": sorted(bad_events),
                        "bad_event_statuses": sorted(bad_statuses),
                        "bad_importance": sorted(bad_importance),
                    },
                )

            return DiagnosticResult(
                name="Vocabulary",
                status="PASS",
                message="All stored enum values are within allowed vocabulary",
            )
        except Exception as exc:
            return DiagnosticResult(
                name="Vocabulary",
                status="FAIL",
                message=f"Vocabulary check failed: {exc}",
            )

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def check_configuration(self) -> DiagnosticResult:
        if not self.config:
            return DiagnosticResult(
                name="Configuration",
                status="WARN",
                message="No AppConfig provided; cannot validate runtime configuration",
            )

        issues: List[str] = []
        if self.config.default_poll_interval <= 0:
            issues.append(f"default_poll_interval must be > 0, got {self.config.default_poll_interval}")
        if self.config.default_batch_size <= 0:
            issues.append(f"default_batch_size must be > 0, got {self.config.default_batch_size}")
        if self.config.default_max_retries < 0:
            issues.append(f"default_max_retries must be >= 0, got {self.config.default_max_retries}")
        if self.config.default_base_backoff_sec <= 0:
            issues.append(f"default_base_backoff_sec must be > 0, got {self.config.default_base_backoff_sec}")
        if self.config.retention_max_age_days <= 0:
            issues.append(f"retention_max_age_days must be > 0, got {self.config.retention_max_age_days}")
        valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.config.log_level.upper() not in valid_log_levels:
            issues.append(f"log_level must be one of {valid_log_levels}, got {self.config.log_level}")

        if issues:
            return DiagnosticResult(
                name="Configuration",
                status="FAIL",
                message="; ".join(issues),
            )

        return DiagnosticResult(
            name="Configuration",
            status="PASS",
            message="Configuration values are valid",
            details={
                "db_path": self.config.db_path,
                "poll_interval": self.config.default_poll_interval,
                "batch_size": self.config.default_batch_size,
                "max_retries": self.config.default_max_retries,
                "retention_max_age_days": self.config.retention_max_age_days,
                "log_level": self.config.log_level,
            },
        )

    # ------------------------------------------------------------------
    # Runtime state
    # ------------------------------------------------------------------
    def check_runtime_state(self) -> DiagnosticResult:
        if not self._db_exists():
            return DiagnosticResult(
                name="Runtime State",
                status="WARN",
                message="Database not available; cannot inspect runtime state",
            )

        try:
            conn = sqlite3.connect(self.db_path, timeout=3.0)
            cursor = conn.cursor()

            # Targets by status (from fetch_state + any target table if present)
            cursor.execute("SELECT COUNT(*) FROM fetch_state;")
            fetch_state_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM events WHERE status = 'open';")
            open_events = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM events WHERE status = 'investigating';")
            investigating_events = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM notifications WHERE status = 'pending';")
            pending_notifications = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM investigation_results WHERE status = 'running';")
            running_investigations = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM investigation_results WHERE status = 'pending';")
            pending_investigations = cursor.fetchone()[0]

            conn.close()

            details = {
                "fetch_state_targets": fetch_state_count,
                "open_events": open_events,
                "investigating_events": investigating_events,
                "pending_notifications": pending_notifications,
                "running_investigations": running_investigations,
                "pending_investigations": pending_investigations,
            }

            warnings = []
            if pending_notifications > 50:
                warnings.append(f"High pending notification backlog: {pending_notifications}")
            if running_investigations > 20:
                warnings.append(f"Many running investigations: {running_investigations}")
            if pending_investigations > 20:
                warnings.append(f"Many pending investigations: {pending_investigations}")

            if warnings:
                return DiagnosticResult(
                    name="Runtime State",
                    status="WARN",
                    message="; ".join(warnings),
                    details=details,
                )

            return DiagnosticResult(
                name="Runtime State",
                status="PASS",
                message="Runtime state counts within normal bounds",
                details=details,
            )
        except Exception as exc:
            return DiagnosticResult(
                name="Runtime State",
                status="FAIL",
                message=f"Runtime state check failed: {exc}",
            )

    # ------------------------------------------------------------------
    # Pipeline health
    # ------------------------------------------------------------------
    def check_pipeline_health(self) -> DiagnosticResult:
        if not self._db_exists():
            return DiagnosticResult(
                name="Pipeline Health",
                status="WARN",
                message="Database not available; cannot assess pipeline health",
            )

        try:
            conn = sqlite3.connect(self.db_path, timeout=3.0)
            cursor = conn.cursor()

            # Targets never fetched (fetch_state exists but fetched_at is NULL)
            cursor.execute("SELECT COUNT(*) FROM fetch_state WHERE fetched_at IS NULL;")
            never_fetched = cursor.fetchone()[0]

            # Repeated failures: count targets with consecutive failure signals
            # Since consecutive_failures lives in Target model, not directly in DB,
            # we approximate by counting recent error signals per entity.
            cursor.execute(
                """
                SELECT e.id, COUNT(s.id) as error_count
                FROM entities e
                JOIN signals s ON s.entity_id = e.id
                WHERE s.signal_type = 'WEB_CONTENT_CHANGED'
                  AND s.value LIKE '%%error%%'
                  AND s.created_at > ?
                GROUP BY e.id
                HAVING error_count >= 3
                """
            , (self._iso(datetime.now(timezone.utc) - timedelta(hours=1)),))
            repeated_failure_entities = cursor.fetchall()

            # Excessive 429 / 403: approximate from recent signals
            cursor.execute(
                """
                SELECT COUNT(*) FROM signals
                WHERE signal_type = 'WEB_CONTENT_CHANGED'
                  AND (value LIKE '%%429%%' OR value LIKE '%%403%%')
                  AND created_at > ?
                """
            , (self._iso(datetime.now(timezone.utc) - timedelta(hours=1)),))
            rate_limit_signals = cursor.fetchone()[0]

            # Stuck investigations: running for > 1h
            cursor.execute(
                """
                SELECT COUNT(*) FROM investigation_results
                WHERE status = 'running'
                  AND created_at < ?
                """
            , (self._iso(datetime.now(timezone.utc) - timedelta(hours=1)),))
            stuck_investigations = cursor.fetchone()[0]

            # Stuck notifications: pending for > 24h
            cursor.execute(
                """
                SELECT COUNT(*) FROM notifications
                WHERE status = 'pending'
                  AND created_at < ?
                """
            , (self._iso(datetime.now(timezone.utc) - timedelta(days=1)),))
            stuck_notifications = cursor.fetchone()[0]

            # Stale leases: targets with lease_until in the past (if lease columns exist)
            stale_leases = 0
            try:
                cursor.execute("SELECT COUNT(*) FROM fetch_state WHERE fetched_at IS NOT NULL;")
                # We don't have lease columns in fetch_state, so skip exact lease check
                # and rely on target status counts if a targets table exists.
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='targets';")
                if cursor.fetchone():
                    cursor.execute(
                        "SELECT COUNT(*) FROM targets WHERE lease_until IS NOT NULL AND lease_until < ?",
                        (self._iso(datetime.now(timezone.utc)),),
                    )
                    stale_leases = cursor.fetchone()[0]
            except Exception:
                stale_leases = 0

            conn.close()

            issues = []
            if never_fetched > 0:
                issues.append(f"{never_fetched} targets never fetched")
            if repeated_failure_entities:
                issues.append(f"{len(repeated_failure_entities)} entities with repeated failures in last 1h")
            if rate_limit_signals > 10:
                issues.append(f"{rate_limit_signals} rate-limit signals in last 1h")
            if stuck_investigations > 0:
                issues.append(f"{stuck_investigations} investigations stuck for > 1h")
            if stuck_notifications > 0:
                issues.append(f"{stuck_notifications} notifications pending for > 24h")
            if stale_leases > 0:
                issues.append(f"{stale_leases} stale leases")

            if issues:
                return DiagnosticResult(
                    name="Pipeline Health",
                    status="WARN",
                    message="; ".join(issues),
                    details={
                        "never_fetched": never_fetched,
                        "repeated_failure_entities": len(repeated_failure_entities),
                        "rate_limit_signals_1h": rate_limit_signals,
                        "stuck_investigations": stuck_investigations,
                        "stuck_notifications": stuck_notifications,
                        "stale_leases": stale_leases,
                    },
                )

            return DiagnosticResult(
                name="Pipeline Health",
                status="PASS",
                message="No pipeline health issues detected",
            )
        except Exception as exc:
            return DiagnosticResult(
                name="Pipeline Health",
                status="FAIL",
                message=f"Pipeline health check failed: {exc}",
            )

    # ------------------------------------------------------------------
    # Metrics snapshot
    # ------------------------------------------------------------------
    def check_metrics(self) -> DiagnosticResult:
        if not self.metrics:
            return DiagnosticResult(
                name="Metrics",
                status="WARN",
                message="No metrics provider configured",
            )

        try:
            snapshot = {}
            if hasattr(self.metrics, "to_dict"):
                snapshot = self.metrics.to_dict()
            elif hasattr(self.metrics, "_counters"):
                snapshot = {str(k): v for k, v in self.metrics._counters.items()}

            return DiagnosticResult(
                name="Metrics",
                status="PASS",
                message="Metrics snapshot available",
                details={"snapshot": snapshot},
            )
        except Exception as exc:
            return DiagnosticResult(
                name="Metrics",
                status="FAIL",
                message=f"Metrics check failed: {exc}",
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _db_exists(self) -> bool:
        return Path(self.db_path).is_file()

    @staticmethod
    def _iso(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run_all(self) -> List[DiagnosticResult]:
        return [
            self.check_database_connection(),
            self.check_schema_version(),
            self.check_required_tables(),
            self.check_required_columns(),
            self.check_indexes(),
            self.check_vocabulary(),
            self.check_configuration(),
            self.check_runtime_state(),
            self.check_pipeline_health(),
            self.check_metrics(),
        ]

    def render_report(self, results: Optional[List[DiagnosticResult]] = None) -> str:
        results = results or self.run_all()
        lines = [
            "=== Web Watcher System Doctor ===",
            "",
        ]
        for r in results:
            badge = f"[{r.status}]"
            lines.append(f"{badge:<8} {r.name}: {r.message}")
            if r.details:
                for key, value in r.details.items():
                    lines.append(f"         {key}: {value}")

        lines.append("")
        lines.append("-" * 36)
        has_fail = any(r.status == "FAIL" for r in results)
        has_warn = any(r.status == "WARN" for r in results)
        if has_fail:
            lines.append("Verdict: UNHEALTHY (Please inspect FAIL items)")
        elif has_warn:
            lines.append("Verdict: HEALTHY WITH WARNINGS")
        else:
            lines.append("Verdict: ALL CHECKS PASSED (System Healthy)")

        return "\n".join(lines)
