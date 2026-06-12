"""Honest deterministic escalation reason (``_compose_fallback_reason``).

``_compose_fallback_reason`` builds a deterministic, factual reason from hard
signals (``last_error_code`` / ``escalation_trigger_reason`` /
``failure_context.json``) — NEVER the phase's ``failure_language`` (that
fabrication produced the misleading "blank white page" message). It is recorded
as ``escalation_message`` the moment an escalation dispatches
(``_record_escalation_reason``), and is upgraded in place when the escalation
agent writes its richer ``escalation_summary.json`` — see
``test_escalation_advisory_agent_owned.py`` for that fold-in contract. (The
former ``_generate_and_record_advisory`` LLM wrapper and the "generating"
loader-ordering guards were removed with the orchestrator's direct LLM call.)
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Bare-orch helper (method-level tests; mirrors test_contract_failure_orchestrator)
# ---------------------------------------------------------------------------

def _bare_orch(tmp_dir):
    """Bare Orchestrator with PROJECT_ARTIFACTS_DIR + PHASE_STATE_FILE → tmp_dir."""
    import orchestrator as orc_module

    ps_path = os.path.join(tmp_dir, "phase_state.json")
    with patch.object(orc_module, "PHASE_STATE_FILE", ps_path), \
         patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", tmp_dir):
        from orchestrator import Orchestrator
        orch = Orchestrator.__new__(Orchestrator)
        orch.lock_fd = None
        orch.openclaw_config = {"hooks": {"token": "t"}}
        orch.state = {"current_phase": 1, "current_phase_raw_id": "CORE-1"}
        orch.write_state = MagicMock()
        orch.transition_state = MagicMock()
    return orch, ps_path


def _write_failure_context(tmp_dir, **fields):
    with open(os.path.join(tmp_dir, "failure_context.json"), "w") as f:
        json.dump(fields, f)


# ---------------------------------------------------------------------------
# Part C — _compose_fallback_reason (deterministic, factual, no LLM)
# ---------------------------------------------------------------------------

class TestComposeFallbackReason:

    def test_contract_failure_from_trigger_reason(self, tmp_workspace):
        """CONTRACT_FAILURE: honest reason from last_error_code/escalation_trigger_reason
        (failure_context is NOT rewritten on this escalation)."""
        import orchestrator as orc_module
        orch, _ = _bare_orch(tmp_workspace)
        ps = {
            "last_error_code": "ERR_REVIEWER_CONTRACT_FAILURE",
            "escalation_trigger_reason": (
                "Reviewer CONTRACT_FAILURE: contract retry cap reached (3): "
                "CONTRACT_FAILURE_SOFT_RETRY_EXHAUSTED — reviewer ended without a verdict"
            ),
        }
        with patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", tmp_workspace):
            msg = orch._compose_fallback_reason(ps)
        assert msg and isinstance(msg, str)
        assert "reviewer" in msg.lower()
        assert "ERR_REVIEWER_CONTRACT_FAILURE" in msg
        # must point the operator at the log and admit the summary is unavailable
        assert "log" in msg.lower()

    def test_executor_failure_from_failure_context(self, tmp_workspace):
        """Executor/route-escalate: honest reason from failure_context hard fields."""
        import orchestrator as orc_module
        _write_failure_context(
            tmp_workspace,
            failing_agent="executor",
            gate_error_codes=["ERR_TESTS_FAILING"],
            attempt_number=3,
        )
        orch, _ = _bare_orch(tmp_workspace)
        ps = {"last_error_code": "ERR_TESTS_FAILING"}
        with patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", tmp_workspace):
            msg = orch._compose_fallback_reason(ps)
        assert msg and "executor" in msg.lower()
        assert "ERR_TESTS_FAILING" in msg
        assert "log" in msg.lower()

    def test_missing_signal_degrades_to_generic(self, tmp_workspace):
        """No error code, no trigger reason, no failure_context → minimal generic line."""
        import orchestrator as orc_module
        orch, _ = _bare_orch(tmp_workspace)
        with patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", tmp_workspace):
            msg = orch._compose_fallback_reason({})
        assert msg and isinstance(msg, str)
        assert "log" in msg.lower()

    def test_never_leaks_failure_language(self, tmp_workspace):
        """The phase's failure_language must NOT appear — that fabrication is the bug."""
        import orchestrator as orc_module
        sentinel = "a blank white page or a Vite error overlay"
        _write_failure_context(
            tmp_workspace,
            failing_agent="reviewer",
            gate_error_codes=["ERR_REVIEWER_CONTRACT_FAILURE"],
            current_phase_behavioral_verification={"failure_language": sentinel},
        )
        orch, _ = _bare_orch(tmp_workspace)
        ps = {"last_error_code": "ERR_REVIEWER_CONTRACT_FAILURE",
              "escalation_trigger_reason": "Reviewer CONTRACT_FAILURE"}
        with patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", tmp_workspace):
            msg = orch._compose_fallback_reason(ps)
        assert sentinel not in msg, "fallback must not surface failure_language as observed reality"


# (The former Part A — ``_generate_and_record_advisory`` recording tests and the
# "generating"-loader reorder guards — was removed with the orchestrator's LLM
# advisory call. The replacement contract — fallback recorded immediately at
# dispatch, agent summary promoted when it lands — is pinned by
# ``test_escalation_advisory_agent_owned.py``.)
