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
    """Stand-in model: P(violation) = 1 when allowed_before + amount > cap,
    or - if it reads the breaker bit - when denied_before is set. It has to
    read every column a leak could show up in, or a guard fed the true
    ledger state would pass these tests too."""

    def __init__(self, cap, reads_denied):
        self.cap = cap
        self.reads_denied = reads_denied

    def predict_proba(self, X):
        import numpy as np
        p = X[:, 0] + X[:, 1] > self.cap
        if self.reads_denied:
            p |= X[:, 2] > 0
        p = p.astype(float)
        return np.column_stack([1 - p, p])


def guard(cap, reads_denied=True):
    g = FeatureMLP(["amount", "allowed_before", "denied_before"])
    g.model = FlagsAbove(cap, reads_denied)
    return g


def flags(g, t):
    return [p >= 0.5 for p in g.scores([t])[0]]


def test_denied_comes_from_the_guards_own_decisions():
    # True budget is 10,000, so the 9,000 call is the real violation and the
    # true ledger has denied_before set on the last call. This guard thinks
    # the cap is 20,000: it misses the 9,000, so its own state never says
    # "denied" and the 100 passes. Fed the true state, it would flag the 100.
    t = Trajectory(0, True, (3_000, 9_000, 100), limit=10_000)
    assert t.labels() == [False, True, True]
    assert flags(guard(20_000), t) == [False, False, False]


def test_a_flagged_call_stops_counting_toward_the_guards_total():
    # Nothing here is a real violation, so the true allowed total before the
    # 500 is 6,000. This guard thinks the cap is 5,000: it flags the 2,000,
    # so its own allowed total stays at 4,000 and the 500 fits (4,500).
    # Fed the true state, it would see 6,500 and flag the 500. Breaker bit
    # ignored here so the total is the only thing deciding.
    t = Trajectory(0, False, (4_000, 2_000, 500), limit=10_000)
    assert t.labels() == [False, False, False]
    assert flags(guard(5_000, reads_denied=False), t) == [False, True, False]
