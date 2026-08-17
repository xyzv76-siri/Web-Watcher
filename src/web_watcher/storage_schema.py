"""SQLite schema for Web Watcher core state."""

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL,
    signal_type TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    value TEXT,
    fingerprint TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    UNIQUE(entity_id, signal_type, fingerprint)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    importance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS event_signals (
    event_id INTEGER NOT NULL,
    signal_id INTEGER NOT NULL,
    PRIMARY KEY(event_id, signal_id),
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
    FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
    UNIQUE(event_id, channel)
);

CREATE TABLE IF NOT EXISTS fetch_state (
    target_key TEXT PRIMARY KEY,
    etag TEXT,
    last_modified TEXT,
    content_hash TEXT,
    fetched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_entity
    ON signals(entity_id);

CREATE INDEX IF NOT EXISTS idx_signals_observed
    ON signals(observed_at);

CREATE INDEX IF NOT EXISTS idx_events_entity
    ON events(entity_id);

CREATE INDEX IF NOT EXISTS idx_events_status
    ON events(status);

CREATE INDEX IF NOT EXISTS idx_notifications_event
    ON notifications(event_id);

CREATE INDEX IF NOT EXISTS idx_notifications_channel
    ON notifications(channel);

CREATE INDEX IF NOT EXISTS idx_fetch_state_hash
    ON fetch_state(content_hash);
"""
