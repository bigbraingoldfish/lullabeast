"""Escalation-advisory loader + honest fallback.

Two behaviors, both about the human-facing escalation *advisory* (the dashboard
``escalation_message`` / ``escalation_advisory_status``) — distinct from the
``reviewer_retry_directive`` reviewer-retry channel:

1. **Loader is visible while the advisory generates.** The escalation panel only
   renders at ``WAITING_FOR_HUMAN``; the advisory is a ≤30s LLM call. So the
   transition to ``WAITING_FOR_HUMAN`` (with ``escalation_advisory_status=
   "generating"``) must happen BEFORE the advisory call, or the "generating"
   loader is never seen. These source-level guards pin that ordering at the 3
   escalation dispatch sites (repo-init, reviewer, crash handler) — matching the
   source-inspection idiom in ``test_reviewer_routing_dispatch.py`` /
   ``test_traffic_cop_retired.py`` (the in-``run()`` blocks aren't extractable).

2. **Honest fallback when the advisory hangs/fails.** ``_compose_fallback_reason``
   builds a deterministic, factual reason from hard signals (``last_error_code`` /
   ``escalation_trigger_reason`` / ``failure_context.json``) — NEVER the phase's
   ``failure_language`` (that fabrication produced the misleading "blank white
   page" message). ``_generate_and_record_advisory`` records it on the fallback
   path so ``escalation_message`` is populated (not left empty for the UI to
   show a generic line).
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

_ORCH_SRC = open(os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8").read()


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


# ---------------------------------------------------------------------------
# Part A — _generate_and_record_advisory records ready / honest-fallback
# ---------------------------------------------------------------------------

class TestGenerateAndRecordAdvisory:

    def test_ready_path_records_summary(self, tmp_workspace):
        import orchestrator as orc_module
        orch, ps_path = _bare_orch(tmp_workspace)
        orch._generate_escalation_advisory = MagicMock(
            return_value={"summary": "X failed.", "recommended_action": "Reset Phase"}
        )
        ps = {}
        with patch.object(orc_module, "PHASE_STATE_FILE", ps_path), \
             patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", tmp_workspace):
            ret = orch._generate_and_record_advisory(ps)
        assert ret is not None
        assert ps["escalation_advisory_status"] == "ready"
        assert ps["escalation_message"] == "X failed."
        assert ps["escalation_recommended_action"] == "Reset Phase"

    def test_fallback_path_populates_message(self, tmp_workspace):
        """Advisory None (hang/fail) → status='fallback' AND a non-empty honest message."""
        import orchestrator as orc_module
        orch, ps_path = _bare_orch(tmp_workspace)
        orch._generate_escalation_advisory = MagicMock(return_value=None)
        ps = {"last_error_code": "ERR_REVIEWER_CONTRACT_FAILURE",
              "escalation_trigger_reason": "Reviewer CONTRACT_FAILURE"}
        with patch.object(orc_module, "PHASE_STATE_FILE", ps_path), \
             patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", tmp_workspace):
            ret = orch._generate_and_record_advisory(ps)
        assert ret is None
        assert ps["escalation_advisory_status"] == "fallback"
        assert ps.get("escalation_message"), "fallback must populate escalation_message"
        assert "log" in ps["escalation_message"].lower()


# ---------------------------------------------------------------------------
# Part A — source-level reorder guards (WAITING_FOR_HUMAN before the advisory)
# ---------------------------------------------------------------------------

def _slice(start_sub, end_sub):
    start = _ORCH_SRC.find(start_sub)
    assert start != -1, f"anchor not found: {start_sub!r}"
    end = _ORCH_SRC.find(end_sub, start)
    assert end != -1, f"end anchor not found after {start_sub!r}: {end_sub!r}"
    return _ORCH_SRC[start:end]


class TestDispatchReorderGuards:

    def test_helper_exists(self):
        assert "def _generate_and_record_advisory" in _ORCH_SRC
        assert "def _compose_fallback_reason" in _ORCH_SRC

    def test_reviewer_site_transitions_before_advisory(self):
        # Reviewer-specific anchor: the escalation session key (repo-init uses
        # ":repo-init-failure", crash uses ":exception-escalation").
        block = _slice(
            'f"pipeline:phase-{phase}:{raw_id}:escalation"',
            "webhook_status = invoke_agent_webhook",
        )
        t = block.find('transition_state("WAITING_FOR_HUMAN", "Invoking Escalation Agent")')
        a = block.find("_generate_and_record_advisory")
        assert t != -1 and a != -1, "reviewer dispatch must transition + use the advisory helper"
        assert t < a, "reviewer dispatch must transition to WAITING_FOR_HUMAN BEFORE generating the advisory"

    def test_repo_init_site_transitions_before_advisory(self):
        block = _slice(
            '"Repository setup needs your attention"',
            "webhook_status = invoke_agent_webhook",
        )
        t = block.find('transition_state("WAITING_FOR_HUMAN"')
        a = block.find("_generate_and_record_advisory")
        assert t != -1 and a != -1, "repo-init dispatch must transition + use the advisory helper"
        assert t < a, "repo-init dispatch must transition to WAITING_FOR_HUMAN BEFORE generating the advisory"

    def test_crash_site_transitions_before_advisory(self):
        block = _slice("Escalated after unhandled exception", "except Exception as escalation_err")
        t = block.find('transition_state(')
        a = block.find("_generate_and_record_advisory")
        assert t != -1 and a != -1, "crash handler must transition + use the advisory helper"
        assert t < a, "crash handler must transition to WAITING_FOR_HUMAN BEFORE generating the advisory"

    def test_sites_do_not_inline_generate_before_transition(self):
        """No dispatch site should still call the raw _generate_escalation_advisory()
        inline before the transition (the old advisory-before-panel ordering)."""
        # The only direct call to the raw method is inside the helper.
        direct = _ORCH_SRC.count("self._generate_escalation_advisory()")
        assert direct == 1, (
            f"expected exactly one direct _generate_escalation_advisory() call "
            f"(inside _generate_and_record_advisory), found {direct}"
        )
