"""The benchmark is only worth anything if its construction holds. These pin
down the properties the per-call baseline experiment relies on.
"""

import re
import shutil
from pathlib import Path

import pytest

from experiments import benchmark

POLICY = Path(__file__).resolve().parent.parent / "src" / "policies" / "budget.cedar"


@pytest.fixture(scope="module")
def pairs():
    return benchmark.make_pairs(200, seed=0)


def test_budget_matches_the_policy():
    # If someone changes the cap in budget.cedar, the benchmark has to follow.
    match = re.search(r"context\.cumulative_cost\s*>\s*(\d+)", POLICY.read_text())
    assert match, "couldn't find the cumulative_cost forbid in budget.cedar"
    assert int(match.group(1)) == benchmark.BUDGET


def test_every_call_is_individually_legal(pairs):
    # The whole point: a per-call check has nothing to object to.
    for t in pairs:
        assert all(a <= benchmark.BUDGET for a in t.amounts)


def test_totals_split_the_classes(pairs):
    for t in pairs:
        total = sum(t.amounts)
        if t.violating:
            assert total > benchmark.BUDGET
        else:
            assert total <= benchmark.BUDGET


def test_violating_member_extends_its_compliant_twin(pairs):
    for compliant, violating in zip(pairs[0::2], pairs[1::2]):
        assert compliant.pair_id == violating.pair_id
        assert not compliant.violating and violating.violating
        n = len(compliant.amounts)
        assert violating.amounts[:n] == compliant.amounts
        assert len(violating.amounts) > n


def test_labels_cross_once_and_stay_crossed(pairs):
    for t in pairs:
        labels = t.labels()
        if not t.violating:
            assert not any(labels)
            continue
        first = labels.index(True)
        assert all(labels[first:])
        assert not any(labels[:first])


def test_same_seed_same_benchmark():
    assert benchmark.make_pairs(20, seed=7) == benchmark.make_pairs(20, seed=7)
    assert benchmark.make_pairs(20, seed=7) != benchmark.make_pairs(20, seed=8)


@pytest.mark.skipif(shutil.which("cedar") is None, reason="needs the cedar CLI")
def test_accrue_matches_the_labels_exactly():
    """accrue's per-call decisions should equal the ground truth: allow until
    the running total crosses, deny that call and (via the breaker) everything
    after. No learning involved, so anything short of exact is a bug.
    """
    # Deliberately not importing experiments.per_call_baseline - that pulls in
    # scikit-learn, and CI only installs pytest.
    from src import authorize, ledger

    conn = ledger.connect(":memory:")
    for i, t in enumerate(benchmark.make_pairs(15, seed=3)):
        flagged = [
            authorize.authorize_transfer(conn, f"bench-{i}", "acct-bench", a) == "DENY"
            for a in t.amounts
        ]
        assert flagged == t.labels(), t
