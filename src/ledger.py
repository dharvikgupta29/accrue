"""SQLite-backed append-only event log — the memory Cedar doesn't have.

See PRIMER.md Part 2. Rows are only ever inserted, never updated or deleted;
that's what makes the totals in aggregates.py trustworthy.
"""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    timestamp   TEXT,
    session_id  TEXT,
    action      TEXT,
    resource    TEXT,
    cost_usd    REAL,
    decision    TEXT
);
"""


def connect(db_path="ledger.db"):
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def insert_event(conn, timestamp, session_id, action, resource, cost_usd, decision):
    conn.execute(
        "INSERT INTO events "
        "(timestamp, session_id, action, resource, cost_usd, decision) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (timestamp, session_id, action, resource, cost_usd, decision),
    )
