import sqlite3
import time
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from pathlib import Path
from web_watcher.repository import Repository


@dataclass
class DiagnosticResult:
    name: str
    status: str  # "OK", "WARN", "FAIL"
    message: str
    details: Optional[Dict[str, Any]] = None


class SystemDoctor:
    """系统健康诊断与自检引擎"""

    def __init__(self, repo: Optional[Repository] = None, db_path: Optional[str] = None):
        self.repo = repo
        self.db_path = db_path or (getattr(repo, "db_path", None) if repo else None) or "web_watcher.db"

    def check_database(self) -> DiagnosticResult:
        path = Path(self.db_path)
        if not path.exists():
            return DiagnosticResult(
                name="Database File",
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
                status="OK",
                message=f"SQLite healthy (journal_mode: {journal_mode}, latency: {elapsed_ms:.2f}ms)",
                details={"journal_mode": journal_mode, "latency_ms": elapsed_ms},
            )
        except Exception as e:
            return DiagnosticResult(
                name="Database Connection",
                status="FAIL",
                message=f"Connection error: {str(e)}",
            )

    def check_notification_queue(self, max_lag_warn: int = 50) -> DiagnosticResult:
        if not self.repo:
            return DiagnosticResult(
                name="Notification Queue",
                status="WARN",
                message="No repository configured to inspect pending queue",
            )

        try:
            pending = []
            if hasattr(self.repo, "list_pending_notifications"):
                pending = self.repo.list_pending_notifications()

            count = len(pending)
            if count > max_lag_warn:
                return DiagnosticResult(
                    name="Notification Queue",
                    status="WARN",
                    message=f"High pending queue backlog: {count} notifications waiting",
                    details={"pending_count": count},
                )

            return DiagnosticResult(
                name="Notification Queue",
                status="OK",
                message=f"Queue healthy ({count} pending notifications)",
                details={"pending_count": count},
            )
        except Exception as e:
            return DiagnosticResult(
                name="Notification Queue",
                status="FAIL",
                message=f"Queue check failed: {str(e)}",
            )

    def run_all(self) -> List[DiagnosticResult]:
        return [
            self.check_database(),
            self.check_notification_queue(),
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

        has_fail = any(r.status == "FAIL" for r in results)
        has_warn = any(r.status == "WARN" for r in results)
        lines.append("")
        lines.append("-" * 36)
        if has_fail:
            lines.append("Verdict: UNHEALTHY (Please inspect FAIL items)")
        elif has_warn:
            lines.append("Verdict: HEALTHY WITH WARNINGS")
        else:
            lines.append("Verdict: ALL CHECKS PASSED (System Healthy)")

        return "\n".join(lines)
