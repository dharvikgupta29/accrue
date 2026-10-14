"""Stress suite: does any learned guard replace the ledger once you stop
being nice to it?

    python3 -m experiments.stress            # ~12 minutes on a laptop; keep it awake
    python3 -m experiments.figures           # redraw charts from the saved results

per_call_baseline.py showed a per-call net is blind and that handing it the
running total fixes it. The obvious comeback is "so use a model that learns
the running total itself." This suite answers that four ways:

1. Violation classes - spend over budget, probing after a deny (the
   breaker's job), and a volume cap on reads. Each needs a different piece
   of state.
2. Distribution shift - train on ordinary sessions, test on many tiny
   calls, a few huge ones, and a budget cut nobody retrained for.
3. Session length - the same shift as a smooth sweep, for a curve.
4. Adaptive attacker - attack.py: free queries, greedy and split-amount
   strategies, how many dollars get through - and what that becomes after
   a budget cut the learned guards were never retrained for.

Results go to experiments/results/stress.json; figures.py draws from that.
"""

import argparse
import json
import shutil
import sys
import time
from contextlib import contextmanager
from functools import partial
from pathlib import Path

from sklearn.metrics import roc_curve

from . import attack, benchmark, models
from .per_call_baseline import accrue, score, stateless_cedar
from .policy import budget_policy

RESULTS = Path(__file__).parent / "results" / "stress.json"

# Every line out immediately, so a long run shows where it is.
print = partial(print, flush=True)


@contextmanager
def stage(name):
    started = time.time()
    print(f"-- {name} ...")
    yield
    print(f"-- {name}: {time.time() - started:.1f}s")

CLASSES = {
    "spend": benchmark.make_pairs,
    "probing": benchmark.make_probing_pairs,
    "volume": benchmark.make_volume_pairs,
}

# Volume sessions run ~170 calls each, so 600 pairs is ~200k training rows.
# Train it on fewer pairs and cap the MLPs to keep the suite laptop-sized.
# Only 0.6% of volume calls are violations, so every arm is trained with
# the rare class weighted up - without it they all learn "never flag".
VOLUME_TRAIN_PAIRS = 150
VOLUME_MLP = dict(max_iter=300, early_stopping=True, balance=True)

# accrue enforces spend (Cedar) and probing (breaker). There's no volume
# policy yet, so it doesn't get credit for one.
ACCRUE_ENFORCES = {"spend", "probing"}

SHIFTS = {
    "in-distribution": dict(),
    "split small ($50-400)": dict(min_amount=50, max_amount=400),
    "few large ($4k-9,999)": dict(min_amount=4_000, max_amount=9_999),
    "budget cut to $5k": dict(budget=5_000),
}

# Divide the ordinary $500-4,000 range by each factor: same budget, more and
# smaller calls. Factor 10 is ~45 calls a session.
LENGTH_FACTORS = [1, 1.5, 2, 3, 4, 6, 8, 10]

ATTACK_CUT_BUDGET = 5_000


def run_guards(trained, test_set):
    rows = {}
    for name, guard in trained.items():
        s = guard.scores(test_set)
        flagged = [[p >= 0.5 for p in ps] for ps in s]
        rows[name] = score(test_set, flagged, s) | {"_scores": s}
    return rows


def balanced(r):
    return (r["detected"] + 1 - r["false_flagged"]) / 2


def roc_points(test_set, s):
    fpr, tpr, _ = roc_curve([t.violating for t in test_set], [max(x) for x in s])
    return {"fpr": fpr.tolist(), "tpr": tpr.tolist()}


def strip(rows):
    return {k: {m: v for m, v in r.items() if not m.startswith("_")} for k, r in rows.items()}


def fmt(v, pct=False):
    if v is None:
        return "—"
    return f"{v:.0%}" if pct else f"{v:.2f}"


def print_table(title, rows):
    print(f"\n{title}\n")
    print(f"{'arm':28} {'call recall':>11} {'detected':>9} {'false-flag':>11} {'AUC':>6} {'FF@90%':>7}")
    print("-" * 76)
    for name, r in rows.items():
        if r is None:
            print(f"{name:28} {'n/a - no policy for this yet':>47}")
            continue
        print(f"{name:28} {fmt(r['call_recall']):>11} {fmt(r['detected'], True):>9} "
              f"{fmt(r['false_flagged'], True):>11} {fmt(r['auc']):>6} {fmt(r['ff_at_90'], True):>7}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--train-pairs", type=int, default=600)
    parser.add_argument("--test-pairs", type=int, default=200)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=RESULTS)
    args = parser.parse_args(argv)

    if shutil.which("cedar") is None:
        sys.exit("needs the cedar CLI on PATH: "
                 "cargo install cedar-policy-cli --version 4.12.0")

    started = time.time()
    out = {"seed": args.seed, "train_pairs": args.train_pairs,
           "test_pairs": args.test_pairs, "budget": benchmark.BUDGET}

    # 1. Violation classes ------------------------------------------------
    out["classes"] = {}
    spend_guards = None
    for cls, make in CLASSES.items():
        volume = cls == "volume"
        train_set = make(VOLUME_TRAIN_PAIRS if volume else args.train_pairs, seed=args.seed)
        test_set = make(args.test_pairs, seed=args.seed + 1)
        with stage(f"{cls}: train"):
            arms = models.guards(args.seed, **(VOLUME_MLP if volume else {}))
            trained = {n: g.fit(train_set) for n, g in arms.items()}
        if cls == "spend":
            spend_guards = trained

        rows = {}
        with stage(f"{cls}: score"):
            rows["stateless Cedar"] = score(test_set, stateless_cedar(test_set)) if cls in ACCRUE_ENFORCES else None
            guard_rows = run_guards(trained, test_set)
            rows.update(guard_rows)
        with stage(f"{cls}: accrue"):
            rows["accrue"] = score(test_set, accrue(test_set)) if cls in ACCRUE_ENFORCES else None

        out["classes"][cls] = {
            "rows": {k: (None if v is None else strip({k: v})[k]) for k, v in rows.items()},
            "roc": {n: roc_points(test_set, r["_scores"]) for n, r in guard_rows.items()},
            "mean_calls": sum(len(t.amounts) for t in test_set) / len(test_set),
        }
        print_table(f"[{cls}] {args.test_pairs} test pairs, "
                    f"~{out['classes'][cls]['mean_calls']:.0f} calls/session", rows)

    # 2. Distribution shift (spend, guards trained in-distribution) -------
    out["shift"] = {}
    for shift, kwargs in SHIFTS.items():
        with stage(f"shift: {shift}"):
            test_set = benchmark.make_pairs(args.test_pairs, seed=args.seed + 1, **kwargs)
            rows = strip(run_guards(spend_guards, test_set))
            with budget_policy(kwargs.get("budget", benchmark.BUDGET)):
                rows["accrue"] = score(test_set, accrue(test_set))
        out["shift"][shift] = rows

    names = list(next(iter(out["shift"].values())))
    print("\n[shift] spend guards trained in-distribution; cells are detected / false-flag\n")
    print(f"{'arm':28}" + "".join(f"{s:>24}" for s in SHIFTS))
    print("-" * (28 + 24 * len(SHIFTS)))
    for n in names:
        cells = [f"{fmt(out['shift'][s][n]['detected'], True)} / "
                 f"{fmt(out['shift'][s][n]['false_flagged'], True)}" for s in SHIFTS]
        print(f"{n:28}" + "".join(f"{c:>24}" for c in cells))

    # 3. Session length sweep ---------------------------------------------
    out["length"] = []
    for f in LENGTH_FACTORS:
        lo, hi = round(benchmark.MIN_AMOUNT / f), round(benchmark.MAX_AMOUNT / f)
        test_set = benchmark.make_pairs(args.test_pairs, seed=args.seed + 1,
                                        min_amount=lo, max_amount=hi)
        with stage(f"length: x{f}"):
            rows = strip(run_guards(spend_guards, test_set))
            rows["accrue"] = score(test_set, accrue(test_set))
        out["length"].append({
            "factor": f, "range": [lo, hi],
            "mean_calls": sum(len(t.amounts) for t in test_set) / len(test_set),
            "balanced": {n: balanced(r) for n, r in rows.items()},
            # Balanced accuracy alone can't tell "blocks everything" from
            # "catches nothing" - both are 0.50. Keep the two halves.
            "detected": {n: r["detected"] for n, r in rows.items()},
            "false_flagged": {n: r["false_flagged"] for n, r in rows.items()},
        })

    print("\n[length] balanced accuracy vs session length; "
          "(detected / false-flag) in brackets\n")
    print(f"{'calls/session':>14}" + "".join(f"{n[:22]:>24}" for n in out["length"][0]["balanced"]))
    for p in out["length"]:
        cells = [f"{p['balanced'][n]:.2f} ({p['detected'][n]:.0%}/{p['false_flagged'][n]:.0%})"
                 for n in p["balanced"]]
        print(f"{p['mean_calls']:>14.1f}" + "".join(f"{c:>24}" for c in cells))

    # 4. Adaptive attacker -------------------------------------------------
    # The learned guards are attacked once. They're never told the budget, so
    # after a cut to $5k they behave exactly as before - the same rows are
    # reported against the new budget rather than recomputed. Only accrue is
    # re-attacked, under the edited policy. The point isn't a new attack;
    # it's what a policy change costs: retraining, vs. one number in
    # budget.cedar.
    guard_rows = {}
    for name, guard in spend_guards.items():
        with stage(f"attack: {name}"):
            strategy, h = attack.attack_model(guard)
        guard_rows[name] = {"extracted": sum(h), "calls": len(h), "strategy": strategy}

    out["attack"] = {}
    for budget in (benchmark.BUDGET, ATTACK_CUT_BUDGET):
        rows = dict(guard_rows)
        with stage(f"attack ${budget:,}: accrue"), budget_policy(budget):
            strategy, h = attack.attack_accrue(budget)
        rows["accrue"] = {"extracted": sum(h), "calls": len(h), "strategy": strategy}
        out["attack"][str(budget)] = rows

        cut = budget != benchmark.BUDGET
        print(f"\n[attack] budget ${budget:,}"
              f"{' - policy changed; learned guards not retrained (same rows as above)' if cut else ''}"
              f"\nbest of greedy and split-amount strategies, free queries, up to "
              f"{attack.MAX_CALLS} calls of $1-${attack.PER_CALL_MAX:,}\n")
        print(f"{'guard':28} {'extracted':>12} {'x budget':>9} {'calls':>6}  strategy")
        print("-" * 72)
        for name, r in rows.items():
            print(f"{name:28} {'$' + format(r['extracted'], ','):>12} "
                  f"{r['extracted'] / budget:>8.2f}x {r['calls']:>6}  {r['strategy']}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1))
    print(f"\nsaved {args.out} ({time.time() - started:.0f}s)\n")


if __name__ == "__main__":
    main()
