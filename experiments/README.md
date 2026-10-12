# Per-call baseline

**Question:** could you skip the ledger and just train a model to spot bad
transfers, one call at a time?

**Answer:** no — and not because the model is weak. Give the same neural net
one extra number, the running total, and it goes from catching nothing to
catching everything. The thing missing was never intelligence. It was state.

## Run it

```bash
pip install -r experiments/requirements.txt   # scikit-learn, numpy
python3 -m experiments.per_call_baseline       # needs the cedar CLI on PATH
```

About 15 seconds. `--seed`, `--train-pairs`, `--test-pairs` are adjustable.

## The benchmark

`benchmark.py` builds pairs of transfer sessions. In each pair:

- both sessions are made of calls drawn from the same range ($500–$4,000)
- **every call is individually under the $10,000 budget**
- the violating session is the compliant one plus 1–3 more ordinary calls,
  which push the running total over

So nothing about any single call says "this is the bad one". The only
difference between the two sessions is the total.

It's synthetic on purpose. This is a controlled experiment, not a demo of real
usage — the construction is what lets you tell a *structural* blind spot apart
from a model that just needs more data. `tests/test_benchmark.py` checks the
construction holds, and that the budget matches `src/policies/budget.cedar`.

## Results

200 test pairs, model trained on 600 different pairs. Seed 0:

```
arm                                    call recall  call prec  detected  false-flag    AUC  FF@90%
--------------------------------------------------------------------------------------------------
stateless Cedar (what ships today)            0.00          —        0%          0%      —       —
NN, per-call features                         0.00          —        0%          0%   0.65     74%
NN, + call position                           0.68       0.83       78%         12%   0.92     26%
NN, + running total (ledger state)            0.99       1.00      100%          0%   1.00      0%
accrue: ledger + Cedar                        1.00       1.00      100%          0%      —       —
```

Seeds 1 and 2 give the same picture: per-call AUC 0.64 both times, per-call
FF@90% 74–76%, position-aware detection 79–86%.

- **detected / false-flag** — share of violating / compliant sessions with at
  least one call blocked, at the model's default threshold
- **AUC** — how well the model ranks violating sessions above compliant ones
  across all thresholds (0.5 = coin flip, 1.0 = perfect)
- **FF@90%** — tune the threshold until 90% of violating sessions are caught;
  this is the share of *legitimate* sessions you'd block to get there

## Reading it

**Stateless Cedar catches nothing.** That's the gap this whole repo exists for:
every call is legal on its own, so a per-call rule has nothing to say.

**A neural net with per-call features also catches nothing** at its default
threshold. Forced to catch 90%, it blocks about three quarters of legitimate
sessions too. Unusable.

**Its AUC is 0.65, not 0.5 — and that's worth being honest about.** It isn't
seeing the total. Two side effects of how the benchmark works give it a weak
signal:

1. the call that crosses the budget tends to be larger, since a larger call is
   more likely to be the one that tips it over
2. violating sessions have more calls, and the biggest of more draws tends to
   be bigger

Both are correlations with *size*, not knowledge of the total — which is why
there's no threshold where it's any good.

**Telling it the call's position helps, a lot but not enough.** Later calls are
more likely to be over budget, so a crude hint of history gets it to ~80%
detection — at the cost of blocking 12–18% of legitimate sessions.

**Telling it the running total solves it.** Same network, same training, one
extra feature: perfect. That's the result. The model was never the bottleneck;
the missing input was. And once you have the running total, you don't need a
model at all — `total + amount > budget` is a comparison. That's what accrue
does, deterministically, and it matches the ground truth exactly
(`test_accrue_matches_the_labels_exactly`).

## Limits

- **One policy.** Cumulative spend only — the one rule accrue currently
  enforces through Cedar. Distinct-resource caps and ordering rules aren't
  tested here.
- **Synthetic data, by design.** See above. A real-traffic version needs real
  agent sessions, which this repo doesn't ingest yet.
- **One small model.** A 2×32 MLP. A bigger model doesn't change the argument:
  with only per-call features, the two members of a pair are drawn from the
  same distribution, so there's nothing for any per-call model to learn from.
- **The ground truth matches accrue's own semantics** (deny the crossing call
  and, via the circuit breaker, everything after). A different definition of
  "violating" would need different labels.
