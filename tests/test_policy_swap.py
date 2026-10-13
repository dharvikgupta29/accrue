"""experiments.policy.budget_policy is what the stress suite's budget-cut
results rest on, and attack.attack_accrue is the attacker run against the
real authorize path. Neither imports the ML packages, so CI runs these.
"""

import shutil

import pytest

from experiments import attack
from experiments.policy import budget_policy
from src import authorize, ledger

pytestmark = pytest.mark.skipif(shutil.which("cedar") is None, reason="needs the cedar CLI")


def decide(amount):
    conn = ledger.connect(":memory:")
    try:
        return authorize.authorize_transfer(conn, "s", "acct", amount)
    finally:
        conn.close()


def test_budget_policy_changes_the_cap_and_restores_it():
    original = authorize.POLICY_PATH
    assert decide(6_000) == "ALLOW"
    with budget_policy(5_000):
        assert authorize.POLICY_PATH != original
        assert decide(5_000) == "ALLOW"
        assert decide(5_001) == "DENY"
    assert authorize.POLICY_PATH == original
    assert decide(6_000) == "ALLOW"


def test_budget_policy_restores_the_cap_after_an_error():
    original = authorize.POLICY_PATH
    with pytest.raises(RuntimeError, match="boom"):
        with budget_policy(5_000):
            raise RuntimeError("boom")
    assert authorize.POLICY_PATH == original


def test_budget_policy_at_the_shipped_cap_changes_nothing():
    original = authorize.POLICY_PATH
    with budget_policy(10_000):
        assert authorize.POLICY_PATH == original


@pytest.mark.parametrize("budget", [10_000, 5_000])
def test_attacker_never_gets_more_than_the_budget_from_accrue(budget):
    with budget_policy(budget):
        strategy, history = attack.attack_accrue(budget)
    assert sum(history) <= budget
    # And it isn't just refusing everything: it gets close to the cap.
    assert sum(history) >= budget * 0.99, (strategy, history)
