"""Phase 9 — abort the in-flight agent session on escalation (zombie-session fix).

**The bug.** When the orchestrator gives up on a phase and routes to escalation, it
leaves the last-invoked pipeline agent's OpenClaw session *streaming*. The executor
retry-start abort only stops the *prior* attempt when launching the *next* one — so
the terminal attempt that triggers escalation is never aborted. That "zombie" keeps
running and can `git commit` / `git tag` / edit files AFTER the orchestrator handed
off to the human (observed live on Tick-Tac-Toe, 2026-06-06: a CORE-E1 commit + an
inert `phase_base_commit` git tag created ~64 s post-escalation).

**The fix.** Track the last-invoked agent's session (`_record_active_agent`) and at
the single escalation chokepoint abort it (`_abort_active_agent_session`). OpenClaw's
`sessions.abort` is cooperative (no force-kill), so when the abort is NOT confirmed
to have stopped the session, we additionally inject a "halt — end your turn" message
as a fallback. Crucially we do NOT inject the message when the session is already
confirmed-stopped — that would needlessly wake it just to read "do nothing"
(operator-directed).

These tests pin the helper's branch logic (abort target key shape; message sent iff
not-confirmed-stopped; verify-failed vs abort-failed; best-effort message; no-op when
nothing is in-flight) and the refactor wiring (record at the 3 invocation sites +
abort at the escalation dispatch).
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402

_ORCH_SRC = open(
    os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8"
).read()


def _make_orch(monkeypatch, tmp_path, *, abort_ret=True, verify_ret=True, webhook_raises=False):
    """A bare Orchestrator with the module-level abort/verify/webhook/event functions
    stubbed to record calls. Returns (orch, calls)."""
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    calls = {"abort": [], "verify": [], "webhook": [], "events": []}

    def _abort(key, ws, tok, **k):
        calls["abort"].append((key, ws, tok))
        return abort_ret

    def _verify(stamp, settle_seconds=5.0):
        calls["verify"].append((stamp, settle_seconds))
        return verify_ret

    def _webhook(agent_id, session_key, token, **k):
        calls["webhook"].append((agent_id, session_key, k.get("message"), k.get("url")))
        if webhook_raises:
            raise RuntimeError("boom")
        return "SUCCESS"

    monkeypatch.setattr(orch_mod, "abort_agent_session", _abort)
    monkeypatch.setattr(orch_mod, "verify_session_stopped", _verify)
    monkeypatch.setattr(orch_mod, "invoke_agent_webhook", _webhook)
    monkeypatch.setattr(orch_mod, "_write_pipeline_event",
                        lambda ev, phase, agent, detail: calls["events"].append((ev, detail)))

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.openclaw_config = {
        "hooks": {"token": "tok"}, "hooks_url": "http://h",
        "gateway_token": "gw", "gateway_ws_url": "ws://gw",
    }
    orch.state = {"current_phase_raw_id": "CORE-1"}
    orch._active_agent_session_key = None
    orch._active_agent_role = None
    orch._active_agent_stamp = None
    return orch, calls


def _event(calls, name):
    matches = [d for (e, d) in calls["events"] if e == name]
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


def test_record_active_agent_sets_fields(monkeypatch, tmp_path):
    """_record_active_agent stores the bare session key, the role, and the role's
    activity-stamp path (used by verify_session_stopped)."""
    orch, _ = _make_orch(monkeypatch, tmp_path)
    orch._record_active_agent("executor", "pipeline:phase-2:CORE-1:executor-attempt-1")
    assert orch._active_agent_session_key == "pipeline:phase-2:CORE-1:executor-attempt-1"
    assert orch._active_agent_role == "executor"
    assert orch._active_agent_stamp.endswith("executor_activity.stamp")
    assert str(tmp_path) in orch._active_agent_stamp


def test_abort_confirmed_stopped_does_not_inject_halt(monkeypatch, tmp_path):
    """Abort acked AND verify says stopped → do NOT inject the halt message (waking a
    confirmed-stopped session just to tell it to do nothing is the exact waste the
    operator flagged). The abort targets the gateway `agent:{role}:…` lowercased key.
    No abort_verify_failed; abort_attempted carries halt_message_sent=False."""
    orch, calls = _make_orch(monkeypatch, tmp_path, abort_ret=True, verify_ret=True)
    orch._record_active_agent("executor", "pipeline:phase-2:CORE-1:executor-attempt-3")

    orch._abort_active_agent_session("escalation")

    assert calls["abort"], "abort_agent_session must be called"
    assert calls["abort"][0][0] == "agent:executor:pipeline:phase-2:core-1:executor-attempt-3"
    assert calls["webhook"] == [], "must NOT wake a confirmed-stopped session with a message"
    assert _event(calls, "abort_verify_failed") is None
    att = _event(calls, "abort_attempted")
    assert att is not None and att["source"] == "escalation"
    assert att["result"] == "ok"
    assert att["halt_message_sent"] is False
    assert orch._active_agent_session_key is None and orch._active_agent_role is None


def test_abort_verify_failed_injects_halt(monkeypatch, tmp_path):
    """Abort acked but verify shows the stamp still advancing → emit abort_verify_failed
    AND inject the halt message (the session is still streaming; tell it to wind down).
    The message goes to the agent's own bare session via invoke_agent_webhook."""
    orch, calls = _make_orch(monkeypatch, tmp_path, abort_ret=True, verify_ret=False)
    orch._record_active_agent("executor", "pipeline:phase-2:CORE-1:executor-attempt-3")

    orch._abort_active_agent_session("escalation")

    assert _event(calls, "abort_verify_failed") is not None
    assert len(calls["webhook"]) == 1
    agent_id, sk, msg, url = calls["webhook"][0]
    assert agent_id == "executor"
    assert sk == "pipeline:phase-2:CORE-1:executor-attempt-3"
    assert "end your turn" in (msg or "").lower()
    assert url == "http://h"
    att = _event(calls, "abort_attempted")
    assert att is not None and att["halt_message_sent"] is True
    assert orch._active_agent_session_key is None


def test_abort_failed_injects_halt(monkeypatch, tmp_path):
    """Abort call itself failed → don't verify; inject the halt message as a fallback
    (the HTTP webhook is a different channel from the WS abort and may still land).
    abort_attempted records result=FAILED + halt_message_sent=True."""
    orch, calls = _make_orch(monkeypatch, tmp_path, abort_ret=False)
    orch._record_active_agent("planner", "pipeline:phase-1:CORE-1:planner-attempt-1")

    orch._abort_active_agent_session("escalation")

    assert calls["verify"] == [], "verify must be skipped when the abort call failed"
    assert len(calls["webhook"]) == 1
    assert calls["webhook"][0][0] == "planner"
    att = _event(calls, "abort_attempted")
    assert att is not None and att["result"] == "FAILED"
    assert att["halt_message_sent"] is True


def test_abort_active_agent_session_noop_when_none(monkeypatch, tmp_path):
    """No in-flight session recorded → the helper is a clean no-op (no abort, no
    webhook, no event). Covers F4/F10/exception escalations where no agent was
    streaming, and the cleared-after-abort state."""
    orch, calls = _make_orch(monkeypatch, tmp_path)
    orch._abort_active_agent_session("escalation")
    assert calls["abort"] == []
    assert calls["webhook"] == []
    assert calls["events"] == []


def test_halt_message_is_best_effort(monkeypatch, tmp_path):
    """A raising invoke_agent_webhook (gateway down / session busy) must not propagate —
    the abort already happened, the fields are still cleared, and abort_attempted records
    halt_message_sent=False. Escalation must never be blocked by the halt attempt."""
    orch, calls = _make_orch(monkeypatch, tmp_path, abort_ret=True, verify_ret=False, webhook_raises=True)
    orch._record_active_agent("executor", "pipeline:phase-2:CORE-1:executor-attempt-3")

    orch._abort_active_agent_session("escalation")  # must not raise

    assert calls["abort"]
    assert orch._active_agent_session_key is None
    att = _event(calls, "abort_attempted")
    assert att is not None and att["halt_message_sent"] is False


# ---------------------------------------------------------------------------
# Structural: the refactor is wired (record at invocation sites + abort at dispatch)
# ---------------------------------------------------------------------------


def test_helpers_defined():
    assert "def _record_active_agent(self" in _ORCH_SRC
    assert "def _abort_active_agent_session(self" in _ORCH_SRC


def test_escalation_dispatch_aborts_inflight_session():
    """The escalation dispatch must abort the in-flight session on the (once-per-
    escalation) first-invocation path."""
    assert 'self._abort_active_agent_session("escalation")' in _ORCH_SRC, (
        "the escalation dispatch must call _abort_active_agent_session('escalation')"
    )


def test_three_invocation_sites_record_active_agent():
    """Planner, executor, and reviewer invocations must each record the active agent so
    the escalation abort knows which session to stop. Catches a new agent invocation
    that forgets to register (leaving a fresh zombie class)."""
    assert _ORCH_SRC.count("self._record_active_agent(") >= 3, (
        "all 3 pipeline-agent invocation sites must call _record_active_agent"
    )
