# Security

accrue is an authorization layer, so a bug that lets a request through when
it should be denied is a security bug, even if nothing crashes.

## Reporting a vulnerability

Please report privately, not in a public issue: use **Report a
vulnerability** under this repository's **Security** tab
(GitHub private vulnerability reporting). Include the call sequence that
gets through, what you expected, and which commit you tested.

Things we especially want to hear about:

- any sequence of calls that spends more than the budget in
  `src/policies/budget.cedar`
- a way to un-trip the circuit breaker without an override in the ledger
- a request that Cedar evaluates against a different principal, resource or
  context than the caller passed
- a way to edit or delete ledger rows (they're meant to be append-only)

## What's in scope

The code in `src/` and the CI checks that guard it (`cedar validate`, the
test suite). `experiments/` is research code and runs only on synthetic
data; bugs there matter for the results but aren't security issues.

## Known limits

These are documented design assumptions, not vulnerabilities:

- **The integrator, not the agent, chooses the scope's home segment.** An
  agent that can name its own home gets a budget per home (see NOTES.md).
- **No volume or per-task cap yet.** Only cumulative spend is enforced.
