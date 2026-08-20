"""SQLite schema for Web Watcher core state."""

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL PRIMARY KEY,
    applied_at TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (0, datetime('now'));

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
    payload TEXT,
    dispatch_owner TEXT,
    dispatch_until TEXT,
    dispatch_token TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
    UNIQUE(event_id, channel)
);

CREATE TABLE IF NOT EXISTS investigation_results (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT,
    metadata TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events (id)
);

CREATE TABLE IF NOT EXISTS investigation_evidence (
    id TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (investigation_id) REFERENCES investigation_results (id)
);

CREATE TABLE IF NOT EXISTS fetch_state (
    target_key TEXT PRIMARY KEY,
    etag TEXT,
    last_modified TEXT,
    content_hash TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS host_rate_limits (
    host TEXT PRIMARY KEY,
    next_allowed_at TEXT,
    claim_token TEXT,
    claimed_at TEXT,
    claim_until TEXT
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
