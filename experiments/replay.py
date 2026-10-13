"""Replay real agent sessions through accrue.

    python3 -m experiments.replay                     # reads ~/.codex/sessions
    python3 -m experiments.replay --sessions DIR --budget 10000000 --budget 50000000

Everything else in experiments/ runs on synthetic sessions. This runs on
real ones: Codex CLI rollout logs. They don't record dollars, but every turn
records the tokens it used, so a turn is one "call" and its token count is
its cost. The question is the paper's question, on real traces: does a
session's total blow past a budget while no single turn looks alarming?

For each budget, every session goes through:

- per-call Cedar: budget.cedar (cap swapped to the budget) asked about each
  turn on its own - the check a stateless policy engine can do
- accrue: the real authorize_transfer path, ledger and breaker included

Only numbers are read - turn token counts, tool-call counts, the model
name. No message text, tool arguments or outputs leave the log files, and
sessions are reported by index, not id.

Caveat: total_tokens includes cached input tokens (context re-read on every
turn), which providers bill far below fresh tokens. Token totals here are a
usage budget, not a dollar figure.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

from src import authorize, ledger

from .policy import budget_policy

DEFAULT_SESSIONS = Path.home() / ".codex" / "sessions"
DEFAULT_BUDGETS = [10_000_000, 50_000_000, 100_000_000]
TOOL_CALLS = {"function_call", "custom_tool_call", "local_shell_call", "mcp_tool_call"}


def read_session(path):
    """Per-turn token counts, tool-call count and model for one rollout log.
    Reads numbers and the model name only."""
    turns, tool_calls, model = [], 0, None
    for line in path.open():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        payload = event.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if event.get("type") == "turn_context":
            model = payload.get("model") or model
        elif event.get("type") == "token_usage_record":
            usage = payload.get("turn_token_usage") or {}
            turns.append(int(usage.get("total_tokens") or 0))
        elif payload.get("type") in TOOL_CALLS:
            tool_calls += 1
    return {"turns": turns, "tool_calls": tool_calls, "model": model}


def per_call_cedar(turns):
    """Index of the first turn per-call Cedar denies on its own, or None."""
    for i, tokens in enumerate(turns):
        if authorize._ask_cedar('Session::"replay"', 'Action::"transfer"',
                                'Account::"tokens"', {"cumulative_cost": tokens}) == "DENY":
            return i
    return None


def through_accrue(turns, session):
    """Index of the first turn accrue denies, and tokens it let through."""
    conn = ledger.connect(":memory:")
    first_deny, allowed = None, 0
    for i, tokens in enumerate(turns):
        if authorize.authorize_transfer(conn, session, "tokens", tokens) == "DENY":
            first_deny = i if first_deny is None else first_deny
        else:
            allowed += tokens
    conn.close()
    return first_deny, allowed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessions", type=Path, default=DEFAULT_SESSIONS)
    parser.add_argument("--budget", type=int, action="append",
                        help="token budget per session; repeatable")
    parser.add_argument("--out", type=Path, help="write the numbers here as JSON")
    args = parser.parse_args(argv)

    if shutil.which("cedar") is None:
        sys.exit("needs the cedar CLI on PATH")
    files = sorted(args.sessions.rglob("*.jsonl"))
    sessions = [s for s in (read_session(f) for f in files) if s["turns"]]
    if not sessions:
        sys.exit(f"no sessions with token usage under {args.sessions}")
    sessions.sort(key=lambda s: -sum(s["turns"]))
    budgets = args.budget or DEFAULT_BUDGETS

    print(f"\n{len(sessions)} sessions with token usage "
          f"({sum(len(s['turns']) for s in sessions)} turns, "
          f"{sum(s['tool_calls'] for s in sessions)} tool calls)\n")
    print(f"{'#':>3} {'turns':>5} {'largest turn':>13} {'session total':>14} {'total/largest':>14}")
    for i, s in enumerate(sessions, 1):
        t = s["turns"]
        print(f"{i:>3} {len(t):>5} {max(t):>13,} {sum(t):>14,} {sum(t) / max(t):>13.1f}x")

    results = {"sessions": [{"turns": s["turns"], "tool_calls": s["tool_calls"],
                             "model": s["model"]} for s in sessions],
               "budgets": {}}
    for budget in budgets:
        rows = []
        with budget_policy(budget):
            for i, s in enumerate(sessions, 1):
                t = s["turns"]
                if sum(t) <= budget:
                    continue
                cedar_at = per_call_cedar(t)
                accrue_at, allowed = through_accrue(t, f"replay-{i}")
                rows.append({"session": i, "total": sum(t), "per_call_denies_at": cedar_at,
                             "accrue_denies_at": accrue_at, "accrue_allowed": allowed})
        results["budgets"][str(budget)] = rows

        print(f"\nbudget {budget:,} tokens per session: {len(rows)} session(s) go over\n")
        if rows:
            print(f"{'#':>3} {'total':>14} {'per-call Cedar':>18} {'accrue stops at':>16} "
                  f"{'accrue let through':>19}")
            for r in rows:
                cedar = "never" if r["per_call_denies_at"] is None else f"turn {r['per_call_denies_at'] + 1}"
                print(f"{r['session']:>3} {r['total']:>14,} {cedar:>18} "
                      f"{'turn ' + str(r['accrue_denies_at'] + 1):>16} {r['accrue_allowed']:>19,}")

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))
        print(f"\nsaved {args.out}")
    print()


if __name__ == "__main__":
    main()
