"""Aggregate queries — the three things Cedar can't do, done in SQL.

Only rows with decision = 'ALLOW' count. A DENYed attempt never happened,
so it must not count toward the next check — otherwise a burst of rejected
requests would itself push a session over budget.
"""


def cumulative_cost(conn, session_id):
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM events "
        "WHERE session_id = ? AND decision = 'ALLOW'",
        (session_id,),
    ).fetchone()
    return row[0]


def action_count(conn, session_id):
    row = conn.execute(
        "SELECT COUNT(*) FROM events WHERE session_id = ? AND decision = 'ALLOW'",
        (session_id,),
    ).fetchone()
    return row[0]


def distinct_resource_count(conn, session_id):
    row = conn.execute(
        "SELECT COUNT(DISTINCT resource) FROM events "
        "WHERE session_id = ? AND decision = 'ALLOW'",
        (session_id,),
    ).fetchone()
    return row[0]


# --- Cross-session scopes ---------------------------------------------
#
# A session_id is one conversation. A scope_id is the thing that outlives
# it - the same agent/user across many sessions. See NOTES.md "Ideas" for
# the full design writeup; these two are the pieces authorize.py needs to
# actually use a scope instead of a lone session.

def cumulative_cost_for_scope(conn, scope_id, since=None):
    """Like cumulative_cost, but rolled up across every session under one
    durable scope_id, optionally windowed to only events at or after
    `since`.

    TODO: write the query. Two things to get right:
      - filter on scope_id instead of session_id (a scope spans many
        session_ids by design - that's the whole point of it)
      - if `since` is given, add `AND timestamp >= ?`. Timestamps in this
        table are ISO 8601 strings (what datetime.now(timezone.utc)
        .isoformat() produces) - they sort lexically in exactly the same
        order they sort chronologically, so a plain string comparison on
        the TEXT column is enough. No date parsing needed.
      - still only count decision = 'ALLOW', same reason as
        cumulative_cost above: a DENYed attempt never happened.

    Start from cumulative_cost above and adapt it - the shape is the same.
    """
    raise NotImplementedError


def last_event_for_scope(conn, scope_id):
    """The single most recent event for a scope, or None if it has none
    yet. breaker.is_tripped needs this to check whether the *last* thing
    that happened to a scope was a DENY.

    TODO:
      SELECT id, timestamp, session_id, scope_id, action, resource,
             cost_usd, decision
      FROM events WHERE scope_id = ? ORDER BY id DESC LIMIT 1

    fetchone() and return the row as-is (or None if nothing came back -
    fetchone() already gives you that for free on no match).
    """
    raise NotImplementedError
