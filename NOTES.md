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

### Scope paths — a filesystem-shaped hierarchy

Instead of `scope_id` being one flat opaque string, shape it like a path:
`session_id` stays a separate column (unchanged, one conversation) but
`scope_id` becomes a `/`-separated lineage, e.g.:

```
agent-a/task-checkout-flow
^^^^^^^ ^^^^^^^^^^^^^^^^^^
"home"        "task"
```

Three levels, filesystem-style:

| level | example | meaning | analogous to |
|---|---|---|---|
| root | `""` (empty prefix) | every agent, org-wide | `/` |
| home | `agent-a` | one durable agent/user across all its tasks and sessions | `/home/alice` |
| task | `agent-a/task-checkout-flow` | one objective, possibly spanning many sessions | a working directory |

**Why this works without a new table.** "Total spend under this node" is a
prefix match on the same `events.scope_id` column - the same trick `du`
uses on a real filesystem tree, no joins, no separate hierarchy table:

```sql
SELECT COALESCE(SUM(cost_usd), 0) FROM events
WHERE decision = 'ALLOW'
  AND (scope_id = ?           -- exact match at this node
       OR scope_id LIKE ? || '/%')   -- everything nested under it
```
Called once with `prefix = "agent-a"` that's the home-level total; once with
`prefix = "agent-a/task-checkout-flow"` that's the task-level total; called
with `prefix = ""` (skip the exact-match arm) that's everyone.

**Where the budget check actually lives - the open design question.**
`budget.cedar` today checks exactly one `cumulative_cost` number. A tree
means potentially a different cap per level, and the project's whole ethos
(README, CEDAR.md Part 5) is that the *decision* belongs in Cedar, not in
Python doing its own if-checks around it. So the shape to keep that intact:
`authorize.py` computes one cumulative number *per level* and hands Cedar
all of them at once:

```json
{ "cumulative_cost_task": 4000, "cumulative_cost_home": 22000, "cumulative_cost_root": 900000 }
```

and `budget.cedar` gets one `forbid` per level, each with its own constant
cap - `budget.cedarschema` grows to declare all three context fields, same
pattern as today's single `cumulative_cost`, just three of them.

**Open before building:**
- Start with two levels (home + task) rather than all three - root-level
  org-wide budgets are a real feature but not something this project has a
  stakeholder for yet; easy to add a third `forbid` later without
  reshaping anything.
- `scope_id` segments can't contain `%` or `_` (SQL `LIKE` wildcards) or the
  prefix match lies about what it's matching - validate segments when a
  scope_id is first constructed, don't discover this from a bug report.
- `aggregates.cumulative_cost_for_scope` (above) becomes the task-level case
  of a single prefix-aware function rather than a second implementation -
  worth writing it as the general one from the start.

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
| `src/aggregates.py` | done - `cumulative_cost_for_scope` is prefix-aware (flat scope_ids and `"a/b"` paths both work, `""` = root/everyone), `last_event_for_scope` |
| `src/breaker.py` | done - `is_tripped` |
| `src/authorize.py` | done - `scope_id` threaded through, breaker check wired in before the cumulative-cost check |
| `tests/test_scopes.py`, `tests/test_scope_paths.py`, `tests/test_breaker.py` | full target-behavior tests, all passing |

**Behavior change worth flagging:** `tests/test_authorize.py::test_denied_attempts_do_not_inflate_the_total`
used to prove a session could recover and get `ALLOW` again once it had
cumulative-cost headroom, even after prior DENYs. The breaker intentionally
overrides that - once tripped, a scope stays denied regardless of headroom
until a human calls `ledger.insert_override`. Updated the test to assert
the new default (`DENY`) and added an override step proving the old
headroom check does run again once explicitly approved.

**Still not done - the actual Cedar-side hierarchy enforcement.** Right
now `budget.cedar` only ever sees one `cumulative_cost` number computed
from `session_id`, same as before this branch; nothing calls
`cumulative_cost_for_scope` yet, and there's no second `forbid` for a
home-level cap. That's the real remaining "Scope paths" work, and it needs
answers to the open questions above (two levels vs three, what the caps
actually are) before touching `budget.cedar`/`budget.cedarschema` - flagged
rather than guessed.

