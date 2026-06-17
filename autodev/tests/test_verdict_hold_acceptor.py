"""Verdict-hold acceptor — hold a premature `.done` only while the turn is still
in flight, so a genuinely-ended no-verdict turn is NOT held.

Root cause of the reviewer false-CONTRACT_FAILURE (#3): the reviewer writes
`.done` (or the agent_end backstop does) but keeps streaming and writes its real
`reviewer_output.json` seconds-to-minutes later; the gate read at `.done` saw no
verdict → CONTRACT_FAILURE → a contract-retry that streamed *concurrently* with
the first, and the real PASS was discarded.

The hold signal is the session's **turn-terminality**, NOT activity-stamp timing.
The plugin refreshes the stamp on `after_tool_call`, which fires right after the
agent writes `.done` — so `stamp_mtime > done_mtime` is true even for a genuine
no-verdict end, which would false-hold it until stall/budget. Instead: HOLD only
while the last assistant row is a non-terminal tool-loop step (`stopReason
"toolUse"`); a terminally-ended turn (any other / absent stopReason) accepts
immediately → the gate's CONTRACT_FAILURE path, no stall-window latency.

These tests pin all arms of the predicate.
"""

import json
import os
import time

import orchestrator as orch_mod  # sys.path wired by conftest


def _acceptor(monkeypatch, tmp_path, *, stop_reason, verdict=False, attempt_age=10):
    """Build the acceptor over a controlled artifacts dir + session JSONL.

    stop_reason: the last assistant row's stopReason (None → no assistant row).
    verdict: write a fresh parseable reviewer_output.json when True.
    """
    art = tmp_path / "art"
    root = tmp_path / "oc"
    art.mkdir()
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(art))
    monkeypatch.setattr(orch_mod, "OPENCLAW_ROOT", str(root))
    monkeypatch.setattr(orch_mod, "_write_pipeline_event", lambda *a, **k: None)
    key = "pipeline:phase-1:CORE-E2:reviewer-attempt-1"
    sdir = root / "agents" / "reviewer" / "sessions"
    sdir.mkdir(parents=True)
    sid = "sess-r"
    (sdir / "sessions.json").write_text(
        json.dumps({f"agent:reviewer:{key}".lower(): {"sessionId": sid}})
    )
    rows = [{"type": "message", "message": {"role": "user", "content": "go"}}]
    if stop_reason is not None:
        rows.append({"type": "message", "message": {"role": "assistant", "stopReason": stop_reason}})
    (sdir / f"{sid}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    if verdict:
        v = art / "reviewer_output.json"
        v.write_text('{"blocking_issues": []}')
        os.utime(v, (time.time(), time.time()))
    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {"current_phase_raw_id": "CORE-E2"}
    return orch._make_verdict_hold_acceptor("reviewer", key, time.time() - attempt_age)


def test_holds_while_turn_in_flight(monkeypatch, tmp_path):
    """Last assistant row is a non-terminal tool-loop step (`toolUse`) + no verdict
    → HOLD (False). This is the streaming-past-.done case the fix recovers."""
    assert _acceptor(monkeypatch, tmp_path, stop_reason="toolUse")() is False


def test_accepts_when_turn_terminally_ended(monkeypatch, tmp_path):
    """Last assistant row is a terminal stop (`stop`) + no verdict → ACCEPT (True),
    immediately, so the gate's CONTRACT_FAILURE path runs with no stall-window
    latency. This is the genuine no-verdict end the stamp heuristic false-held."""
    assert _acceptor(monkeypatch, tmp_path, stop_reason="stop")() is True


def test_accepts_when_verdict_present(monkeypatch, tmp_path):
    """A fresh parseable verdict on disk → ACCEPT immediately, even mid-tool-loop
    (never hold when the real verdict already landed)."""
    assert _acceptor(monkeypatch, tmp_path, stop_reason="toolUse", verdict=True)() is True


def test_accepts_when_budget_spent(monkeypatch, tmp_path):
    """Past the hold budget the acceptor stops holding even while in flight, so a
    held sentinel can never hang the poll beyond _OVERFLOW_HOLD_BUDGET_SECONDS."""
    over = orch_mod._OVERFLOW_HOLD_BUDGET_SECONDS + 100
    assert _acceptor(monkeypatch, tmp_path, stop_reason="toolUse", attempt_age=over)() is True


def test_accepts_when_terminality_unknown(monkeypatch, tmp_path):
    """No assistant row to read a stopReason from → can't confirm the turn is in
    flight → ACCEPT (conservative: never false-hold on uncertainty)."""
    assert _acceptor(monkeypatch, tmp_path, stop_reason=None)() is True
