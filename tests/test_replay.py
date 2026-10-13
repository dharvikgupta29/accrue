"""experiments.replay reads real Codex logs. These use a fabricated log in
the same shape, so they need no real data, and check that only numbers are
taken from it."""

import json
import shutil

import pytest

from experiments import replay


def write_log(path, turn_tokens, secret="do-not-read"):
    events = [{"type": "turn_context", "payload": {"model": "test-model", "cwd": secret}}]
    for tokens in turn_tokens:
        events.append({"type": "response_item", "payload": {
            "type": "function_call", "name": "exec", "arguments": secret}})
        events.append({"type": "response_item", "payload": {
            "type": "message", "content": [{"text": secret}]}})
        events.append({"type": "token_usage_record", "payload": {
            "turn_token_usage": {"total_tokens": tokens, "input_tokens": tokens}}})
    path.write_text("\n".join(json.dumps(e) for e in events) + "\nnot json\n")


def test_read_session_takes_numbers_and_the_model_only(tmp_path):
    log = tmp_path / "rollout.jsonl"
    write_log(log, [100, 250, 50])
    session = replay.read_session(log)
    assert session == {"turns": [100, 250, 50], "tool_calls": 3, "model": "test-model"}
    assert "do-not-read" not in json.dumps(session)


@pytest.mark.skipif(shutil.which("cedar") is None, reason="needs the cedar CLI")
def test_cumulative_overrun_with_no_large_turn(tmp_path, capsys):
    # Ten 400-token turns against a 1,000-token budget: no turn is anywhere
    # near the budget on its own, so per-call Cedar never fires; accrue stops
    # the third turn, the one that would cross.
    write_log(tmp_path / "rollout.jsonl", [400] * 10)
    out = tmp_path / "result.json"
    replay.main(["--sessions", str(tmp_path), "--budget", "1000", "--out", str(out)])
    [row] = json.loads(out.read_text())["budgets"]["1000"]
    assert row["per_call_denies_at"] is None
    assert row["accrue_denies_at"] == 2
    assert row["accrue_allowed"] == 800
    assert "do-not-read" not in out.read_text() + capsys.readouterr().out
