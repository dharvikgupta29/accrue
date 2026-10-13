"""FeatureMLP.scores runs ledger-state arms on their *own* decisions. If it
fed them the true ledger state instead, a guard that missed the crossing
call would still be told "a call was already denied" - the leak the stress
suite was fixed for. Needs torch and scikit-learn, so CI skips it.
"""

import pytest

pytest.importorskip("torch")
pytest.importorskip("sklearn")

from experiments.benchmark import Trajectory  # noqa: E402
from experiments.models import FeatureMLP  # noqa: E402


class FlagsAbove:
    """Stand-in model: P(violation) = 1 when allowed_before + amount > cap."""

    def __init__(self, cap):
        self.cap = cap

    def predict_proba(self, X):
        import numpy as np
        p = (X[:, 0] + X[:, 1] > self.cap).astype(float)
        return np.column_stack([1 - p, p])


def guard(cap):
    g = FeatureMLP(["amount", "allowed_before", "denied_before"])
    g.model = FlagsAbove(cap)
    return g


def test_state_comes_from_the_guards_own_decisions():
    # True budget is 10,000, so the 9,000 call is the real violation and the
    # ledger would mark denied_before on the last call. This guard thinks
    # the cap is 20,000: it misses the 9,000, so its own state never says
    # "denied" - and it must not flag the last call either.
    t = Trajectory(0, True, (3_000, 9_000, 100), limit=10_000)
    assert t.labels() == [False, True, True]
    assert [p >= 0.5 for p in guard(20_000).scores([t])[0]] == [False, False, False]


def test_a_flagged_call_stops_counting_toward_the_guards_total():
    # Guard with the right cap: it flags the 9,000, so that amount never
    # joins its allowed total - the following 100 is judged on 3,000 + 100.
    t = Trajectory(0, True, (3_000, 9_000, 100), limit=10_000)
    assert [p >= 0.5 for p in guard(10_000).scores([t])[0]] == [False, True, False]
