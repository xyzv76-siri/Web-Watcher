"""Unit tests for Web UI v1."""

import json
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from web_watcher.repository import Repository
from web_watcher.webui import WebUIServer, WebUIHandler


def _make_handler(server, path="/", method="GET", body=None):
    handler = WebUIHandler.__new__(WebUIHandler)
    handler.server = server
    handler.path = path
    handler.command = method
    handler.headers = MagicMock()
    handler.wfile = BytesIO()
    handler.rfile = BytesIO(body or b"")
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.close_connection = False
    handler.protocol_version = "HTTP/1.1"
    handler.client_address = ("127.0.0.1", 12345)
    return handler


def _extract_json(handler):
    raw = handler.wfile.getvalue().decode("utf-8")
    parts = raw.split("\r\n\r\n", 1)
    body = parts[1] if len(parts) > 1 else raw
    return json.loads(body)


def test_webui_server_initialization(tmp_path):
    db_path = str(tmp_path / "web_watcher.db")
    repo = Repository(db_path)
    server = WebUIServer("127.0.0.1", 0, repository=repo)
    assert server.repository == repo
    assert server.server_address[1] > 0
    server.server_close()


@patch("urllib.request.urlopen")
def test_api_targets(mock_urlopen, tmp_path):
    db_path = str(tmp_path / "web_watcher.db")
    repo = Repository(db_path)
    repo.create_entity(canonical_key="target:alpha", name="Alpha", entity_type="generic_web")

    server = WebUIServer("127.0.0.1", 0, repository=repo)
    handler = _make_handler(server, path="/api/targets")
    handler.do_GET()

    data = _extract_json(handler)
    assert len(data["targets"]) == 1
    assert data["targets"][0]["entity_key"] == "target:alpha"
    server.server_close()


@patch("urllib.request.urlopen")
def test_api_events(mock_urlopen, tmp_path):
    db_path = str(tmp_path / "web_watcher.db")
    repo = Repository(db_path)
    entity = repo.create_entity(canonical_key="target:alpha", name="Alpha", entity_type="generic_web")
    repo.create_event(entity_id=entity.id, event_type="content_change", importance="important", status="open")

    server = WebUIServer("127.0.0.1", 0, repository=repo)
    handler = _make_handler(server, path="/api/events?limit=10")
    handler.do_GET()

    data = _extract_json(handler)
    assert data["total"] == 1
    assert len(data["events"]) == 1
    server.server_close()


@patch("urllib.request.urlopen")
def test_api_event_detail(mock_urlopen, tmp_path):
    db_path = str(tmp_path / "web_watcher.db")
    repo = Repository(db_path)
    entity = repo.create_entity(canonical_key="target:alpha", name="Alpha", entity_type="generic_web")
    event = repo.create_event(entity_id=entity.id, event_type="content_change", importance="important", status="open")
    signal = repo.create_signal(entity_id=entity.id, signal_type="content_change", observed_at=datetime.now(timezone.utc), value="{}")
    if signal is not None:
        repo.attach_signal_to_event(event_id=event.id, signal_id=signal.id)

    server = WebUIServer("127.0.0.1", 0, repository=repo)
    handler = _make_handler(server, path=f"/api/events/{event.id}")
    handler.do_GET()

    data = _extract_json(handler)
    assert data["event"]["id"] == event.id
    assert len(data["signals"]) == 1
    server.server_close()


@patch("urllib.request.urlopen")
def test_api_stats(mock_urlopen, tmp_path):
    db_path = str(tmp_path / "web_watcher.db")
    repo = Repository(db_path)
    repo.create_entity(canonical_key="target:alpha", name="Alpha", entity_type="generic_web")

    server = WebUIServer("127.0.0.1", 0, repository=repo)
    handler = _make_handler(server, path="/api/stats")
    handler.do_GET()

    data = _extract_json(handler)
    assert data["targets_count"] == 1
    assert "events_24h" in data
    assert "by_importance" in data
    server.server_close()


def test_dashboard_page_served(tmp_path):
    db_path = str(tmp_path / "web_watcher.db")
    repo = Repository(db_path)
    server = WebUIServer("127.0.0.1", 0, repository=repo)
    handler = _make_handler(server, path="/")
    handler.do_GET()

    raw = handler.wfile.getvalue().decode("utf-8")
    assert "Web-Watcher Dashboard" in raw
    server.server_close()


def test_targets_page_served(tmp_path):
    db_path = str(tmp_path / "web_watcher.db")
    repo = Repository(db_path)
    server = WebUIServer("127.0.0.1", 0, repository=repo)
    handler = _make_handler(server, path="/targets")
    handler.do_GET()

    raw = handler.wfile.getvalue().decode("utf-8")
    assert "Targets" in raw
    server.server_close()


def test_event_detail_page_served(tmp_path):
    db_path = str(tmp_path / "web_watcher.db")
    repo = Repository(db_path)
    server = WebUIServer("127.0.0.1", 0, repository=repo)
    handler = _make_handler(server, path="/events/123")
    handler.do_GET()

    raw = handler.wfile.getvalue().decode("utf-8")
    assert "Event 123" in raw
    server.server_close()


def test_404_page(tmp_path):
    db_path = str(tmp_path / "web_watcher.db")
    repo = Repository(db_path)
    server = WebUIServer("127.0.0.1", 0, repository=repo)
    handler = _make_handler(server, path="/not-found")
    handler.do_GET()

    raw = handler.wfile.getvalue().decode("utf-8")
    assert "Not Found" in raw
    server.server_close()
