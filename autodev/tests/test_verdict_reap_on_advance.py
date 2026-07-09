"""Verdict reap — abort a session still streaming past its consumed verdict.

**The bug.** A model can write ``.done`` + a valid verdict and keep streaming; the
verdict-hold acceptor rightly accepts the verdict the instant it lands and the
pipeline advances. Nothing ever polls that session again — the tool-loop detector
watches only the *current* attempt, the retry-start abort only the *prior* attempt
of the same role, and the escalation abort only fires on give-up. A runaway turn
(e.g. an exec loop OpenClaw's input+output-keyed block never trips) therefore burns
provider spend unwatched (observed live 2026-07-07: a phase-1 planner streamed
~45 min past its PASS, through the entire rest of the run).

**The fix.** ``_reap_agent_session_after_verdict(role)`` at every verdict-consumption
point where the pipeline advances past the producing role. It is a role-guarded thin
wrapper over ``_abort_active_agent_session("verdict_reap")``, so the ``skip_if_idle``
liveness gate keeps the common already-ended turn a steer-free ``skipped_idle`` no-op.

These tests pin the helper's branch logic and the wiring at the consumption sites.
"""

import os
import sys

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
    """A bare Orchestrator with the steer/verify/event functions stubbed to record
    calls (same harness as test_escalation_aborts_inflight_session)."""
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "_INTERRUPT_SETTLE_MAX", 0.05, raising=False)
    calls = {"abort": [], "events": []}

    monkeypatch.setattr(
        orch_mod, "abort_agent_session",
        lambda key, ws, tok, **k: calls["abort"].append(key) or abort_ret,
    )
    monkeypatch.setattr(
        orch_mod, "verify_session_stopped",
        lambda stamp, settle_seconds=5.0: verify_ret,
    )
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
    orch._agent_turn_still_in_flight = lambda role, session_key: in_flight_ret
    return orch, calls


def _event(calls, name):
    matches = [d for (e, d) in calls["events"] if e == name]
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


def test_reap_steers_streaming_session_and_clears_tracking(monkeypatch, tmp_path):
    """A turn still in flight past its consumed verdict is steered; the event
    carries source=verdict_reap and the tracking fields clear."""
    orch, calls = _make_orch(monkeypatch, tmp_path, in_flight_ret=True)
    orch._record_active_agent("planner", "pipeline:phase-1:CORE-1:planner-attempt-1")

    orch._reap_agent_session_after_verdict("planner")

    assert calls["abort"] == ["agent:planner:pipeline:phase-1:core-1:planner-attempt-1"]
    att = _event(calls, "abort_attempted")
    assert att is not None and att["source"] == "verdict_reap" and att["result"] == "ok"
    assert orch._active_agent_session_key is None and orch._active_agent_role is None


def test_reap_skips_steer_when_turn_ended(monkeypatch, tmp_path):
    """The common case — the turn ended with (or before) its verdict — must be a
    steer-free skipped_idle no-op: no gratuitous injected turn on a finished agent."""
    orch, calls = _make_orch(monkeypatch, tmp_path, in_flight_ret=False)
    orch._record_active_agent("reviewer", "pipeline:phase-2:UI-1:reviewer-attempt-1")

    orch._reap_agent_session_after_verdict("reviewer")

    assert calls["abort"] == []
    att = _event(calls, "abort_attempted")
    assert att is not None and att["result"] == "skipped_idle"
    assert att["source"] == "verdict_reap"
    assert orch._active_agent_session_key is None


def test_reap_noop_on_role_mismatch(monkeypatch, tmp_path):
    """A stale recording from another role must be left to its own reap point —
    reaping the executor must not touch a recorded reviewer session."""
    orch, calls = _make_orch(monkeypatch, tmp_path)
    orch._record_active_agent("reviewer", "pipeline:phase-2:UI-1:reviewer-attempt-1")

    orch._reap_agent_session_after_verdict("executor")

    assert calls["abort"] == [] and calls["events"] == []
    assert orch._active_agent_session_key == "pipeline:phase-2:UI-1:reviewer-attempt-1"


def test_reap_noop_when_nothing_recorded(monkeypatch, tmp_path):
    orch, calls = _make_orch(monkeypatch, tmp_path)
    orch._reap_agent_session_after_verdict("planner")
    assert calls["abort"] == [] and calls["events"] == []


# ---------------------------------------------------------------------------
# Structural: the reap is wired at every verdict-consumption site
# ---------------------------------------------------------------------------


def test_helper_defined():
    assert "def _reap_agent_session_after_verdict(self" in _ORCH_SRC


def test_planner_verdict_sites_reap():
    """Both planner-gate consumption arms (PASS → executor, FAIL → retry) reap."""
    assert _ORCH_SRC.count('self._reap_agent_session_after_verdict("planner")') >= 2


def test_executor_verdict_sites_reap():
    """The executor advance points (succeeded PASS, preempted PASS, EX-RR) reap.
    The gate-FAIL arm deliberately does not — the retry-start abort owns it."""
    assert _ORCH_SRC.count('self._reap_agent_session_after_verdict("executor")') >= 3


def test_reviewer_verdict_sites_reap():
    """The reviewer verdicts that advance without an existing abort (PASS,
    ROUTE_EXECUTOR, ROUTE_PLANNER, MISSING_ARTIFACTS re-invoke) reap; the
    CONTRACT_FAILURE / *_UNVERIFIED / escalation arms keep their own aborts."""
    assert _ORCH_SRC.count('self._reap_agent_session_after_verdict("reviewer")') >= 4


def test_reviewer_pass_reaps_before_git_block():
    """On reviewer PASS the reap must precede the merge — a reviewer still
    streaming past its PASS could mutate the tree mid-merge."""
    pass_block = _ORCH_SRC.index('if gate_result == "PASS":')
    reap = _ORCH_SRC.index('self._reap_agent_session_after_verdict("reviewer")', pass_block)
    merge = _ORCH_SRC.index("Phase 10 Git Operations", pass_block)
    assert reap < merge, "reviewer PASS must reap before entering the git block"
