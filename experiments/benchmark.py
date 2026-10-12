"""Benchmark for the per-call baseline experiment.

Builds pairs of transfer trajectories that a per-call check can't tell apart:

- both members of a pair are made of calls drawn from the same distribution
- every single call is individually under the budget, so a rule that looks at
  one call at a time allows all of them
- the violating member is the compliant member plus a few more ordinary-looking
  calls

The only thing separating the two is the running total. That's the whole
point - it isolates the one property accrue exists to supply.

This data is synthetic on purpose. It's a controlled experiment, not a demo of
real usage: the construction is what guarantees per-call checks *can't* see the
difference, so you can tell a structural blind spot from a model that just
needs more training data.
"""

import random
from dataclasses import dataclass

# Must match the forbid in src/policies/budget.cedar.
# tests/test_benchmark.py fails if the two ever drift apart.
BUDGET = 10_000

# Every call is drawn from this range. MAX_AMOUNT < BUDGET is what makes each
# call individually legal.
MIN_AMOUNT = 500
MAX_AMOUNT = 4_000

# How many extra calls the violating member gets after the one that crosses.
MAX_EXTRA_CALLS = 2


@dataclass(frozen=True)
class Trajectory:
    pair_id: int
    violating: bool
    amounts: tuple

    def labels(self, budget=BUDGET):
        """Per-call ground truth: has the running total crossed the budget at
        or before this call?

        Once it crosses it stays crossed - the same semantics accrue enforces
        with the cumulative check plus the circuit breaker.
        """
        out, total = [], 0
        for amount in self.amounts:
            total += amount
            out.append(total > budget)
        return out

    def features(self):
        """One row per call. `amount` is all a per-call check gets to see.
        `position` and `running_total_before` are history - the kind of
        thing only something keeping state can supply.
        """
        rows, before = [], 0
        for i, amount in enumerate(self.amounts):
            rows.append({
                "amount": amount,
                "position": i,
                "running_total_before": before,
            })
            before += amount
        return rows


def make_pairs(n_pairs, seed=0, budget=BUDGET):
    """Return 2 * n_pairs trajectories: a compliant and a violating one per
    pair, in that order.

    The compliant one keeps drawing amounts until the next draw would cross
    the budget. That crossing draw is where the violating one diverges - it
    takes it, then 0 to MAX_EXTRA_CALLS more ordinary calls.
    """
    rng = random.Random(seed)
    trajectories = []

    for pair_id in range(n_pairs):
        prefix, total = [], 0
        while True:
            amount = rng.randint(MIN_AMOUNT, MAX_AMOUNT)
            if total + amount > budget:
                break
            prefix.append(amount)
            total += amount

        extra = [rng.randint(MIN_AMOUNT, MAX_AMOUNT)
                 for _ in range(rng.randint(0, MAX_EXTRA_CALLS))]
        tail = [amount] + extra

        trajectories.append(Trajectory(pair_id, False, tuple(prefix)))
        trajectories.append(Trajectory(pair_id, True, tuple(prefix + tail)))

    return trajectories
