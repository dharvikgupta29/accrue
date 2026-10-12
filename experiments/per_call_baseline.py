"""Per-call baseline: can a model that sees one call at a time catch what
accrue catches?

    python3 -m experiments.per_call_baseline

Five arms, same test trajectories:

1. stateless Cedar - budget.cedar, but handed only this call's amount.
   What a per-call policy engine does today.
2. NN, per-call features - a small neural net that only sees `amount`.
   The "just use a model" answer.
3. NN, + call position - same net, also told which call this is in the
   session. A crude hint of history.
4. NN, + running total - same net, also given the running total before
   this call. The number a ledger supplies.
5. accrue - the real ledger + Cedar path, src.authorize.authorize_transfer.

Arms 2-4 use the same model, so any difference between them comes from what
it's allowed to see, not how clever it is.

Needs scikit-learn (experiments/requirements.txt) and the `cedar` CLI.
src/ itself stays dependency-free.
"""

import argparse
import shutil
import sys
from functools import lru_cache

import numpy as np
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src import authorize, ledger

from . import benchmark

NN_ARMS = {
    "NN, per-call features": ["amount"],
    "NN, + call position": ["amount", "position"],
    "NN, + running total (ledger state)": ["amount", "running_total_before"],
}


def to_xy(trajectories, columns):
    X, y = [], []
    for t in trajectories:
        for row, label in zip(t.features(), t.labels()):
            X.append([row[c] for c in columns])
            y.append(label)
    return np.array(X, dtype=float), np.array(y, dtype=bool)


def train(trajectories, columns, seed):
    X, y = to_xy(trajectories, columns)
    model = make_pipeline(
        StandardScaler(),
        MLPClassifier(hidden_layer_sizes=(32, 32), max_iter=2000, random_state=seed),
    )
    model.fit(X, y)
    return model


def nn_scores(model, trajectories, columns):
    """Per-trajectory lists of P(violation) for each call."""
    out = []
    for t in trajectories:
        X, _ = to_xy([t], columns)
        out.append(model.predict_proba(X)[:, 1].tolist())
    return out


@lru_cache(maxsize=None)
def _stateless_cedar_denies(amount):
    # Same policy file, but context only ever holds this one call's amount -
    # no history. Cached because the answer depends on nothing else.
    return authorize._ask_cedar(
        'Session::"bench"', 'Action::"transfer"', 'Account::"bench"',
        {"cumulative_cost": amount},
    ) == "DENY"


def stateless_cedar(trajectories):
    return [[_stateless_cedar_denies(a) for a in t.amounts] for t in trajectories]


def accrue(trajectories):
    """Each trajectory gets its own session (and so its own scope) in one
    in-memory ledger. Exactly the production path - nothing stubbed.
    """
    conn = ledger.connect(":memory:")
    flagged = []
    for i, t in enumerate(trajectories):
        session = f"bench-{i}"
        flagged.append([
            authorize.authorize_transfer(conn, session, "acct-bench", a) == "DENY"
            for a in t.amounts
        ])
    conn.close()
    return flagged


def score(trajectories, flagged, scores=None):
    """flagged: per-trajectory lists of bool, one per call.
    scores: optional per-trajectory lists of P(violation), for AUC.
    """
    y_call = [l for t in trajectories for l in t.labels()]
    p_call = [f for fs in flagged for f in fs]

    tp = sum(1 for y, p in zip(y_call, p_call) if y and p)
    fp = sum(1 for y, p in zip(y_call, p_call) if not y and p)
    fn = sum(1 for y, p in zip(y_call, p_call) if y and not p)

    violating = [any(fs) for t, fs in zip(trajectories, flagged) if t.violating]
    compliant = [any(fs) for t, fs in zip(trajectories, flagged) if not t.violating]

    result = {
        "call_recall": tp / (tp + fn) if tp + fn else None,
        "call_precision": tp / (tp + fp) if tp + fp else None,
        "detected": sum(violating) / len(violating),
        "false_flagged": sum(compliant) / len(compliant),
        "auc": None,
        "ff_at_90": None,
    }

    if scores is not None:
        # A trajectory is as suspicious as its most suspicious call -
        # that's how a per-call guard would block it.
        y_traj = [t.violating for t in trajectories]
        s_traj = [max(s) for s in scores]
        result["auc"] = roc_auc_score(y_traj, s_traj)

        # The honest cost question: tune the threshold until 90% of
        # violating sessions get caught - what fraction of legitimate
        # sessions get blocked along the way?
        fpr, tpr, _ = roc_curve(y_traj, s_traj)
        result["ff_at_90"] = float(fpr[np.argmax(tpr >= 0.9)])

    return result


def fmt(value, pct=False):
    if value is None:
        return "—"
    return f"{value:.0%}" if pct else f"{value:.2f}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--train-pairs", type=int, default=600)
    parser.add_argument("--test-pairs", type=int, default=200)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    if shutil.which("cedar") is None:
        sys.exit("needs the cedar CLI on PATH: "
                 "cargo install cedar-policy-cli --version 4.12.0")

    # Different seeds so train and test trajectories never overlap.
    train_set = benchmark.make_pairs(args.train_pairs, seed=args.seed)
    test_set = benchmark.make_pairs(args.test_pairs, seed=args.seed + 1)

    rows = [("stateless Cedar (what ships today)", score(test_set, stateless_cedar(test_set)))]

    for name, columns in NN_ARMS.items():
        model = train(train_set, columns, args.seed)
        s = nn_scores(model, test_set, columns)
        flagged = [[p >= 0.5 for p in ps] for ps in s]
        rows.append((name, score(test_set, flagged, s)))

    rows.append(("accrue: ledger + Cedar", score(test_set, accrue(test_set))))

    n_calls = sum(len(t.amounts) for t in test_set)
    print(f"\n{args.test_pairs} test pairs ({2 * args.test_pairs} trajectories, "
          f"{n_calls} calls), budget ${benchmark.BUDGET:,}, seed {args.seed}\n")
    print(f"{'arm':38} {'call recall':>11} {'call prec':>10} "
          f"{'detected':>9} {'false-flag':>11} {'AUC':>6} {'FF@90%':>7}")
    print("-" * 98)
    for name, r in rows:
        print(f"{name:38} {fmt(r['call_recall']):>11} {fmt(r['call_precision']):>10} "
              f"{fmt(r['detected'], pct=True):>9} {fmt(r['false_flagged'], pct=True):>11} "
              f"{fmt(r['auc']):>6} {fmt(r['ff_at_90'], pct=True):>7}")
    print()
    print("detected / false-flag: share of violating / compliant sessions with at")
    print("  least one call blocked, at the model's default 0.5 threshold.")
    print("FF@90%: false-flag rate after tuning the threshold to catch 90% of")
    print("  violating sessions - the price of making a per-call model useful.")
    print()


if __name__ == "__main__":
    main()
