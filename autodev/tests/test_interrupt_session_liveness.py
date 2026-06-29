"""Direct unit tests for the interrupt liveness primitives.

``_agent_turn_still_in_flight`` and ``_wait_for_stamp_settle`` (both orchestrator) are the two
oracles behind the consolidated ``_interrupt_agent_session`` helper.  Everywhere else they are
stubbed, so these tests pin their *real* behaviour:

* ``_agent_turn_still_in_flight`` — the pre-steer liveness gate.  It reads the session
  transcript's LAST assistant row (the same signal the verdict-hold acceptor trusts), NOT a
  stamp-movement window: ``True`` iff the turn is still streaming (a tool-loop ``stopReason`` or
  a recoverable context-overflow that will compact+resume), ``False`` iff a terminal
  ``stopReason`` proves the turn ended, ``None`` iff the transcript is unresolvable.  This is
  what lets ``skip_if_idle`` leave a finished agent alone WITHOUT misreading a live
  mid-model-call agent (whose activity stamp is silent for the whole call) as idle — the
  false-negative that made the old 3 s ``session_is_streaming`` probe skip the abort it exists
  to perform.
* ``_wait_for_stamp_settle`` — the post-steer wait: loops the settle probe until quiet or until
  ``_INTERRUPT_SETTLE_MAX``.  It absorbs the steer's own spawned stop-turn.
"""

import json
import os
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402


def _bare_orch():
    return orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)


def _write_session(tmp_path, monkeypatch, role, session_key, rows, *, sid="sid-1", write_jsonl=True):
    """Materialise ``OPENCLAW_ROOT/agents/{role}/sessions/{sessions.json,<sid>.jsonl}`` and point
    ``orchestrator.OPENCLAW_ROOT`` at ``tmp_path`` (the same lookup ``_resolve_session_jsonl_path``
    performs).  ``rows`` are written verbatim as JSONL lines; returns the jsonl path.  With
    ``write_jsonl=False`` the sessions.json names a sid whose ``.jsonl`` is absent (an
    unresolvable transcript)."""
    monkeypatch.setattr(orch_mod, "OPENCLAW_ROOT", str(tmp_path))
    sdir = tmp_path / "agents" / role / "sessions"
    sdir.mkdir(parents=True)
    full_key = f"agent:{role}:{session_key}".lower()
    (sdir / "sessions.json").write_text(json.dumps({full_key: {"sessionId": sid}}))
    jsonl = sdir / f"{sid}.jsonl"
    if write_jsonl:
        with open(jsonl, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    return str(jsonl)


def _assistant_row(stop_reason=None, error_message=None):
    inner = {"role": "assistant"}
    if stop_reason is not None:
        inner["stopReason"] = stop_reason
    if error_message is not None:
        inner["errorMessage"] = error_message
    return {"type": "message", "message": inner}


# ---------------------------------------------------------------------------
# _agent_turn_still_in_flight — the pre-steer liveness gate (transcript last-row)
# ---------------------------------------------------------------------------

_KEY = "pipeline:phase-2:CORE-1:executor-attempt-1"


def test_in_flight_true_on_tooluse(tmp_path, monkeypatch):
    """Last assistant row is a non-terminal tool-loop step (stopReason 'toolUse') → still
    streaming past .done → True (steer)."""
    _write_session(tmp_path, monkeypatch, "executor", _KEY, [
        _assistant_row("toolUse"),
        {"type": "tool_result"},  # a trailing non-message row must not change the verdict
    ])
    assert _bare_orch()._agent_turn_still_in_flight("executor", _KEY) is True


def test_in_flight_false_on_terminal_stop_reason(tmp_path, monkeypatch):
    """Last assistant row has a terminal stopReason → turn provably ended → False (skip)."""
    _write_session(tmp_path, monkeypatch, "executor", _KEY, [
        _assistant_row("toolUse"),
        _assistant_row("endTurn"),
    ])
    assert _bare_orch()._agent_turn_still_in_flight("executor", _KEY) is False


def test_in_flight_true_on_recoverable_overflow(tmp_path, monkeypatch):
    """An overflow turn ends on stopReason 'error' (a terminal value) but OpenClaw compacts +
    resumes it, so it is still effectively in flight → True.  The overflow check must win over
    the otherwise-terminal stopReason."""
    _write_session(tmp_path, monkeypatch, "reviewer", _KEY, [
        _assistant_row(
            "error",
            "Context overflow: estimated context size exceeds safe threshold during tool loop.",
        ),
    ])
    assert _bare_orch()._agent_turn_still_in_flight("reviewer", _KEY) is True


def test_in_flight_none_when_sessions_json_missing(tmp_path, monkeypatch):
    """No sessions.json at all → unresolvable → None (callers steer, not skip)."""
    monkeypatch.setattr(orch_mod, "OPENCLAW_ROOT", str(tmp_path))
    assert _bare_orch()._agent_turn_still_in_flight("executor", _KEY) is None


def test_in_flight_none_when_jsonl_absent(tmp_path, monkeypatch):
    """sessions.json names a sid whose .jsonl is absent → unresolvable → None."""
    _write_session(tmp_path, monkeypatch, "executor", _KEY, [], write_jsonl=False)
    assert _bare_orch()._agent_turn_still_in_flight("executor", _KEY) is None


def test_in_flight_none_when_no_assistant_row(tmp_path, monkeypatch):
    """JSONL exists but has no assistant row yet → terminality unknown → None (steer)."""
    _write_session(tmp_path, monkeypatch, "executor", _KEY, [
        {"type": "message", "message": {"role": "user"}},
    ])
    assert _bare_orch()._agent_turn_still_in_flight("executor", _KEY) is None


# ---------------------------------------------------------------------------
# _wait_for_stamp_settle — the post-steer settle loop
# ---------------------------------------------------------------------------


def test_wait_for_stamp_settle_true_when_quiet(monkeypatch):
    """First settle probe quiet → returns True immediately."""
    monkeypatch.setattr(orch_mod, "verify_session_stopped", lambda *a, **k: True)
    assert _bare_orch()._wait_for_stamp_settle("/tmp/x.stamp") is True


def test_wait_for_stamp_settle_false_on_timeout(monkeypatch):
    """Never quiet → returns False once the bounded deadline passes (no infinite loop)."""
    monkeypatch.setattr(orch_mod, "_INTERRUPT_SETTLE_MAX", 0.05, raising=False)
    monkeypatch.setattr(orch_mod, "verify_session_stopped", lambda *a, **k: False)
    start = time.time()
    assert _bare_orch()._wait_for_stamp_settle("/tmp/x.stamp") is False
    assert time.time() - start < 5, "settle-wait must be bounded by _INTERRUPT_SETTLE_MAX"
