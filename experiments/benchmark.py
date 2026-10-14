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
    limit: int = BUDGET

    def labels(self, budget=None):
        """Per-call ground truth, using exactly accrue's semantics: a call is
        denied if allowed-so-far + this call crosses the limit, and once
        anything is denied the breaker denies everything after it.

        For make_pairs trajectories that's the same as "has the running
        total crossed the budget at or before this call". It differs for
        make_probing_pairs, where small calls after a denial would fit under
        the limit but are still violations.
        """
        return [denied for denied, _, _ in self._replay(budget)]

    def _replay(self, budget=None):
        limit = self.limit if budget is None else budget
        allowed, tripped, out = 0, False, []
        for amount in self.amounts:
            before = (tripped, allowed)
            if tripped or allowed + amount > limit:
                tripped = True
            else:
                allowed += amount
            out.append((tripped, *before))
        return out

    def features(self):
        """One row per call. `amount` is all a per-call check gets to see.
        Everything else is history - the kind of thing only something
        keeping state can supply.

        - `running_total_before`: sum of every earlier *attempt*. Used by
          per_call_baseline.py.
        - `allowed_before` / `denied_before`: what accrue's ledger actually
          holds - ALLOW-only spend so far, and whether the breaker has
          tripped. Used by the stress suite.
        """
        rows, before = [], 0
        for i, (amount, (_, tripped, allowed)) in enumerate(zip(self.amounts, self._replay())):
            rows.append({
                "amount": amount,
                "position": i,
                "running_total_before": before,
                "allowed_before": allowed,
                "denied_before": float(tripped),
            })
            before += amount
        return rows


def make_pairs(n_pairs, seed=0, budget=BUDGET, min_amount=MIN_AMOUNT,
               max_amount=MAX_AMOUNT):
    """Return 2 * n_pairs trajectories: a compliant and a violating one per
    pair, in that order.

    The compliant one keeps drawing amounts until the next draw would cross
    the budget. That crossing draw is where the violating one diverges - it
    takes it, then 0 to MAX_EXTRA_CALLS more ordinary calls.

    `min_amount` / `max_amount` / `budget` are what the stress suite varies
    to test models off the distribution they were trained on. The defaults
    reproduce the original benchmark exactly.
    """
    rng = random.Random(seed)
    trajectories = []

    for pair_id in range(n_pairs):
        prefix, total = [], 0
        while True:
            amount = rng.randint(min_amount, max_amount)
            if total + amount > budget:
                break
            prefix.append(amount)
            total += amount

        extra = [rng.randint(min_amount, max_amount)
                 for _ in range(rng.randint(0, MAX_EXTRA_CALLS))]
        tail = [amount] + extra

        trajectories.append(Trajectory(pair_id, False, tuple(prefix), budget))
        trajectories.append(Trajectory(pair_id, True, tuple(prefix + tail), budget))

    return trajectories


def make_probing_pairs(n_pairs, seed=0, budget=BUDGET):
    """Retry-after-deny: the pattern src/breaker.py exists for.

    The violating session asks for a transfer that crosses the budget, gets
    denied, then *searches* for amounts that slip under the remaining
    headroom. Every one of those probes fits - a running-total check alone
    would allow them. Only "this scope was already denied" catches them.

    The compliant twin makes the same probe calls without the denied
    attempt in front of them, so the probes themselves look ordinary: the
    only difference between the two sessions is one denied call.
    """
    rng = random.Random(seed)
    trajectories = []

    pair_id = 0
    while pair_id < n_pairs:
        prefix, total = [], 0
        while True:
            amount = rng.randint(MIN_AMOUNT, MAX_AMOUNT)
            if total + amount > budget:
                break
            prefix.append(amount)
            total += amount

        headroom = budget - total
        n_probes = rng.randint(2, 4)
        if headroom < n_probes:
            continue  # no room to probe; draw a different pair

        # Split some of the headroom into n_probes positive pieces, largest
        # first - "try less, try less again".
        spend = rng.randint(n_probes, headroom)
        cuts = sorted(rng.sample(range(1, spend), n_probes - 1)) if n_probes > 1 else []
        probes = sorted((b - a for a, b in zip([0] + cuts, cuts + [spend])), reverse=True)

        trajectories.append(Trajectory(pair_id, False, tuple(prefix + probes), budget))
        trajectories.append(Trajectory(pair_id, True, tuple(prefix + [amount] + probes), budget))
        pair_id += 1

    return trajectories


# Volume: a cap on records read per session instead of dollars spent. Many
# small calls - "800 one-record reads" - so sessions run to a couple of
# hundred calls, which is what stresses a sequence model.
READ_LIMIT = 500
MIN_RECORDS = 1
MAX_RECORDS = 5


def make_volume_pairs(n_pairs, seed=0):
    """Same construction as make_pairs, with records per read in place of
    dollars per transfer. accrue doesn't enforce a volume policy yet - the
    ledger has the count (aggregates.action_count) but no Cedar rule reads
    it - so the stress suite reports accrue as n/a here.
    """
    return make_pairs(n_pairs, seed=seed, budget=READ_LIMIT,
                      min_amount=MIN_RECORDS, max_amount=MAX_RECORDS)
