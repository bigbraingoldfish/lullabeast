"""Phase 9 — abort the in-flight agent session on escalation (zombie-session fix).

**The bug.** When the orchestrator gives up on a phase and routes to escalation, it
leaves the last-invoked pipeline agent's OpenClaw session *streaming*. The executor
retry-start abort only stops the *prior* attempt when launching the *next* one — so
the terminal attempt that triggers escalation is never aborted. That "zombie" keeps
running and can `git commit` / `git tag` / edit files AFTER the orchestrator handed
off to the human (observed live on Tick-Tac-Toe, 2026-06-06: a CORE-E1 commit + an
inert `phase_base_commit` git tag created ~64 s post-escalation).

**The fix.** Track the last-invoked agent's session (`_record_active_agent`) and at
the single escalation chokepoint abort it (`_abort_active_agent_session`). The abort
now issues `sessions.steer {key, message:<non-empty>}` (via `abort_agent_session`), which is
the only call that reaches a `/hooks`-launched embedded run and blocks server-side
until it ends. The old `_HALT_SESSION_MESSAGE`-via-`invoke_agent_webhook` fallback was
**removed**: it fired a *second* `/hooks` run rather than interrupting the streaming
one — the actual cause of "two agents updating at once". The helper now never sends a
webhook; a not-confirmed-stopped session emits `abort_verify_failed` and soft-continues.

The same helper is reused by the reviewer contract-shape retry handlers
(`source="reviewer_retry"`) to kill the prior reviewer run before re-invoking.

These tests pin the helper's branch logic (abort target key shape; NO webhook on any
branch; verify-failed vs abort-failed; no-op when nothing is in-flight; the new
reviewer_retry source) and the refactor wiring (record at the 3 invocation sites +
abort at the escalation dispatch + the removal of `_HALT_SESSION_MESSAGE`).
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


def _make_orch(monkeypatch, tmp_path, *, abort_ret=True, verify_ret=True, in_flight_ret=True):
    """A bare Orchestrator with the module-level abort/verify/webhook/event functions and the
    transcript-based liveness oracle stubbed to record calls. Returns (orch, calls).

    ``invoke_agent_webhook`` is still stubbed so that any *unexpected* webhook call
    (a regression that re-introduces the removed halt fallback) is recorded and the
    no-webhook assertions catch it.

    ``_agent_turn_still_in_flight`` is stubbed to ``in_flight_ret`` (default True): the
    consolidated ``_interrupt_agent_session`` helper liveness-gates the steer on the
    ``skip_if_idle`` paths, so the escalation/reviewer-retry tests must declare the prior turn
    "still in flight" for the abort to actually fire (a PROVABLY-ended turn — ``in_flight_ret``
    False — is now a clean ``skipped_idle`` no-op). The real oracle reads the session transcript;
    here we drive the decision directly (its real behaviour is pinned in
    ``test_interrupt_session_liveness.py``). ``_INTERRUPT_SETTLE_MAX`` is shrunk so the
    verify-False settle-wait does not busy-loop for the full 45 s against the (instant) verify
    stub."""
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "_INTERRUPT_SETTLE_MAX", 0.05, raising=False)
    calls = {"abort": [], "verify": [], "webhook": [], "events": [], "in_flight": []}

    def _abort(key, ws, tok, **k):
        calls["abort"].append((key, ws, tok))
        return abort_ret

    def _verify(stamp, settle_seconds=5.0):
        calls["verify"].append((stamp, settle_seconds))
        return verify_ret

    def _webhook(agent_id, session_key, token, **k):
        calls["webhook"].append((agent_id, session_key, k.get("message"), k.get("url")))
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
    # Instance-attribute stub shadows the bound method; it receives (role, session_key).
    orch._agent_turn_still_in_flight = lambda role, session_key: (
        calls["in_flight"].append(session_key) or in_flight_ret
    )
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


def test_abort_confirmed_stopped_sends_no_webhook(monkeypatch, tmp_path):
    """Abort acked AND verify says stopped → the steer interrupt is enough; the
    helper never sends a webhook (the removed halt-message fallback used to fire a
    second /hooks run). The abort targets the gateway `agent:{role}:…` lowercased
    key. No abort_verify_failed; abort_attempted has result=ok and no
    halt_message_sent key."""
    orch, calls = _make_orch(monkeypatch, tmp_path, abort_ret=True, verify_ret=True)
    orch._record_active_agent("executor", "pipeline:phase-2:CORE-1:executor-attempt-3")

    orch._abort_active_agent_session("escalation")

    assert calls["abort"], "abort_agent_session must be called"
    assert calls["abort"][0][0] == "agent:executor:pipeline:phase-2:core-1:executor-attempt-3"
    assert calls["webhook"] == [], "the helper must never send a webhook (halt fallback removed)"
    assert _event(calls, "abort_verify_failed") is None
    att = _event(calls, "abort_attempted")
    assert att is not None and att["source"] == "escalation"
    assert att["result"] == "ok"
    assert "halt_message_sent" not in att, "the removed halt-fallback field must be gone"
    assert orch._active_agent_session_key is None and orch._active_agent_role is None


def test_abort_verify_failed_emits_event_no_halt(monkeypatch, tmp_path):
    """Abort acked but verify shows the stamp still advancing → emit abort_verify_failed
    and soft-continue. The helper must NOT send any webhook (the halt-message fallback
    that fired a second /hooks run — the double-write cause — is removed)."""
    orch, calls = _make_orch(monkeypatch, tmp_path, abort_ret=True, verify_ret=False)
    orch._record_active_agent("executor", "pipeline:phase-2:CORE-1:executor-attempt-3")

    orch._abort_active_agent_session("escalation")

    assert _event(calls, "abort_verify_failed") is not None
    assert calls["webhook"] == [], "no halt webhook — the fallback was removed"
    att = _event(calls, "abort_attempted")
    assert att is not None and "halt_message_sent" not in att
    assert orch._active_agent_session_key is None


def test_abort_failed_clears_tracking_no_halt(monkeypatch, tmp_path):
    """Abort call itself failed → skip verify, send no webhook, still clear tracking and
    emit abort_attempted result=FAILED. The removed halt fallback used to fire a webhook
    here; it must not anymore."""
    orch, calls = _make_orch(monkeypatch, tmp_path, abort_ret=False)
    orch._record_active_agent("planner", "pipeline:phase-1:CORE-1:planner-attempt-1")

    orch._abort_active_agent_session("escalation")

    assert calls["verify"] == [], "verify must be skipped when the abort call failed"
    assert calls["webhook"] == [], "no halt webhook — the fallback was removed"
    att = _event(calls, "abort_attempted")
    assert att is not None and att["result"] == "FAILED"
    assert "halt_message_sent" not in att
    assert orch._active_agent_session_key is None and orch._active_agent_role is None


def test_abort_active_agent_session_noop_when_none(monkeypatch, tmp_path):
    """No in-flight session recorded → the helper is a clean no-op (no abort, no
    webhook, no event). Covers F4/F10/exception escalations where no agent was
    streaming, and the cleared-after-abort state."""
    orch, calls = _make_orch(monkeypatch, tmp_path)
    orch._abort_active_agent_session("escalation")
    assert calls["abort"] == []
    assert calls["webhook"] == []
    assert calls["events"] == []


def test_reviewer_retry_source_aborts_without_halt(monkeypatch, tmp_path):
    """The reviewer contract-shape retry path reuses this helper with
    source="reviewer_retry" to kill the prior reviewer run before re-invoking.
    It must abort the recorded reviewer key, emit abort_attempted with that source,
    send no webhook, and clear tracking (the next reviewer invoke re-records it)."""
    orch, calls = _make_orch(monkeypatch, tmp_path, abort_ret=True, verify_ret=True)
    orch._record_active_agent("reviewer", "pipeline:phase-3:STATS-E1:reviewer-attempt-1")

    orch._abort_active_agent_session("reviewer_retry")

    assert calls["abort"][0][0] == "agent:reviewer:pipeline:phase-3:stats-e1:reviewer-attempt-1"
    assert calls["webhook"] == []
    att = _event(calls, "abort_attempted")
    assert att is not None and att["source"] == "reviewer_retry"
    assert orch._active_agent_session_key is None


def test_escalation_skips_steer_when_turn_ended(monkeypatch, tmp_path):
    """When the terminal agent's turn has PROVABLY ended (``_agent_turn_still_in_flight`` is
    False), the escalation abort is a clean ``skipped_idle`` no-op: NO steer (no gratuitous turn
    on a finished agent), NO webhook, but tracking is still cleared and ``abort_attempted``
    result=skipped_idle is emitted. Complements ``test_abort_confirmed_stopped_sends_no_webhook``
    (the in-flight zombie IS steered), pinning both arms of the liveness gate."""
    orch, calls = _make_orch(monkeypatch, tmp_path, in_flight_ret=False)
    orch._record_active_agent("executor", "pipeline:phase-2:CORE-1:executor-attempt-3")

    orch._abort_active_agent_session("escalation")

    assert calls["abort"] == [], "a finished turn must NOT be steered on escalation"
    assert calls["webhook"] == []
    att = _event(calls, "abort_attempted")
    assert att is not None and att["result"] == "skipped_idle" and att["source"] == "escalation"
    assert orch._active_agent_session_key is None and orch._active_agent_role is None


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


def test_no_halt_message_constant():
    """Removal completeness: the `_HALT_SESSION_MESSAGE`-via-`/hooks` fallback (which
    fired a *second* run instead of interrupting the streaming one — the double-write
    cause) is gone. Neither the constant nor the `halt_message_sent` event field may
    remain. `sessions.steer` now actually stops embedded runs, so the fallback is dead."""
    assert "_HALT_SESSION_MESSAGE" not in _ORCH_SRC, (
        "the dead _HALT_SESSION_MESSAGE constant/fallback must be removed entirely"
    )
    assert "halt_message_sent" not in _ORCH_SRC, (
        "the halt_message_sent event field must be removed (no halt message is sent)"
    )
