# NOTES

## Next action
Find a real agent log on this machine (~/.codex, ~/.copilot, ~/.claude), open one,
and write down whether it has: timestamp, session id, tool name, resource, cost.

## Ideas (branch: feat/cross-session-scopes)

### Cross-session scopes

`session_id` is one conversation; it never outlives it. A `scope_id` is the
durable identity underneath - the same agent/user across many sessions - so
budget can be checked against "everything this agent has ever done," not
just "everything in this one conversation."

- `events.scope_id` (new column) - defaults to `session_id` when a caller
  has no real cross-session identity yet, so nothing breaks.
- `aggregates.cumulative_cost_for_scope(conn, scope_id, since=None)` rolls
  up cost across every session under a scope, same ALLOW-only rule as
  `cumulative_cost`.
- Rolling window via `since`, not infinite accumulation - otherwise a scope
  that's been alive a year eventually can't do anything. ISO 8601
  timestamps sort lexically same as chronologically, so it's a plain string
  comparison (`timestamp >= ?`), no date parsing.
- Cedar itself doesn't change - it still only ever sees one
  `cumulative_cost` number in `context`. Only *what* number gets computed
  changes (per-scope-windowed instead of per-session).

### Circuit breaker on denied requests

The 50x-$9,999 case is already closed: DENYs don't count, so retrying the
same amount keeps failing. What isn't closed: an agent that *searches* for
an amount that slips under budget after a DENY (try $9,999, denied, try
$500, try $50...). Cumulative cost alone can't see that pattern.

- `overrides` table (new) - append-only, records a human explicitly saying
  "let this scope keep going": `timestamp`, `scope_id`, `authorized_by`,
  `reason`.
- `breaker.is_tripped(conn, scope_id)` - True if the scope's *most recent*
  event was a DENY with no override logged after it.
- `authorize_transfer` checks `is_tripped` first, before even calling
  Cedar. Tripped -> auto-DENY, log it, done. Not tripped -> proceed as
  today.
- Risk to design around: one borderline DENY otherwise permanently locks a
  scope. The override path has to be a first-class, auditable ledger event
  (who, when, why) - never a manual row deletion, which the append-only
  triggers block anyway.

### Layout added on this branch

| file | status |
|---|---|
| `src/ledger.py` | done - `scope_id` column, `overrides` table, `insert_override`/`list_overrides` |
| `src/aggregates.py` | TODO stubs - `cumulative_cost_for_scope`, `last_event_for_scope` |
| `src/breaker.py` | new file, TODO stub - `is_tripped` |
| `src/authorize.py` | `scope_id` threaded through; breaker check left as a TODO comment at the top of the transaction |
| `tests/test_scopes.py`, `tests/test_breaker.py` | full target-behavior tests, written to fail (`NotImplementedError`) until the TODOs above are filled in |

