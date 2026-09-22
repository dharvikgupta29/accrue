"""Circuit breaker for cross-session scopes.

The 50x-$9,999 pattern (PRIMER.md 1.4) is already closed by
aggregates.cumulative_cost: DENYs don't count, so retrying the *same*
amount keeps failing once the running total is over budget. This solves a
different problem - an agent (or attacker) that doesn't retry the same
amount, but *searches* for one that slips under: try $9,999, get denied,
try $500, try $50, try $1... Cumulative cost alone can't see that pattern,
because each smaller request might individually still fit.

The rule this file implements instead: once a scope has been denied, it's
done - every further request for that scope auto-DENYs, and Cedar isn't
even asked - until a human appends an explicit override (see
ledger.insert_override). authorize.py should call is_tripped() before its
existing cumulative_cost check, so a tripped scope never even gets that
far.
"""

from . import aggregates, ledger


def is_tripped(conn, scope_id):
    """True if the most recent event for this scope was a DENY with no
    override logged after it.

    TODO:
      1. last = aggregates.last_event_for_scope(conn, scope_id)
      2. no last event, or its decision isn't "DENY" -> not tripped, False
      3. otherwise, pull the DENY's timestamp out of `last` and check
         ledger.list_overrides(conn, scope_id) for one whose timestamp is
         greater than it (ISO 8601 strings compare correctly as plain
         Python strings - same property used in
         aggregates.cumulative_cost_for_scope)
      4. any override after the DENY -> False (reset). None -> True
         (tripped).

    Remember `last` is a raw sqlite3 row (a tuple) in the column order from
    list_events/last_event_for_scope's SELECT - index into it, or unpack it,
    to get timestamp and decision back out.
    """
    raise NotImplementedError
