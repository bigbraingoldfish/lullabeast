"""Section 6.1.b — abort_attempted and abort_verify_failed events.

The Section 0/2 work added ``[ABORT] result=`` and ``[ABORT][VERIFY_FAILED]``
print lines but no structured events.  The UI activity feed can show
``gate_pass``/``gate_fail`` today; without abort events, operators have to tail
``/tmp/orchestrator.log`` by hand to see whether attempt #N's abort succeeded —
the exact gap that hid the CORE-E6 attempt-#2-kept-running incident.

Every steer-abort path (retry-start, inline stall, escalation, reviewer_retry) now
funnels through the single ``_interrupt_agent_session`` helper, which is the one
emitter of both events.  These tests pin the event contract **behaviourally** against
that helper (far more robust than the old source-scraping), plus a couple of structural
checks that the call sites still delegate to it:

* ``abort_attempted`` — emitted once per interrupt with
  ``{session_key, result, agent_role, source}`` (``result`` ∈
  ``ok`` / ``FAILED`` / ``skipped_idle`` / ``unconfirmed``).
* ``abort_verify_failed`` — emitted only when the post-steer settle-wait times out
  (a genuine runaway still streaming), carrying ``{session_key, stamp_path, ...}``.
"""

import os
import re
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


def _make_orch(monkeypatch, *, abort_ret=True, in_flight_ret=True, settled=True):
    """Bare Orchestrator with the abort/verify/event functions and the transcript-based
    liveness oracle stubbed.

    ``in_flight_ret`` is what the stubbed ``_agent_turn_still_in_flight`` returns — the
    tri-state turn-liveness signal that drives the ``skip_if_idle`` decision (its REAL
    behaviour, reading the session transcript's last assistant row, is pinned in
    ``test_interrupt_session_liveness.py``).  ``True`` = still in flight (steer), ``False`` =
    provably ended (skip), ``None`` = unresolvable (steer).  ``_INTERRUPT_SETTLE_MAX`` is
    shrunk so a ``settled=False`` run (verify stub always returns False) does not busy-loop
    for the full 45 s against the instant stub."""
    monkeypatch.setattr(orch_mod, "_INTERRUPT_SETTLE_MAX", 0.05, raising=False)
    calls = {"abort": [], "events": [], "in_flight": [], "verify": []}
    monkeypatch.setattr(
        orch_mod, "abort_agent_session",
        lambda key, ws, tok, **k: (calls["abort"].append(key) or abort_ret),
    )
    monkeypatch.setattr(
        orch_mod, "verify_session_stopped",
        lambda stamp, **k: (calls["verify"].append(stamp) or settled),
    )
    monkeypatch.setattr(
        orch_mod, "_write_pipeline_event",
        lambda ev, phase, agent, detail: calls["events"].append((ev, detail)),
    )
    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.openclaw_config = {"gateway_token": "gw", "gateway_ws_url": "ws://gw"}
    orch.state = {"current_phase_raw_id": "CORE-1"}
    # Instance-attribute stub shadows the bound method; it receives (role, session_key).
    orch._agent_turn_still_in_flight = lambda role, session_key: (
        calls["in_flight"].append(session_key) or in_flight_ret
    )
    return orch, calls


def _event(calls, name):
    matches = [d for (e, d) in calls["events"] if e == name]
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Behaviour — abort_attempted result codes + detail fields
# ---------------------------------------------------------------------------


def test_abort_attempted_ok_carries_session_key_and_result(monkeypatch):
    """A streaming session that the gateway aborts and that then settles emits
    ``abort_attempted`` with ``result="ok"`` and the gateway-namespaced session key."""
    orch, calls = _make_orch(monkeypatch, in_flight_ret=True, settled=True)
    orch._interrupt_agent_session(
        role="executor",
        session_key="pipeline:phase-2:CORE-1:executor-attempt-1",
        stamp_path="/tmp/executor_activity.stamp",
        source="retry_start",
        skip_if_idle=True,
        prior_attempt=1,
    )
    assert calls["abort"], "a still-in-flight session must actually be steered"
    att = _event(calls, "abort_attempted")
    assert att is not None
    assert att["result"] == "ok"
    assert att["session_key"] == "agent:executor:pipeline:phase-2:core-1:executor-attempt-1"
    assert att["source"] == "retry_start"
    assert att["prior_attempt"] == 1
    assert _event(calls, "abort_verify_failed") is None


def test_abort_attempted_skipped_idle_when_turn_ended(monkeypatch):
    """The liveness pre-check (skip_if_idle) skips ONLY on a PROVABLY-ended turn
    (``_agent_turn_still_in_flight`` is False): emits ``result="skipped_idle"`` and issues NO
    steer — no gratuitous turn on a done agent."""
    orch, calls = _make_orch(monkeypatch, in_flight_ret=False)
    result = orch._interrupt_agent_session(
        role="reviewer",
        session_key="pipeline:phase-3:UI-E1:reviewer-attempt-1",
        stamp_path="/tmp/reviewer_activity.stamp",
        source="reviewer_retry",
        skip_if_idle=True,
    )
    assert result == "skipped_idle"
    assert calls["abort"] == [], "a finished session must NOT be steered"
    att = _event(calls, "abort_attempted")
    assert att is not None and att["result"] == "skipped_idle"


def test_unresolvable_transcript_steers_not_skips(monkeypatch):
    """When the turn's liveness is UNKNOWN (transcript unresolvable → ``_agent_turn_still_in_flight``
    returns None), skip_if_idle must NOT skip — it steers. Favours killing a possible zombie over
    skipping it (the failure direction that corrupts the repo / races output files)."""
    orch, calls = _make_orch(monkeypatch, in_flight_ret=None, settled=True)
    result = orch._interrupt_agent_session(
        role="executor",
        session_key="pipeline:phase-2:CORE-1:executor-attempt-1",
        stamp_path="/tmp/executor_activity.stamp",
        source="escalation",
        skip_if_idle=True,
    )
    assert result == "ok"
    assert calls["abort"], "an unresolvable (unknown) turn must be steered, not skipped"


def test_abort_attempted_failed_when_steer_rejected(monkeypatch):
    """A steer the gateway rejects emits ``result="FAILED"`` and skips the settle-wait."""
    orch, calls = _make_orch(monkeypatch, in_flight_ret=True, abort_ret=False)
    result = orch._interrupt_agent_session(
        role="planner",
        session_key="pipeline:phase-1:CORE-1:planner-attempt-1",
        stamp_path="/tmp/planner_activity.stamp",
        source="inline_stall",
        skip_if_idle=False,
    )
    assert result == "failed"
    att = _event(calls, "abort_attempted")
    assert att is not None and att["result"] == "FAILED"
    assert calls["verify"] == [], "settle-wait must be skipped when the steer was rejected"


def test_abort_verify_failed_on_settle_timeout(monkeypatch):
    """A steered session still streaming after the settle ceiling emits
    ``abort_attempted result="unconfirmed"`` AND ``abort_verify_failed`` with the
    session key — the only signal the UI needs to render the runaway state in red."""
    orch, calls = _make_orch(monkeypatch, in_flight_ret=True, settled=False)
    result = orch._interrupt_agent_session(
        role="executor",
        session_key="pipeline:phase-2:CORE-1:executor-attempt-2",
        stamp_path="/tmp/executor_activity.stamp",
        source="retry_start",
        skip_if_idle=True,
        prior_attempt=2,
    )
    assert result == "unconfirmed"
    att = _event(calls, "abort_attempted")
    assert att is not None and att["result"] == "unconfirmed"
    vf = _event(calls, "abort_verify_failed")
    assert vf is not None
    assert vf["session_key"] == "agent:executor:pipeline:phase-2:core-1:executor-attempt-2"
    assert vf["stamp_path"] == "/tmp/executor_activity.stamp"


def test_stall_path_always_steers_even_when_turn_looks_ended(monkeypatch):
    """``_handle_stall_outcome`` passes ``skip_if_idle=False``: on a stall the agent is wedged
    by definition, so the liveness pre-check must NOT skip the steer even if the transcript
    oracle would report the turn ended."""
    orch, calls = _make_orch(monkeypatch, in_flight_ret=False, settled=True)
    orch._record_phase_outcome = lambda **k: None  # no phase_state on a bare instance
    orch._handle_stall_outcome(
        agent_role="planner",
        session_key="pipeline:phase-1:CORE-1:planner-attempt-1",
        stamp_path="/tmp/planner_activity.stamp",
        reason="stalled",
    )
    assert calls["abort"], "the stall path must steer even when the stamp is quiet"
    att = _event(calls, "abort_attempted")
    assert att is not None and att["result"] == "ok" and att["reason"] == "stalled"


# ---------------------------------------------------------------------------
# Structural — the call sites delegate to the single emitter
# ---------------------------------------------------------------------------


def test_interrupt_helper_is_the_single_emitter():
    """Both events are emitted from _interrupt_agent_session (the consolidated chokepoint),
    so there is exactly one place to reason about the abort event contract."""
    helper_idx = _ORCH_SRC.find("def _interrupt_agent_session")
    assert helper_idx != -1
    helper_next = _ORCH_SRC.find("\n    def ", helper_idx + 1)
    body = _ORCH_SRC[helper_idx : helper_next if helper_next != -1 else helper_idx + 6000]
    assert re.search(r'_write_pipeline_event\(\s*["\']abort_attempted["\']', body)
    assert re.search(r'_write_pipeline_event\(\s*["\']abort_verify_failed["\']', body)


def test_call_sites_delegate_to_interrupt_helper():
    """Retry-start, stall, and the active-session wrapper all route through the helper."""
    # _handle_stall_outcome delegates (inline stall path).
    stall_idx = _ORCH_SRC.find("def _handle_stall_outcome")
    stall_next = _ORCH_SRC.find("\n    def ", stall_idx + 1)
    assert "_interrupt_agent_session(" in _ORCH_SRC[stall_idx:stall_next]
    # _abort_active_agent_session delegates (escalation + reviewer_retry path).
    abort_idx = _ORCH_SRC.find("def _abort_active_agent_session")
    abort_next = _ORCH_SRC.find("\n    def ", abort_idx + 1)
    assert "_interrupt_agent_session(" in _ORCH_SRC[abort_idx:abort_next]
    # Executor retry-start site delegates with source="retry_start".
    rs = _ORCH_SRC.find("if retries > 0:")
    assert rs != -1, "executor retry-start guard not found"
    window = _ORCH_SRC[rs : rs + 1200]
    assert "_interrupt_agent_session(" in window, (
        "retry-start guard must call _interrupt_agent_session"
    )
    assert 'source="retry_start"' in window, (
        "retry-start interrupt must pass source=\"retry_start\""
    )
