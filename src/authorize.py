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
from datetime import datetime, timezone
from pathlib import Path

from cedar_policy import Authorizer, Context, Decision, Entities, PolicySet, Request

from . import aggregates, ledger

POLICY_PATH = Path(__file__).parent / "policies" / "budget.cedar"
_POLICIES = PolicySet.from_str(POLICY_PATH.read_text())
_ENTITIES = Entities.from_json_str("[]")
_AUTHORIZER = Authorizer()


def authorize_transfer(conn, session_id, resource, amount_usd):
    """Ask "can this transfer happen?", accounting for everything this
    session has already spent. Returns "ALLOW" or "DENY" and logs the
    attempt either way — only ALLOWed amounts count toward future totals.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        already_spent = aggregates.cumulative_cost(conn, session_id)
        prospective_total = already_spent + amount_usd

        request = Request(
            principal=f'Session::"{session_id}"',
            action='Action::"transfer"',
            resource=f'Account::"{resource}"',
            context=Context.from_json_str(
                json.dumps({"cumulative_cost": prospective_total})
            ),
        )
        response = _AUTHORIZER.is_authorized(request, _POLICIES, _ENTITIES)
        decision = "ALLOW" if response.decision == Decision.Allow else "DENY"

        ledger.insert_event(
            conn,
            datetime.now(timezone.utc).isoformat(),
            session_id,
            "transfer",
            resource,
            amount_usd,
            decision,
        )
        conn.commit()
        return decision
    except Exception:
        conn.rollback()
        raise
