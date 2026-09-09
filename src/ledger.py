"""SQLite-backed append-only event log — the memory Cedar doesn't have.

See PRIMER.md Part 2. Rows are only ever inserted, never updated or deleted;
that's what makes the totals in aggregates.py trustworthy.
"""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    action      TEXT NOT NULL,
    resource    TEXT NOT NULL,
    cost_usd    REAL,
    decision    TEXT NOT NULL CHECK (decision IN ('ALLOW', 'DENY'))
);

CREATE INDEX IF NOT EXISTS idx_events_session_id ON events (session_id);

CREATE TRIGGER IF NOT EXISTS prevent_event_updates
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_event_deletes
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;
"""


def connect(db_path="ledger.db"):
    """Open a ledger database and create its schema if needed."""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def insert_event(conn, timestamp, session_id, action, resource, cost_usd, decision):
    """Append one event and return its database id.

    The caller owns the transaction. This is important for authorization:
    reading the aggregates and recording the decision must commit atomically.
    """
    cursor = conn.execute(
        "INSERT INTO events "
        "(timestamp, session_id, action, resource, cost_usd, decision) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (timestamp, session_id, action, resource, cost_usd, decision),
    )
    return cursor.lastrowid


def list_events(conn, session_id=None):
    """Return events in append order, optionally limited to one session."""
    if session_id is None:
        return conn.execute(
            "SELECT id, timestamp, session_id, action, resource, cost_usd, decision "
            "FROM events ORDER BY id"
        ).fetchall()

    return conn.execute(
        "SELECT id, timestamp, session_id, action, resource, cost_usd, decision "
        "FROM events WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()
