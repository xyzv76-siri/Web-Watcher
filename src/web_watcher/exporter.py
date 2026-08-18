import re
import html
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from web_watcher.repository import Repository


def parse_since(since_str: Optional[str]) -> Optional[datetime]:
    if not since_str:
        return None
    since_str = since_str.strip().lower()
    now = datetime.utcnow()
    match = re.match(r"^(\d+)([smhd])$", since_str)
    if match:
        val, unit = int(match.group(1)), match.group(2)
        if unit == "s":
            return now - timedelta(seconds=val)
        elif unit == "m":
            return now - timedelta(minutes=val)
        elif unit == "h":
            return now - timedelta(hours=val)
        elif unit == "d":
            return now - timedelta(days=val)
    try:
        return datetime.fromisoformat(since_str)
    except Exception:
        return now - timedelta(hours=24)


class AuditExporter:
    def __init__(self, repo: Repository):
        self.repo = repo

    def collect_data(self, since: Optional[datetime] = None) -> Dict[str, Any]:
        events = []
        if hasattr(self.repo, "list_events"):
            try:
                events = self.repo.list_events(since=since)
            except TypeError:
                events = self.repo.list_events()

        if since and events:
            events = [
                e for e in events
                if getattr(e, "created_at", None) is None or e.created_at >= since
            ]

        notifications = []
        if hasattr(self.repo, "list_all_notifications"):
            notifications = self.repo.list_all_notifications()
        elif hasattr(self.repo, "list_notifications"):
            notifications = self.repo.list_notifications()

        return {
            "events": events,
            "notifications": notifications,
            "generated_at": datetime.utcnow(),
            "since": since,
        }

    def export_markdown(self, since_str: Optional[str] = "24h") -> str:
        since_dt = parse_since(since_str) if isinstance(since_str, str) else since_str
        data = self.collect_data(since=since_dt)
        events = data["events"]
        notifications = data["notifications"]

        lines = [
            "# Web Watcher 审计报告",
            "",
            f"- **生成时间 (UTC)**: {data['generated_at'].strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **时间范围 (Since)**: {since_str or 'All'}",
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

        return "\n".join(lines)

    def export_html(self, since_str: Optional[str] = "24h") -> str:
        since_dt = parse_since(since_str) if isinstance(since_str, str) else since_str
        data = self.collect_data(since=since_dt)
        events = data["events"]
        notifications = data["notifications"]

        rows = []
        for ev in events:
            ev_id = html.escape(str(getattr(ev, "id", getattr(ev, "event_id", "N/A"))))
            payload = getattr(ev, "payload", {}) or {}
            title = html.escape(str(payload.get("title") or getattr(ev, "title", getattr(ev, "event_type", "Event"))))
            status = html.escape(str(getattr(ev, "status", "unknown")))
            importance = html.escape(str(getattr(ev, "importance", "info")))
            rows.append(f"<tr><td><code>{ev_id}</code></td><td>{title}</td><td><span class=\"badge\">{importance}</span></td><td>{status}</td></tr>")

        tbody = "".join(rows) if rows else "<tr><td colspan=\"4\">暂无事件记录</td></tr>"

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>Web Watcher Audit Report</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 40px; color: #333; }}
        h1 {{ border-bottom: 2px solid #eaecef; padding-bottom: 10px; }}
        .meta {{ background: #f6f8fa; padding: 12px; border-radius: 6px; margin-bottom: 20px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
        th, td {{ border: 1px solid #dfe2e5; padding: 8px 12px; text-align: left; }}
        th {{ background-color: #f6f8fa; }}
        .badge {{ background: #0366d6; color: white; padding: 2px 6px; border-radius: 3px; font-size: 12px; }}
    </style>
</head>
<body>
    <h1>Web Watcher 离线审计报告</h1>
    <div class="meta">
        <p><strong>生成时间:</strong> {data['generated_at'].strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
        <p><strong>时间跨度:</strong> {since_str or 'All'} | <strong>事件数:</strong> {len(events)} | <strong>通知数:</strong> {len(notifications)}</p>
    </div>
    <h2>事件列表</h2>
    <table>
        <thead>
            <tr><th>Event ID</th><th>Title</th><th>Importance</th><th>Status</th></tr>
        </thead>
        <tbody>
            {tbody}
        </tbody>
    </table>
</body>
</html>"""
