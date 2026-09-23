"""Cedar is stateless (PRIMER.md 1.3-1.5); this file proves authorize.py is
the thing that actually keeps score, and that it fails closed rather than
open when Cedar can't be trusted.
"""

from datetime import datetime, timezone

import pytest

from src import authorize, ledger
from src.authorize import _ask_cedar


@pytest.fixture
def conn():
    return ledger.connect(":memory:")


def test_50x_9999_eventually_denies(conn):
    """PRIMER.md's motivating example: no single $9,999 transfer looks like
    a problem, but the running total does. Cedar can't see the total -
    authorize.py must catch it once cumulative spend crosses $10,000.
    """
    decisions = [
        authorize.authorize_transfer(conn, "sess-001", "acct-1234", 9999)
        for _ in range(5)
    ]

    # 9999, 19998, 29997, ... - crosses 10000 on the second transfer.
    assert decisions == ["ALLOW", "DENY", "DENY", "DENY", "DENY"]


def test_denied_attempts_do_not_inflate_the_total(conn):
    """A burst of rejected requests must not itself push a session's
    cumulative cost over budget - only ALLOWed amounts count toward future
    totals. But since src/breaker.py, that's no longer the whole story: the
    first DENY trips the circuit breaker for this scope, and a tripped
    scope stays denied - regardless of remaining cumulative-cost headroom -
    until a human logs an explicit override. This test now proves both
    halves: the breaker's stronger default, and that an override really
    does hand control back to the headroom-based check.
    """
    authorize.authorize_transfer(conn, "sess-001", "acct-1234", 9999)  # ALLOW
    for _ in range(10):
        authorize.authorize_transfer(conn, "sess-001", "acct-1234", 9999)  # DENY, trips the breaker

    # Cumulative cost alone still has 1 (10000 - 9999) of headroom left, but
    # the breaker is tripped - denied outright without even asking Cedar.
    decision = authorize.authorize_transfer(conn, "sess-001", "acct-1234", 1)
    assert decision == "DENY"

    # An explicit override resets the breaker - the headroom-based check
    # runs again, and this $1 request fits in what's left.
    ledger.insert_override(
        conn, datetime.now(timezone.utc).isoformat(), "sess-001", "sid",
        "test: confirming override un-trips the breaker",
    )
    conn.commit()
    decision = authorize.authorize_transfer(conn, "sess-001", "acct-1234", 1)
    assert decision == "ALLOW"


def test_budget_is_shared_across_sessions_under_the_same_scope(conn):
    """The whole point of scope_id: two different session_ids sharing one
    scope_id share one budget. $6000 in sess-A alone would fit; $6000 more
    in sess-B alone would fit too - but together, under the same scope,
    they cross $10,000 and the second one must DENY.
    """
    first = authorize.authorize_transfer(
        conn, "sess-A", "acct-1234", 6000, scope_id="agent-a"
    )
    second = authorize.authorize_transfer(
        conn, "sess-B", "acct-1234", 6000, scope_id="agent-a"
    )
    assert (first, second) == ("ALLOW", "DENY")


def test_exactly_10000_is_allowed(conn):
    """Documents current behavior: budget.cedar's forbid triggers on
    `context.cumulative_cost > 10000`, so a total of exactly $10,000 is
    ALLOWed, not blocked. If the intent was an inclusive $10,000 cap, the
    policy needs `>=` instead - flagging here so a policy change shows up
    as a failing test, not a silent behavior change.
    """
    decision = authorize.authorize_transfer(conn, "sess-001", "acct-1234", 10000)
    assert decision == "ALLOW"


def test_cedar_policy_error_fails_closed():
    """Regression test for the fail-open bug: Cedar drops a policy that
    errors while evaluating (e.g. a context attribute typo) instead of
    aborting, and still exits 0. A context shape the policy doesn't
    recognize must raise, never silently return ALLOW.
    """
    with pytest.raises(RuntimeError, match="silently dropped"):
        _ask_cedar(
            'Session::"sess-001"',
            'Action::"transfer"',
            'Account::"acct-1234"',
            {"cumulative_kost": 50000},  # typo: not a real context attribute
        )


def test_clean_allow_and_deny_still_work():
    assert _ask_cedar(
        'Session::"sess-001"', 'Action::"transfer"', 'Account::"acct-1234"',
        {"cumulative_cost": 500},
    ) == "ALLOW"
    assert _ask_cedar(
        'Session::"sess-001"', 'Action::"transfer"', 'Account::"acct-1234"',
        {"cumulative_cost": 20000},
    ) == "DENY"
