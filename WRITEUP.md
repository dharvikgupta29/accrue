# accrue: stateful authorization for AI agents

*Writeup, due 2026-11-01. Status: outline with evidence; prose to be written.*

Each section below says what it has to establish, lists the evidence with
the exact numbers and where they come from, and ends with a **Write:**
prompt. Delete the scaffolding as each section gets real prose. Numbers
link to the experiment that produced them, so any of them can be re-run.

> Links into `experiments/README.md` and `experiments/figures/` resolve
> once #11 (stress suite) and #13 (Codex replay) are merged.

---

## Abstract

*~150 words. Write last.*

The claims, in order:

1. Policy engines like Cedar judge each request alone, so a limit on a
   total can be beaten with requests that are each within it.
2. accrue adds the missing state, an append-only ledger and a circuit
   breaker, without changing Cedar: it hands Cedar the running total.
3. A learned guard can't stand in for that state. A per-call model is
   blind by construction. A model that learns its own running total works
   only on sessions like its training data, and fails open when the
   policy changes.
4. The pattern happens in real agent use: one real Codex session used
   501.8M tokens with no single turn above 8.6M.

**Write:** one sentence per claim, plus one on what accrue costs (~4 ms
per check).

---

## 1. Introduction

*~500 words.*

Has to establish: agents now take actions with real cost (payments, paid
APIs, tokens), the standard safety control is a policy engine checking each
action, and that leaves a gap.

Evidence:
- The 50 × $9,999 example from [README.md](README.md): every decision
  correct, total $499,950 against a $10,000 rule.
- Real-world pressure: Ramp reports its customers' monthly AI token spend
  grew 13× since January 2025
  ([Ramp](https://ramp.com/blog/ai-agent-spending-controls)).
- AWS ships per-call Cedar for agents (AgentCore Policy, previewed
  December 2025), so this is the mainstream design, not a straw man.

Contributions (end the intro with these as a list):
1. accrue: a stateful layer beside an unmodified stateless policy engine.
2. A benchmark that isolates the per-call blind spot by construction.
3. A stress test of learned guards: sequence models, distribution shift,
   an adaptive attacker, three seeds.
4. A replay of real agent sessions showing the pattern occurs.

**Write:** open with the 50 × $9,999 example, not with definitions.

---

## 2. Background: why Cedar is stateless

*~400 words.*

Has to establish: statelessness is a deliberate design choice (analyzable,
terminating, fast), not an oversight, so the fix belongs beside Cedar,
not inside it.

Evidence and sources:
- [PRIMER.md](PRIMER.md) §1.3–1.5: stateless vs stateful, why it's a
  problem, why Cedar can't just add loops.
- [CEDAR.md](CEDAR.md) Parts 2–3: context vs entities; how Cedar decides
  (forbid overrides permit).
- Cedar's fail-open edge: a policy that errors during evaluation is
  dropped, not fatal. accrue guards it with `cedar validate` in CI and a
  strict decision check in `src/authorize.py` (PR #3).

**Write:** explain "context" carefully. accrue's whole trick is putting
the running total there.

---

## 3. Design

*~800 words. One diagram: request → ledger total → Cedar → logged decision.*

Has to establish: what accrue does on each request, and why each piece
is there.

| piece | what it closes | where |
|---|---|---|
| append-only ledger | the missing history | `src/ledger.py` (update/delete triggers abort) |
| ALLOW-only totals | denied attempts shouldn't count | `src/aggregates.py` |
| one transaction (BEGIN IMMEDIATE) | two concurrent requests both reading a stale total | `src/authorize.py` |
| circuit breaker | probing for an amount that fits after a deny | `src/breaker.py` |
| scopes, enforced at the home | spreading spend across sessions or made-up task names | `src/aggregates.py`, PR #8 |
| input validation | negative amounts buying headroom; quotes in ids | PRs #7, #12 |
| round the total up | $10,000.40 rounding down to pass `> 10000` | PR #9 |

Cedar never changes: `budget.cedar` still only sees one number,
`context.cumulative_cost`.

**Write:** walk one request through end to end, then the table, then the
breaker as a separate subsection (it's the non-obvious one). Be explicit
about the trust assumption: the integrator, not the agent, picks the
scope's home.

---

## 4. Evaluation

*~1,500 words, the core. Four subsections, one figure each.*

### 4.1 The per-call blind spot (synthetic)

Source: [experiments/README.md](experiments/README.md), "Per-call baseline".

- Benchmark: pairs of sessions where every call is under the $10,000
  budget; only the running total separates them.
- Plain Cedar catches 0%. A neural net on per-call features catches 0% at
  its default threshold, and has to block 74% of clean sessions to catch
  90%.
- The same net given the running total: 100% caught, 0% blocked. The
  model was never the bottleneck; the missing input was.

### 4.2 Learned guards under stress

Source: [experiments/README.md](experiments/README.md), "Stress suite";
figures `classes.png`, `shift.png`, `length.png`.

- **Each violation needs its own state.** Probing after a deny: a model
  that sees the running total blocks 31–36% of clean sessions; adding
  "was anything denied?" makes it perfect.
- **A GRU learns the running total within its training range.** 0.99 at
  ~5 calls per session; from ~27 calls on it blocks every session, in
  every seed.
- **Policy change.** After a budget cut nobody retrained for, every
  learned guard catches 17–31% of violating sessions. accrue: 100%.
- **Seed honesty.** Seed 0 flattered the learned guards wherever inputs
  left the training range (e.g. running-total MLP at ~45 calls: 0.94,
  0.57, 0.65 across seeds). Report ranges.

### 4.3 Adaptive attacker

Source: "Stress suite" §4; figure `attack.png`.

- Free queries, any amount $1–$9,999, up to 200 calls, best of greedy and
  fixed-size splitting.
- Amount-only model: $1,999,800 extracted. Position model: $31,142.
  State-aware models: ~$10,000, and the full-ledger-state model leaks a
  little ($10,094).
- After the $5k budget cut the same guards still allow $10,000 (2×),
  until retrained. accrue: $5,000, from one edited number.

### 4.4 Real sessions

Source: [experiments/README.md](experiments/README.md), "Real sessions:
Codex replay"; `experiments/results/replay.json`.

- 12 real Codex sessions. Session 1: 167 turns, 501.8M tokens, largest
  turn 8.6M (58.6× ratio). Sessions with more than a handful of turns
  total 4–59× their largest turn.
- 18 over-budget cases across six budgets: per-call Cedar catches 4, each
  9–65 turns late; accrue catches all 18 at the crossing turn.
- Caveats to state plainly: one user, tokens not dollars (cached tokens
  dominate), budgets illustrative.

**Write:** lead 4.2 with the GRU, because it's the reviewer's obvious
question ("why not a model with memory?"). Put the seed ranges in the
text, not a footnote.

---

## 5. Related work

*~500 words.*

- **Stateless policy engines:** Cedar, OPA/Rego. Per-request by design.
- **Per-call policy for agents:** AWS Bedrock AgentCore Policy, Cedar at a
  gateway in front of tool calls
  ([announcement](https://aws.amazon.com/about-aws/whats-new/2025/12/amazon-bedrock-agentcore-policy-evaluations-preview),
  [docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.md)).
  Its documented example checks one call's amount. **Check before
  submitting** whether it can track totals across calls, and say exactly
  what it does.
- **Agent budget products:** PolicyLayer (cumulative per-agent budgets),
  SatGate (budgets on delegation tokens), and spend controls from payment
  providers. Position accrue as the open, policy-engine-native version,
  plus the evaluation none of them publish.
- **Rate limiting vs. budgets:** a rate limit counts requests per window;
  a budget weighs what each request costs.
- **Learned guardrails:** classifier-based guards judge each input; §4
  shows why that's insufficient for cumulative limits.

**Write:** a paragraph per bullet, ending each with how accrue differs.
Verify every vendor claim against its own docs; several sources found so
far are marketing pages.

---

## 6. Limitations

*~300 words.*

- Only spend is enforced. No volume cap (the ledger has the count, no
  Cedar rule reads it), no per-task cap.
- Synthetic benchmark by design; real data is 12 sessions from one user.
- ~4 ms per check, almost all of it the Cedar subprocess.
- The scope trust assumption (§3).
- Small models: a 2×32 MLP and a 64-unit GRU. A bigger sequence model
  would hold to longer sessions, but would still have lengths it never saw
  and still not know about a budget change.

---

## 7. Future work and conclusion

*~300 words.*

- Volume and per-task caps.
- In-process Cedar instead of the CLI, then report checks per second.
- A drop-in proxy (e.g. an MCP server) in front of agent tools.
- A hybrid: the ledger enforces hard limits, a model flags anomalies for
  a human.
- More real traces, from more users and tools.

**Write:** end on the one-line version: *the limit was always about the
total, so something has to remember the total.*

---

## Schedule to Nov 1

| dates | do |
|---|---|
| Oct 14–17 | §4 Evaluation; the numbers are done, this is the core |
| Oct 18–20 | §3 Design, with the diagram |
| Oct 21–23 | §1 Introduction and §2 Background |
| Oct 24–26 | §5 Related work (verify every vendor claim) and §6 |
| Oct 27–29 | §7, abstract, full read-through |
| Oct 30–31 | buffer; final re-run of every experiment against `main` |
