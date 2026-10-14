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

---

# Stress suite

**Question:** fine, a per-call model is blind. What about a model with
*memory*, one that learns the running total itself? And what happens once
you stop being nice to it?

**Answer:** a GRU does learn the running total, for sessions shaped like the
ones it trained on. Make the sessions longer, change the budget, or let an
attacker pick the amounts, and every learned guard breaks somewhere. accrue
doesn't, because it isn't approximating anything: it's an addition and a
comparison.

## Run it

```bash
pip install -r experiments/requirements.txt   # adds torch, matplotlib
python3 -m experiments.stress                  # ~12 min on a laptop; needs cedar
python3 -m experiments.figures                 # redraws figures/ from results/stress.json
```

Keep the machine awake while it runs (on a Mac, prefix the command with
`caffeinate -i`; on Linux, `systemd-inhibit`). If it sleeps mid-run, the
stage timings are meaningless, though the results aren't affected.

## The arms

Six guards against the same test sessions:

| arm | sees |
|---|---|
| stateless Cedar | this call's amount, through the real `budget.cedar` |
| MLP: amount only | this call's amount |
| MLP: + call position | amount, and which call this is |
| MLP: + running total | amount, and ALLOW-only spend so far (what the ledger stores) |
| MLP: + full ledger state | amount, ALLOW-only spend, and whether anything was denied (the breaker) |
| GRU: raw sequence | the amounts in order, nothing else; any total it wants, it has to learn |
| accrue | the real `authorize_transfer` path: ledger, breaker, Cedar |

The arms that read ledger state are **trained** on the true state but
**scored** on their own decisions, call by call: a call the model flags is
denied and stops counting toward its total, as accrue's would. Scoring them on
the true state instead leaks the answer (it tells the model "a call was
already denied" even when it missed that call itself). An earlier draft of
this suite did that, and it inflated one result from 30% to 70%.

## 1. Each violation needs its own piece of state

![](figures/classes.png)

- **spend**: the original benchmark: over budget, every call legal on its own.
- **probing**: the session asks for a transfer that crosses the budget,
  gets denied, then sends 2–4 smaller "probes" that each *fit* under the
  remaining headroom. Its compliant twin makes the same probes without the
  denied call. This is what `src/breaker.py` exists for.
- **volume**: a cap of 500 records read per session, 1–5 records per read,
  ~167 calls a session.

**The running total isn't enough for probing.** The probes fit, so a model
reading only the total blocks 31% of clean sessions trying to catch them.
Add "was anything denied?", the breaker's one bit, and it's perfect.

**The GRU can't count to 500.** Over ~167 calls it falls apart (AUC
0.51–0.64 across seeds). What it does at the default threshold varies by
seed, from blocking every session to catching nothing, but it never works.
The same thing happens in spend once sessions get long (section 3).

**Even full ledger state needs its threshold tuned for volume.** It ranks
perfectly in every seed (AUC 1.00), but at the default 0.5 threshold it
blocks 0%, 24% and 32% of clean sessions in seeds 0, 1 and 2. Weighting
the rare class up moves where 0.5 falls.

**accrue has no volume policy yet**, so it gets no credit there. The ledger
already has the count (`aggregates.action_count`); no Cedar rule reads it.

Volume needed two training adjustments, both applied to every arm: 150
training pairs instead of 600 (each volume session is ~30× longer), and the
rare violating calls (0.6% of all calls) weighted up. Without the weighting,
every arm learns "never flag".

## 2. Off the training distribution, only the ledger holds

![](figures/shift.png)

Spend guards trained on ordinary sessions ($500–4,000 calls, $10,000
budget), then tested without retraining:

- **split small**: $50–400 calls, ~50 per session. The GRU and the position
  model block every clean session. The running-total model blocks 12% in
  seed 0, but 86% and 70% in seeds 1 and 2.
- **few large**: $4,000–9,999 calls, 2–3 per session. The guards that see
  the total miss 0–6% of violating sessions and the position model misses
  44–64%. The GRU is fine in seed 0 but blocks 25–34% of clean sessions in
  seeds 1 and 2.
- **budget cut to $5k**: the policy changes; nobody retrains. Every learned
  guard still enforces the $10,000 line it memorized and catches ~30%: only
  the sessions that happen to cross $10,000 too. For accrue the change is
  one number in `budget.cedar`.

## 3. A GRU learns the running total, for sessions as long as it trained on

![](figures/length.png)

Same $10,000 budget, calls scaled down so sessions get longer. Trained on ~5
calls a session, the GRU holds up to ~10, and from ~27 calls on it **blocks
every session**, in every seed: 100% of violating sessions caught, and 100%
of clean ones blocked too. That 0.50 isn't guessing. The model has
concluded that long sessions are bad, which is safe and useless.

The MLP handed the full ledger state degrades gently (0.95–0.97 at ~45
calls). The one handed only the running total fails the same way as the
GRU, by over-blocking, but how badly depends on the seed: at ~45 calls it
blocks 12% of clean sessions in seed 0 (the run plotted), 86% and 70% in
seeds 1 and 2. Its calls are 10× smaller than anything it trained on, and
where it draws the line out there is luck. accrue doesn't degrade.

`results/*.json` keep caught and blocked rates for every point, not just
the balanced score, since 0.50 alone can't tell "blocks everything" from
"catches nothing".

## 4. Adaptive attacker

![](figures/attack.png)

`attack.py` gives the attacker free queries to the guard and any amount
from $1 to $9,999 (each individually legal), up to 200 calls. It tries a
greedy strategy (the largest amount that gets through) and fixed-size
splitting ($50 up to $9,999 repeated) and keeps whichever extracts the most.

- The amount-only model gives up **$1,999,800** (200 × $9,999) and the
  position model $31,142.
- The running-total MLP and the GRU hold at $10,000. The full-ledger-state
  MLP, the one learned guard that's supposed to be exact, **leaks a little**:
  $10,094 in seed 0 and $10,001 in seed 1, against a $10,000 budget. A
  small overspend, but an overspend; accrue's comparison can't make one.

**The $5,000 panel isn't a second attack.** The learned guards are never
told the budget changed, so they behave exactly as they did at $10,000;
the suite reports the same rows against the new budget rather than
recomputing them. Only accrue is re-attacked, under the edited policy, and
stops at $5,000. What the panel shows is the cost of a policy change: every
learned guard now lets through **2× the budget** until someone retrains it,
while for accrue the change is one number in `budget.cedar`.

Splitting into small amounts never wins against the GRU. Off-distribution
it *over*-blocks rather than under-blocks, so its failure there is blocking
legitimate users, not losing money (section 2). Losing money shows up when
the budget changes.

## Reading it

The original experiment showed a model is only as good as the state it's
given. The stress suite closes the loophole in that argument: a model that
learns its own state does learn it, but only within the range it trained
on. Policies change, sessions get longer, and attackers choose their
amounts. Each of those takes it out of that range, and the first two need no
adversary at all.

The ledger has no range. "Allowed so far + this call > limit" is exact at
any length, any budget, against any sequence of amounts, and changing the
policy means editing one number.

## Limits

- **Synthetic sessions.** Same caveat as above. The charts show seed 0;
  "Across seeds" below gives ranges over seeds 0–2.
- **Small models, CPU training.** A 2×32 MLP and a 64-unit GRU. A larger
  sequence model would hold up to longer sessions; it would still have a
  length it never saw, and it would still not know about a budget change.
- **The attacker only sees one session.** Spreading spend across sessions
  under one scope is what PR #8's home-scope rollup closes for accrue. The
  learned guards here don't model scopes at all.
- **Volume isn't enforced by accrue yet.** The table shows the gap honestly
  rather than filling it.

## Across seeds

Seeds 0, 1 and 2, each a full run with its own training and test sessions
(`results/stress.json`, `stress-seed1.json`, `stress-seed2.json`).

**Stable in every seed:**

| result | seed 0 | seed 1 | seed 2 |
|---|---|---|---|
| accrue, every condition | 100% caught / 0% blocked | same | same |
| budget cut to $5k: share caught by learned guards | 17–31% | 18–26% | 19–28% |
| GRU at ~18 / ~27 calls: clean sessions blocked (all violating caught) | 96% / 100% | 98% / 100% | 79% / 100% |
| probing: clean sessions blocked, running total only | 31% | 36% | 35% |
| attacker vs. amount-only model | $1,999,800 | $1,999,800 | $1,999,800 |
| attacker vs. state-aware guards (same at either budget; see section 4) | ~$10,000 | ~$10,000 | ~$10,000 |
| attacker vs. accrue, $5k budget | $5,000 | $5,000 | $5,000 |

**Seed 0 was the flattering run** for the learned guards wherever their
inputs went outside the training range. That's the expected way for these
to vary: inside the range the models are well determined, and outside it
they extrapolate however training happened to leave them.

| result | seed 0 | seed 1 | seed 2 |
|---|---|---|---|
| running-total MLP at ~45 calls | 0.94 | 0.57 | 0.65 |
| split small: clean blocked, running-total MLP | 12% | 86% | 70% |
| few large: clean blocked, GRU | 0% | 34% | 25% |
| volume: clean blocked, full ledger state (AUC 1.00 in all) | 0% | 24% | 32% |

---

# Real sessions: Codex replay

**Question:** everything above runs on synthetic sessions. Does the pattern
(a total that blows past a budget while no single call looks alarming)
actually happen in real agent use?

**Answer:** yes. In 12 real Codex CLI sessions, the largest single model
call used **214k tokens**, while one session used **18.2M** in total over
167 calls, 85× its largest call. At every budget tried, from 250k to 10M
tokens per session, per-call Cedar catches none of the 21 sessions that go
over; accrue stops every one at the call that crosses.

## Run it

```bash
python3 -m experiments.replay                                   # reads ~/.codex/sessions
python3 -m experiments.replay --budget 1000000 --out results/replay.json
```

Codex logs don't record dollars, but every model response records the
tokens it used, so each response is a call and its token count is its cost.
Each session goes through per-call Cedar (`budget.cedar` with the cap
swapped to the budget, asked about each call alone) and the real
`authorize_transfer` path.

**Which token count matters.** Each `token_usage_record` carries three:
`usage` (that one response) and two *running totals*, over the user turn
and over the whole thread. The first published version of this replay read
the per-turn running total and summed it, counting every response many
times: it reported session 1 at 501.8M tokens instead of 18.2M. sidh828
caught it in review. `read_session` now takes `usage`, and refuses any log
where the per-response counts don't add up to the thread's own final total.
In all 12 sessions they match exactly.

Only numbers leave the logs: per-call token counts, tool-call counts and the
model name. No message text, tool arguments or outputs. Sessions are
numbered by size, not by id. `tests/test_replay.py` checks that with
fabricated logs, including one with running totals.

## The sessions

| # | model calls | largest call | session total | total / largest |
|---|---|---|---|---|
| 1 | 167 | 213,964 | 18,201,050 | 85.1× |
| 2 | 29 | 122,164 | 2,153,443 | 17.6× |
| 3 | 17 | 113,279 | 1,184,685 | 10.5× |
| 4 | 15 | 62,655 | 678,586 | 10.8× |
| 5 | 12 | 54,778 | 552,186 | 10.1× |
| 6 | 18 | 43,892 | 488,982 | 11.1× |
| 7–12 | 1–11 | ≤ 61,095 | ≤ 475,473 | ≤ 9.2× |

Every session with more than a handful of calls totals 8–85× its largest
call. That ratio is the gap a per-call limit can't see.

## Per-call Cedar vs. accrue

At each budget, the sessions whose total goes over it:

| budget (tokens) | sessions over | per-call Cedar catches | accrue stops them at |
|---|---|---|---|
| 250k | 9 | 0 | call 7–12 |
| 500k | 5 | 0 | call 11–13 |
| 1M | 3 | 0 | call 16–20 |
| 2M | 2 | 0 | call 28–36 |
| 5M | 1 | 0 | call 74 of 167 |
| 10M | 1 | 0 | call 120 of 167 |

**Per-call Cedar catches 0 of 21, and that's structural, not bad luck.**
Every budget here is above the largest single call (214k), so no call is
ever over the limit on its own. To make a per-call limit fire at all, you'd
have to set it below 214k tokens, and then it blocks ordinary calls in
legitimate sessions. A cumulative limit doesn't have that trade-off: accrue
lets each session run up to the budget and stops it at the call that
crosses, so session 1 at a 10M budget stops at 9.96M instead of running on
to 18.2M.

## Limits

- **12 sessions, one user, one tool.** Enough to show the pattern is real,
  not how common it is. The replay reads any directory of Codex rollout
  logs, so more data is one flag away.
- **Tokens, not dollars.** `total_tokens` includes cached input tokens,
  context re-read on every call, which providers bill far below fresh
  tokens. These are usage budgets.
- **Budgets are illustrative.** No one set these limits on these sessions;
  the replay asks what each kind of check *would* have done.
