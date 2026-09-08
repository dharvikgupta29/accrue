# accrue

Cedar (Amazon's policy language) is stateless on purpose — it looks at one
request, answers `ALLOW` or `DENY`, and forgets. Which means a rule like
*"never transfer more than $10,000"* can be beaten by never asking for more
than $10,000 at once:

```
transfer $9,999   →   9,999 > 10,000?  no   →   ALLOWED
transfer $9,999   →   9,999 > 10,000?  no   →   ALLOWED
... 50 times ...
                                    total: $499,950
```

Every individual decision was correct. The rule was about one payment; the
damage was about the total. Cedar can't fix this itself — no loops is the
whole point of the language, see [PRIMER.md](PRIMER.md) §1.5 for why.

**accrue is the memory Cedar doesn't have.** An append-only SQLite ledger
records every attempt, `authorize.py` adds up what a session has already
spent before it ever asks Cedar a question, and Cedar just answers the one
question it's good at: *given this total, is the next request over budget?*

Read [PRIMER.md](PRIMER.md) for the full concept walkthrough and
[CEDAR.md](CEDAR.md) for how Cedar itself works, if either is new to you.

## Quickstart

```bash
# Cedar CLI - accrue shells out to it, doesn't link against cedar_policy
cargo install cedar-policy-cli --version 4.12.0

# run the 50x-$9,999 demo from PRIMER.md, this time with memory
python3 -m src.main
```

Expect to see the first transfer ALLOW and the rest DENY, once the running
total crosses the $10,000 budget in `src/policies/budget.cedar`.

## Layout

| | |
|---|---|
| `src/ledger.py` | the append-only event log (SQLite) |
| `src/aggregates.py` | the totals Cedar can't compute itself |
| `src/authorize.py` | reads the total, asks Cedar, logs the attempt — one transaction |
| `src/policies/budget.cedar` | the actual rule, in Cedar |
| `src/policies/budget.cedarschema` | the request shape the policy is checked against |
| `src/main.py` | the demo |
| `tests/` | the safety properties above, as assertions |

## Testing

```bash
pip install pytest
pytest tests/ -v
```

Tests shell out to the real `cedar` binary (see Quickstart) rather than
mocking it — that CLI boundary is where the interesting failure modes live.

CI (`.github/workflows/ci.yml`) runs this suite plus `cedar validate` against
`budget.cedarschema` on every push, so a typo'd context attribute in the
policy fails the build instead of silently changing what gets approved.

## License

[MIT](LICENSE)
