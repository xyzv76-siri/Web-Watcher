"""Lightweight local monitoring dashboard (Web UI v1).

Zero external dependencies: built on Python standard library ``http.server``.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

from .repository import Repository
from .models import Event, Entity

logger = logging.getLogger(__name__)


class WebUIHandler(SimpleHTTPRequestHandler):
    """HTTP handler for Web UI routes and JSON APIs."""

    server: "WebUIServer"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._serve_html("dashboard")
        elif path == "/targets":
            self._serve_html("targets")
        elif path.startswith("/events/"):
            event_id = path.split("/")[-1]
            self._serve_html("event_detail", event_id=event_id)
        elif path == "/api/targets":
            self._api_targets()
        elif path == "/api/events":
            self._api_events(parse_qs(parsed.query))
        elif path.startswith("/api/events/"):
            event_id = path.split("/")[-1]
            self._api_event_detail(event_id)
        elif path == "/api/stats":
            self._api_stats()
        else:
            self.send_error(404, "Not Found")

    def _send_json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, content: str, status: int = 200) -> None:
        payload = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _api_targets(self) -> None:
        repo: Repository = self.server.repository
        rows = repo.connection.execute("""
            SELECT e.id, e.canonical_key, e.name, e.entity_type, e.created_at
            FROM entities e
            ORDER BY e.created_at DESC
        """).fetchall()
        data = {
            "targets": [
                {
                    "id": row["id"],
                    "entity_key": row["canonical_key"],
                    "name": row["name"],
                    "type": row["entity_type"],
                    "created_at": self.server._serialize_dt(self.server._parse_iso(row["created_at"])),
                }
                for row in rows
            ]
        }
        self._send_json(data)

    def _api_events(self, query: Dict[str, List[str]]) -> None:
        repo: Repository = self.server.repository
        limit = int(query.get("limit", ["50"])[0])
        offset = int(query.get("offset", ["0"])[0])

        since = self.server._parse_iso(query.get("since", [None])[0])
        until = self.server._parse_iso(query.get("until", [None])[0])

        importance = query.get("importance", [])
        status = query.get("status", [])

        clauses: List[str] = []
        params: List[Any] = []

        if importance:
            clauses.append(f"event_type IN ({', '.join('?' * len(importance))})")
            params.extend(importance)
        if status:
            clauses.append(f"status IN ({', '.join('?' * len(status))})")
            params.extend(status)
        if since:
            clauses.append("created_at >= ?")
            params.append(self.server._serialize_dt(since))
        if until:
            clauses.append("created_at <= ?")
            params.append(self.server._serialize_dt(until))

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        count_sql = f"SELECT COUNT(*) as cnt FROM events {where}"
        total = repo.connection.execute(count_sql, params).fetchone()["cnt"]

        list_sql = f"""
            SELECT id, entity_id, event_type, status, importance, created_at
            FROM events
            {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
        rows = repo.connection.execute(list_sql, params + [limit, offset]).fetchall()
        events = []
        for row in rows:
            signal_count = repo.connection.execute(
                "SELECT COUNT(*) as cnt FROM event_signals WHERE event_id = ?", (row["id"],)
            ).fetchone()["cnt"]
            events.append({
                "id": row["id"],
                "entity_id": row["entity_id"],
                "event_type": row["event_type"],
                "importance": row["importance"],
                "status": row["status"],
                "created_at": self.server._serialize_dt(self.server._parse_iso(row["created_at"])),
                "signal_count": signal_count,
            })

        data = {
            "events": events,
            "total": total,
            "limit": limit,
            "offset": offset,
        }
        self._send_json(data)

    def _api_event_detail(self, event_id: str) -> None:
        repo: Repository = self.server.repository
        try:
            event_id_int = int(event_id)
        except ValueError:
            self._send_json({"error": "invalid event id"}, status=400)
            return

        row = repo.connection.execute(
            """
            SELECT id, entity_id, event_type, status, importance, created_at
            FROM events
            WHERE id = ?
            """,
            (event_id_int,),
        ).fetchone()
        if row is None:
            self._send_json({"error": "not found"}, status=404)
            return

        signal_rows = repo.connection.execute("""
            SELECT s.id, s.signal_type, s.observed_at, s.fingerprint
            FROM signals s
            JOIN event_signals es ON es.signal_id = s.id
            WHERE es.event_id = ?
            ORDER BY s.observed_at DESC
        """, (event_id_int,)).fetchall()

        data = {
            "event": {
                "id": row["id"],
                "entity_id": row["entity_id"],
                "event_type": row["event_type"],
                "importance": row["importance"],
                "status": row["status"],
                "created_at": self.server._serialize_dt(self.server._parse_iso(row["created_at"])),
            },
            "signals": [
                {
                    "id": r["id"],
                    "signal_type": r["signal_type"],
                    "observed_at": self.server._serialize_dt(self.server._parse_iso(r["observed_at"])),
                    "value": "",
                    "fingerprint": r["fingerprint"],
                }
                for r in signal_rows
            ],
        }
        self._send_json(data)

    def _api_stats(self) -> None:
        repo: Repository = self.server.repository
        now = datetime.now(timezone.utc)
        day_ago = now.replace(hour=0, minute=0, second=0, microsecond=0)

        entities_count = repo.connection.execute("SELECT COUNT(*) as cnt FROM entities").fetchone()["cnt"]
        events_24h = repo.connection.execute(
            "SELECT COUNT(*) as cnt FROM events WHERE created_at >= ?",
            (self.server._serialize_dt(day_ago),),
        ).fetchone()["cnt"]

        notifications = repo.list_notifications(limit=1000)
        pending = sum(1 for n in notifications if n.status == "pending")
        failed = sum(1 for n in notifications if n.status == "failed")

        by_importance: Dict[str, int] = {}
        rows = repo.connection.execute("""
            SELECT importance, COUNT(*) as cnt
            FROM events
            WHERE created_at >= ?
            GROUP BY importance
        """, (self.server._serialize_dt(day_ago),)).fetchall()
        for row in rows:
            by_importance[row["importance"]] = row["cnt"]

        data = {
            "targets_count": entities_count,
            "events_24h": events_24h,
            "events_7d": events_24h,
            "notifications_pending": pending,
            "notifications_failed": failed,
            "by_importance": by_importance,
        }
        self._send_json(data)

    def _serve_html(self, page: str, **kwargs: Any) -> None:
        html = HTML_TEMPLATES.get(page, "<h1>Not Found</h1>")
        for key, value in kwargs.items():
            html = html.replace("{{" + key + "}}", str(value))
        self._send_html(html)


class WebUIServer(HTTPServer):
    """HTTP server with repository access."""

    def __init__(self, host: str, port: int, repository: Repository):
        super().__init__((host, port), WebUIHandler)
        self.repository = repository

    @staticmethod
    def _serialize_dt(value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @staticmethod
    def _parse_iso(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None


HTML_TEMPLATES = {
    "dashboard": """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>Web-Watcher Dashboard</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 24px; background: #f5f5f5; color: #222; }
    h1 { font-size: 20px; margin: 0 0 16px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }
    .card { background: #fff; border: 1px solid #e5e5e5; border-radius: 8px; padding: 16px; }
    .card h3 { margin: 0 0 8px; font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 0.05em; }
    .card .value { font-size: 28px; font-weight: 600; }
    table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e5e5e5; border-radius: 8px; overflow: hidden; }
    th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #f0f0f0; font-size: 14px; }
    th { background: #fafafa; color: #666; font-weight: 500; }
    a { color: #0066cc; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .status { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; background: #e5e5e5; }
  </style>
</head>
<body>
  <h1>Web-Watcher Dashboard</h1>
  <div class="grid" id="stats"></div>
  <h2>最近事件</h2>
  <table>
    <thead>
      <tr><th>时间</th><th>目标</th><th>类型</th><th>重要性</th><th>状态</th><th>信号</th></tr>
    </thead>
    <tbody id="events"></tbody>
  </table>
  <script>
    async function load() {
      const statsRes = await fetch('/api/stats');
      const stats = await statsRes.json();
      document.getElementById('stats').innerHTML = `
        <div class="card"><h3>目标</h3><div class="value">${stats.targets_count}</div></div>
        <div class="card"><h3>24h 事件</h3><div class="value">${stats.events_24h}</div></div>
        <div class="card"><h3>通知待发</h3><div class="value">${stats.notifications_pending}</div></div>
        <div class="card"><h3>通知失败</h3><div class="value">${stats.notifications_failed}</div></div>
      `;

      const eventsRes = await fetch('/api/events?limit=20');
      const events = await eventsRes.json();
      const tbody = document.getElementById('events');
      tbody.innerHTML = events.events.map(ev => `
        <tr>
          <td>${ev.created_at}</td>
          <td>${ev.entity_id}</td>
          <td>${ev.event_type}</td>
          <td><span class="status">${ev.importance}</span></td>
          <td><span class="status">${ev.status}</span></td>
          <td><a href="/events/${ev.id}">${ev.signal_count} 个信号</a></td>
        </tr>
      `).join('');
    }
    load();
  </script>
</body>
</html>""",
    "targets": """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>Targets — Web-Watcher</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 24px; background: #f5f5f5; color: #222; }
    h1 { font-size: 20px; margin: 0 0 16px; }
    a { color: #0066cc; text-decoration: none; }
    a:hover { text-decoration: underline; }
    table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e5e5e5; border-radius: 8px; overflow: hidden; }
    th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #f0f0f0; font-size: 14px; }
    th { background: #fafafa; color: #666; font-weight: 500; }
    .status { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; background: #e5e5e5; }
  </style>
</head>
<body>
  <h1>Targets</h1>
  <table>
    <thead><tr><th>ID</th><th>URL</th><th>类型</th><th>状态</th><th>最后检查</th></tr></thead>
    <tbody id="targets"></tbody>
  </table>
  <script>
    async function load() {
      const res = await fetch('/api/targets');
      const data = await res.json();
      document.getElementById('targets').innerHTML = data.targets.map(t => `
        <tr>
          <td>${t.id}</td>
          <td><a href="${t.url}" target="_blank">${t.url}</a></td>
          <td>${t.type}</td>
          <td><span class="status">${t.status}</span></td>
          <td>${t.last_checked_at || '-'}</td>
        </tr>
      `).join('');
    }
    load();
  </script>
</body>
</html>""",
    "event_detail": """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>Event {{event_id}} — Web-Watcher</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 24px; background: #f5f5f5; color: #222; }
    h1 { font-size: 20px; margin: 0 0 16px; }
    a { color: #0066cc; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .meta { background: #fff; border: 1px solid #e5e5e5; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
    .meta dt { font-size: 12px; color: #666; text-transform: uppercase; margin-top: 8px; }
    .meta dd { margin: 4px 0 0; font-size: 14px; }
    .status { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; background: #e5e5e5; }
    table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e5e5e5; border-radius: 8px; overflow: hidden; }
    th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #f0f0f0; font-size: 14px; }
    th { background: #fafafa; color: #666; font-weight: 500; }
    pre { background: #fafafa; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 12px; }
  </style>
</head>
<body>
  <h1>Event {{event_id}}</h1>
  <dl class="meta" id="meta"></dl>
  <h2>Signals</h2>
  <table>
    <thead><tr><th>ID</th><th>类型</th><th>观察时间</th><th>指纹</th></tr></thead>
    <tbody id="signals"></tbody>
  </table>
  <script>
    async function load() {
      const res = await fetch('/api/events/{{event_id}}');
      const data = await res.json();
      const ev = data.event;
      document.getElementById('meta').innerHTML = `
        <dt>目标</dt><dd>${ev.entity_id}</dd>
        <dt>类型</dt><dd>${ev.event_type}</dd>
        <dt>重要性</dt><dd><span class="status">${ev.importance}</span></dd>
        <dt>状态</dt><dd><span class="status">${ev.status}</span></dd>
        <dt>创建时间</dt><dd>${ev.created_at}</dd>
      `;
      document.getElementById('signals').innerHTML = data.signals.map(s => `
        <tr>
          <td>${s.id}</td>
          <td>${s.signal_type}</td>
          <td>${s.observed_at}</td>
          <td><code>${s.fingerprint}</code></td>
        </tr>
      `).join('');
    }
    load();
  </script>
</body>
</html>""",
}
