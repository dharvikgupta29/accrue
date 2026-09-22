"""The glue between the append-only ledger and Cedar.

Cedar stays exactly as stateless as it's designed to be: it only ever sees a
`cumulative_cost` number that already includes the pending action. All the
"memory" lives in ledger.py / aggregates.py — never inside a policy.

The check (read the running total) and the act (log this attempt) happen
inside one SQLite transaction opened with BEGIN IMMEDIATE, so two concurrent
requests can't both read the same stale total and both get approved. That
race — not Cedar's statelessness itself — is the actual loophole this file
exists to close. See PRIMER.md §1.4.
"""

import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import aggregates, breaker, ledger

POLICY_PATH = Path(__file__).parent / "policies" / "budget.cedar"


def _ask_cedar(principal, action, resource, context):
    """Shell out to the `cedar` CLI and return "ALLOW" or "DENY".

    Cedar does not abort when a policy errors while being evaluated (e.g. a
    context attribute typo'd in a `when` clause, or a request built with a
    different context shape than the policy expects). It drops just that
    policy and keeps going with whatever's left - so a `forbid` that
    references a bad attribute can be silently skipped and a `permit`
    elsewhere wins by default. The CLI still exits 0 for that case; the only
    sign anything went wrong is extra text on stdout after the decision
    word. A clean decision is *only* "ALLOW" or "DENY" - anything else means
    a policy errored and the decision can't be trusted, so this raises
    instead of returning it. `cedar validate` (run in CI against
    budget.cedarschema) is what's supposed to catch these before they ship;
    this check is the last line of defense if one gets through anyway.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        context_path = Path(tmpdir) / "context.json"
        entities_path = Path(tmpdir) / "entities.json"
        context_path.write_text(json.dumps(context))
        entities_path.write_text("[]")

        result = subprocess.run(
            [
                "cedar",
                "authorize",
                "--policies", str(POLICY_PATH),
                "--entities", str(entities_path),
                "--principal", principal,
                "--action", action,
                "--resource", resource,
                "--context", str(context_path),
            ],
            capture_output=True,
            text=True,
        )
        # The CLI exits 0 for ALLOW and 2 for DENY - both are real decisions.
        # Anything else (bad policy file, malformed context, ...) is a tool
        # failure and must not be silently treated as a DENY decision.
        if result.returncode not in (0, 2):
            raise RuntimeError(f"cedar authorize failed: {result.stderr}")

        decision = result.stdout.strip()
        if decision not in ("ALLOW", "DENY"):
            raise RuntimeError(
                "cedar authorize returned more than a clean decision - a "
                "policy likely errored while evaluating and was silently "
                f"dropped, so this can't be trusted: {decision!r}"
            )
        return decision


def authorize_transfer(conn, session_id, resource, amount_usd, scope_id=None):
    """Ask "can this transfer happen?", accounting for everything this
    scope has already spent - across every session under it, not just this
    one. Returns "ALLOW" or "DENY" and logs the attempt either way — only
    ALLOWed amounts count toward future totals.

    `scope_id` is the durable cross-session identity to check and log this
    event under (see aggregates.cumulative_cost_for_scope - it rolls up a
    flat scope_id or a filesystem-shaped path the same way). Defaults to
    session_id, so a caller with no separate scope concept yet behaves
    exactly as before - a lone session is just a scope of one.
    """
    scope_id = scope_id or session_id
    conn.execute("BEGIN IMMEDIATE")
    try:
        timestamp = datetime.now(timezone.utc).isoformat()

        # A tripped scope doesn't get to ask again, no matter how small the
        # amount or how much cumulative-cost headroom remains - that's the
        # whole point of the breaker (see src/breaker.py). Only an explicit
        # ledger.insert_override for this scope resets it.
        if breaker.is_tripped(conn, scope_id):
            ledger.insert_event(
                conn, timestamp, session_id, "transfer", resource, amount_usd,
                "DENY", scope_id=scope_id,
            )
            conn.commit()
            return "DENY"

        already_spent = aggregates.cumulative_cost_for_scope(conn, scope_id)
        prospective_total = already_spent + amount_usd

        decision = _ask_cedar(
            f'Session::"{session_id}"',
            'Action::"transfer"',
            f'Account::"{resource}"',
            # Cedar's JSON format has no float type - only Long integers -
            # so `cumulative_cost` (a REAL column, always a float in Python)
            # has to be rounded to whole dollars before it crosses the CLI
            # boundary, or the request is rejected outright as malformed.
            {"cumulative_cost": round(prospective_total)},
        )

        ledger.insert_event(
            conn, timestamp, session_id, "transfer", resource, amount_usd,
            decision, scope_id=scope_id,
        )
        conn.commit()
        return decision
    except Exception:
        conn.rollback()
        raise
