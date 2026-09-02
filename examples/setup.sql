"""Session-1 learning example. Not part of the project.
The real schema lives in src/ledger.py."""

.headers on
.mode box

DROP TABLE IF EXISTS events;

CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    session_id TEXT,
    action TEXT,
    resource TEXT,
    cost_usd REAL
);

INSERT INTO events (timestamp, session_id, action, resource, cost_usd) VALUES
  ('10:04:12', 'sess-001', 'read_file',  'config.py', NULL),
  ('10:04:15', 'sess-001', 'read_file',  'main.py',   NULL),
  ('10:04:22', 'sess-001', 'write_file', 'main.py',   NULL),
  ('10:04:31', 'sess-001', 'transfer',   'acct-1234', 9999),
  ('10:04:48', 'sess-001', 'transfer',   'acct-1234', 9999);

SELECT * FROM events;

SELECT SUM(cost_usd) FROM events WHERE session_id = 'sess-001';
SELECT COUNT(*) FROM events WHERE session_id = 'sess-001';
SELECT COUNT(DISTINCT resource) FROM events WHERE session_id = 'sess-001';
