"""experiments.replay reads real Codex logs. These use fabricated logs in the
same shape, so they need no real data, and check that only numbers are
taken from them - and that the per-call cost is the per-response count, not
one of the running totals logged beside it."""

import json
import shutil

import pytest

from experiments import replay


def write_log(path, call_tokens, secret="do-not-read", turn_sizes=None):
    """A rollout log in Codex's shape. Each token_usage_record carries the
    response's own `usage` plus two *running* totals: over the current user
    turn (reset at each new turn) and over the whole thread. `turn_sizes`
    splits the calls into user turns."""
    turn_sizes = turn_sizes or [len(call_tokens)]
    events = [{"type": "turn_context", "payload": {"model": "test-model", "cwd": secret}}]
    thread, i = 0, 0
    for size in turn_sizes:
        turn = 0
        for tokens in call_tokens[i:i + size]:
            turn += tokens
            thread += tokens
            events.append({"type": "response_item", "payload": {
                "type": "function_call", "name": "exec", "arguments": secret}})
            events.append({"type": "response_item", "payload": {
                "type": "message", "content": [{"text": secret}]}})
            events.append({"type": "token_usage_record", "payload": {
                "usage": {"total_tokens": tokens},
                "turn_token_usage": {"total_tokens": turn},
                "thread_token_usage": {"total_tokens": thread}}})
        i += size
    path.write_text("\n".join(json.dumps(e) for e in events) + "\nnot json\n")


def test_read_session_takes_numbers_and_the_model_only(tmp_path):
    log = tmp_path / "rollout.jsonl"
    write_log(log, [100, 250, 50])
    session = replay.read_session(log)
    assert session == {"calls": [100, 250, 50], "tool_calls": 3, "model": "test-model"}
    assert "do-not-read" not in json.dumps(session)


def test_per_call_cost_is_the_response_not_a_running_total(tmp_path):
    # Regression: replay.py used to read turn_token_usage - a running total -
    # and sum it, so 3 calls of 100 came out as 100 + 200 + 300 = 600. The
    # session's real total is 300, across two user turns.
    log = tmp_path / "rollout.jsonl"
    write_log(log, [100, 100, 100], turn_sizes=[2, 1])
    calls = replay.read_session(log)["calls"]
    assert calls == [100, 100, 100]
    assert sum(calls) == 300


def test_a_log_that_does_not_add_up_is_refused(tmp_path):
    # If a future log format makes `usage` cumulative too, the per-response
    # counts stop matching the thread total, and the replay must say so
    # rather than publish inflated numbers.
    log = tmp_path / "rollout.jsonl"
    write_log(log, [100, 100])
    text = log.read_text().replace('"usage": {"total_tokens": 100}', '"usage": {"total_tokens": 150}', 1)
    log.write_text(text)
    with pytest.raises(ValueError, match="running total"):
        replay.read_session(log)


@pytest.mark.skipif(shutil.which("cedar") is None, reason="needs the cedar CLI")
def test_cumulative_overrun_with_no_large_call(tmp_path, capsys):
    # Ten 400-token calls against a 1,000-token budget: no call is anywhere
    # near the budget on its own, so per-call Cedar never fires; accrue stops
    # the third call, the one that would cross.
    write_log(tmp_path / "rollout.jsonl", [400] * 10)
    out = tmp_path / "result.json"
    replay.main(["--sessions", str(tmp_path), "--budget", "1000", "--out", str(out)])
    [row] = json.loads(out.read_text())["budgets"]["1000"]
    assert row["per_call_denies_at"] is None
    assert row["accrue_denies_at"] == 2
    assert row["accrue_allowed"] == 800
    assert "do-not-read" not in out.read_text() + capsys.readouterr().out
