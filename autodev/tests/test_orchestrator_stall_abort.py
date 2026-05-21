"""Section 2 — inline abort + verify on stall / no_first_activity / timeout outcomes.

When ``poll_for_sentinel`` returns ``PollResult`` with a non-success reason
the orchestrator must:

1. Call ``abort_agent_session`` against the *current* attempt's session key
   so the dead session releases its OpenClaw slot.
2. Capture and log the abort return value (``[ABORT] result=ok|FAILED ...``).
3. On a successful abort, verify the agent really stopped via
   ``verify_session_stopped``.  If verification fails (gateway acknowledged
   abort but stamp still advancing) we emit ``abort_verify_failed`` to the
   activity feed and **soft-continue** — the orchestrator launches the
   next attempt anyway.  Rationale: 90%+ of long runs eventually resolve
   on retry, whereas a forced ``HALTED_SILENT`` always requires human
   intervention.
4. Otherwise return control so the existing retry path runs.

These tests pin the helper that encapsulates this logic plus source-level
checks that all three pipeline agents (planner / executor / reviewer)
invoke it on the appropriate poll outcomes.
"""

import os
import re
import sys
import time
import types

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


def _bare_orchestrator():
    """Return an Orchestrator instance without running ``__init__``.

    Mirrors the pattern used by ``test_orchestrator_gateway_config.py``:
    we want to test the helper method without instantiating the full
    lock/config/state machinery.
    """
    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.openclaw_config = {
        "gateway_token": "gw-tok",
        "gateway_ws_url": "ws://127.0.0.1:18789/__openclaw__/ws",
    }
    inst.state = {"status": "RUNNING", "pipeline_status": "RUNNING"}
    inst.lock_fd = None
    return inst


# ---------------------------------------------------------------------------
# Helper method tests
# ---------------------------------------------------------------------------


class TestHandleStallOutcomeHelper:
    def test_helper_method_exists_and_is_callable(self):
        """The Orchestrator must expose ``_handle_stall_outcome`` so the
        three poll sites can share a single, tested implementation rather
        than open-coding abort+verify+escalate at each call site."""
        assert callable(
            getattr(orch_mod.Orchestrator, "_handle_stall_outcome", None)
        ), (
            "Orchestrator must define _handle_stall_outcome(agent, session_key, "
            "stamp_path, reason) so abort+verify+escalate logic is centralised"
        )

    def test_returns_true_when_abort_and_verify_both_succeed(
        self, monkeypatch, tmp_path, capsys
    ):
        """abort succeeds, verify returns True → helper returns True so the
        caller proceeds with the existing retry path."""
        orch = _bare_orchestrator()
        stamp = tmp_path / "executor_activity.stamp"
        stamp.write_text("")
        monkeypatch.setattr(orch_mod, "abort_agent_session", lambda *a, **k: True)
        monkeypatch.setattr(
            orch_mod, "verify_session_stopped", lambda *a, **k: True
        )
        result = orch._handle_stall_outcome(
            agent_role="executor",
            session_key="agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
            stamp_path=str(stamp),
            reason="stalled",
        )
        assert result is True
        out = capsys.readouterr().out
        assert "[ABORT] result=ok" in out
        # No HALTED_SILENT transition.
        assert orch.state.get("pipeline_status") == "RUNNING"

    def test_soft_continues_when_verify_fails(
        self, monkeypatch, tmp_path, capsys
    ):
        """Abort acknowledged but ``verify_session_stopped`` returns False
        — the gateway said yes but the agent is still streaming.  Per the
        post-CORE-E6 policy review, we no longer halt.  We emit the
        ``[ABORT][VERIFY_FAILED]`` print + ``abort_verify_failed`` event
        for activity-feed transparency and return ``True`` so the caller
        proceeds with the next attempt.
        """
        orch = _bare_orchestrator()
        stamp = tmp_path / "executor_activity.stamp"
        stamp.write_text("")
        transitions = []
        monkeypatch.setattr(
            orch,
            "transition_state",
            lambda new_status, action: transitions.append((new_status, action)),
        )
        monkeypatch.setattr(orch_mod, "abort_agent_session", lambda *a, **k: True)
        monkeypatch.setattr(
            orch_mod, "verify_session_stopped", lambda *a, **k: False
        )
        result = orch._handle_stall_outcome(
            agent_role="executor",
            session_key="agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
            stamp_path=str(stamp),
            reason="stalled",
        )
        assert result is True, (
            "Helper must return True after soft-continue so the caller "
            "proceeds with the next attempt"
        )
        assert not any(t[0] == "HALTED_SILENT" for t in transitions), (
            "Helper must NOT transition to HALTED_SILENT on verify failure"
        )
        out = capsys.readouterr().out
        assert "[ABORT][VERIFY_FAILED]" in out, (
            "Diagnostic marker must remain so operators can grep logs"
        )

    def test_returns_true_when_abort_returns_false(
        self, monkeypatch, tmp_path, capsys
    ):
        """abort_agent_session is best-effort by contract.  A False return
        (network error, session not found, etc.) gets logged but does NOT
        block retries — the orchestrator's existing flow continues.  Only
        an acknowledged-but-not-stopped session triggers escalation.
        """
        orch = _bare_orchestrator()
        stamp = tmp_path / "executor_activity.stamp"
        stamp.write_text("")
        monkeypatch.setattr(orch_mod, "abort_agent_session", lambda *a, **k: False)
        # verify_session_stopped must NOT be called when abort returned False
        # (no point verifying an abort that wasn't acknowledged).
        verify_calls = []
        monkeypatch.setattr(
            orch_mod,
            "verify_session_stopped",
            lambda *a, **k: verify_calls.append(a) or True,
        )
        result = orch._handle_stall_outcome(
            agent_role="executor",
            session_key="agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
            stamp_path=str(stamp),
            reason="stalled",
        )
        assert result is True
        assert verify_calls == [], (
            "verify_session_stopped must not be called when abort returned False"
        )
        out = capsys.readouterr().out
        assert "[ABORT] result=FAILED" in out

    def test_handles_no_first_activity_reason_same_path(
        self, monkeypatch, tmp_path, capsys
    ):
        """The ``no_first_activity`` outcome (startup grace exceeded) goes
        through the same abort+verify path as ``stalled``.  The session
        may exist on the gateway side even though no hook fired — abort
        it cleanly to free the slot."""
        orch = _bare_orchestrator()
        stamp = tmp_path / "executor_activity.stamp"
        stamp.write_text("")
        monkeypatch.setattr(orch_mod, "abort_agent_session", lambda *a, **k: True)
        monkeypatch.setattr(
            orch_mod, "verify_session_stopped", lambda *a, **k: True
        )
        result = orch._handle_stall_outcome(
            agent_role="planner",
            session_key="agent:planner:pipeline:phase-1:core-e1:planner-attempt-1",
            stamp_path=str(stamp),
            reason="no_first_activity",
        )
        assert result is True
        out = capsys.readouterr().out
        assert "[ABORT] result=ok" in out
        assert "no_first_activity" in out, (
            "log line must mention the reason so operators can distinguish "
            "stall from startup-timeout in /tmp/orchestrator.log"
        )


# ---------------------------------------------------------------------------
# Source-level wire-up for each of the three poll sites
# ---------------------------------------------------------------------------


class TestPollSiteWiring:
    """Per agent: confirm the orchestrator source dispatches stall and
    no_first_activity outcomes through ``_handle_stall_outcome``."""

    def _agent_block(self, agent: str) -> str:
        """Slice the source between an agent's poll invocation and the
        next ``transition_state`` call following the result.  Crude but
        sufficient for the wiring check — alternatives (AST parsing)
        are heavier weight for a regression test of this size."""
        # Find a poll_for_sentinel call followed by something agent-specific.
        # We slice 400 lines around the agent's poll call.
        src = _ORCH_SRC
        agent_hint = f'"{agent}_activity.stamp"'
        idx = src.find(agent_hint)
        assert idx != -1, f"Could not locate {agent} poll site in orchestrator.py"
        start = max(0, src.rfind("poll_for_sentinel(", 0, idx))
        end = min(len(src), idx + 6000)
        return src[start:end]

    @pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
    def test_poll_site_dispatches_stalled_to_handle_stall_outcome(self, agent):
        """The block following each agent's poll call must reference
        ``_handle_stall_outcome`` and the ``stalled`` reason string."""
        block = self._agent_block(agent)
        assert "_handle_stall_outcome" in block, (
            f"{agent} poll site must call self._handle_stall_outcome(...) "
            f"when result.reason is 'stalled' or 'no_first_activity'"
        )
        assert "stalled" in block, (
            f"{agent} poll site must branch on 'stalled' reason"
        )

    @pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
    def test_poll_site_dispatches_no_first_activity_to_handle_stall_outcome(
        self, agent
    ):
        """Same site must also handle ``no_first_activity`` outcomes."""
        block = self._agent_block(agent)
        assert "no_first_activity" in block, (
            f"{agent} poll site must branch on 'no_first_activity' reason"
        )

    @pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
    def test_poll_site_returns_when_handle_stall_outcome_returns_false(
        self, agent
    ):
        """When the helper returns False (verify failed → HALTED_SILENT),
        the poll site must short-circuit out of its retry path rather than
        continue to the webhook invocation for attempt N+1.  We check for
        a ``return`` statement within ~200 chars after the helper call.
        """
        block = self._agent_block(agent)
        # Match either explicit return or continue-on-False patterns.
        # Window widened (Section 6.4 added _record_phase_outcome calls
        # between the helper invocation and the short-circuit return).
        pat = re.compile(
            r"_handle_stall_outcome\([^)]*\)[\s\S]{0,800}?\b(?:return|continue)\b"
        )
        assert pat.search(block), (
            f"{agent} poll site must short-circuit (return or continue) "
            f"when _handle_stall_outcome returns False — otherwise a "
            f"verify-failed escalation would still launch attempt N+1"
        )
