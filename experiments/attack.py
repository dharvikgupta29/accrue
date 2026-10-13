"""An adaptive attacker: how many dollars can you get past each guard?

The benchmark asks whether a guard catches violations drawn from a fixed
distribution. An attacker doesn't draw from your distribution - it picks
amounts that get through. So this module gives the attacker the strongest
reasonable position and measures the damage:

- it can query the guard for free, as often as it likes, without the
  queries counting (a stolen model, or a scoring endpoint with no memory)
- it may request any whole-dollar amount from $1 to $9,999, so every single
  call is individually legal under budget.cedar
- it only sends calls the guard would let through, up to MAX_CALLS of them

It tries several strategies and keeps whichever extracts the most:

- greedy: each step, the largest amount the guard lets through
- split-$N: the same amount N, over and over, for a range of N - the
  "many small transfers" pattern that sessions in training never showed

A learned guard blocks a call when P(violation) >= 0.5. accrue is attacked
the same way, with Cedar as the oracle (asked without logging, so probing
it is free too). Every winning accrue sequence is then replayed through the
real authorize_transfer path to check production agrees with the oracle.

Result: dollars extracted in one session, against a $10,000 budget.
"""

from src import authorize, ledger

from .benchmark import BUDGET

MAX_CALLS = 200
PER_CALL_MAX = 9_999
SPLIT_AMOUNTS = [50, 100, 199, 500, 1_000, 2_500, 5_000, 9_999]


def candidate_amounts():
    """Roughly log-spaced from $1 to $9,999, dense enough that the greedy
    choice lands near each guard's real boundary."""
    out, a = set(), 1.0
    while a < PER_CALL_MAX:
        out.add(int(a))
        a *= 1.15
    out.add(PER_CALL_MAX)
    return sorted(out, reverse=True)


def _greedy(passes):
    history, candidates = [], candidate_amounts()
    for _ in range(MAX_CALLS):
        ok = passes(history, candidates)
        passing = [c for c, p in zip(candidates, ok) if p]
        if not passing:
            break
        history.append(passing[0])  # candidates are sorted largest first
    return history


def _split(passes, amount):
    history = []
    for _ in range(MAX_CALLS):
        if not passes(history, [amount])[0]:
            break
        history.append(amount)
    return history


def _best(passes):
    """Run every strategy; return (name, history) of the one that extracts
    the most."""
    runs = [("greedy", _greedy(passes))]
    runs += [(f"split-${a:,}", _split(passes, a)) for a in SPLIT_AMOUNTS]
    return max(runs, key=lambda r: sum(r[1]))


def attack_model(guard, threshold=0.5):
    def passes(history, candidates):
        return list(guard.score_next(history, candidates) < threshold)
    return _best(passes)


def attack_accrue(budget=BUDGET):
    """`budget` is only for the report's sake: the cap accrue enforces is
    whatever budget.cedar says (stress.budget_policy swaps it). It's
    checked against the result so a mismatch can't go unnoticed."""
    def passes(history, candidates):
        spent = sum(history)
        return [
            authorize._ask_cedar(
                'Session::"attack"', 'Action::"transfer"', 'Account::"attack"',
                {"cumulative_cost": spent + c},
            ) == "ALLOW"
            for c in candidates
        ]

    strategy, history = _best(passes)

    conn = ledger.connect(":memory:")
    for amount in history:
        decision = authorize.authorize_transfer(conn, "attack", "acct-attack", amount)
        assert decision == "ALLOW", "oracle and production path disagree"
    conn.close()
    assert sum(history) <= budget, "accrue let more through than the budget"
    return strategy, history
