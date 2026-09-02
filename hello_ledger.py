import sqlite3

conn = sqlite3.connect("ledger.db")

cur = conn.cursor()
cur.execute("DROP TABLE IF EXISTS events")

cur.execute("""
    CREATE TABLE events (
        id INTEGER PRIMARY KEY,
        timestamp TEXT,
        session_id TEXT,
        action TEXT,
        resource TEXT,
        cost_usd REAL
    )
""")

rows = [
    ("10:04:12", "sess-001", "read_file", "config.py", None),
    ("10:04:15", "sess-001", "read_file", "main.py", None),
    ("10:04:22", "sess-001", "write_file", "main.py", None),
    ("10:04:31", "sess-001", "transfer", "acct-1234", 9999),
    ("10:04:48", "sess-001", "transfer", "acct-1234", 9999),
]

cur.executemany(
    "INSERT INTO events (timestamp, session_id, action, resource, cost_usd) "
    "VALUES (?, ?, ?, ?, ?)",
    rows,
)

conn.commit()

session = "sess-001"

cur.execute("SELECT SUM(cost_usd) FROM events WHERE session_id = ?", (session,))
total = cur.fetchone()[0]

cur.execute("SELECT COUNT(*) FROM events WHERE session_id = ?", (session,))
calls = cur.fetchone()[0]

cur.execute(
    "SELECT COUNT(DISTINCT resource) FROM events WHERE session_id = ?", (session,)
)
resources = cur.fetchone()[0]

print("total cost:        ", total)
print("actions:           ", calls)
print("distinct resources:", resources)

conn.close()
