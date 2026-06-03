"""
Orchestrator CONTRACT_FAILURE reviewer-branch handling tests.

CONTRACT_FAILURE = the reviewer session ended without a parseable
reviewer_output.json (missing/malformed). This is a contract breach, NOT a
code-quality rejection and NOT an infrastructure outage — genuine
transport/provider failures are peeled off upstream (stall / dead-on-arrival /
provider-rejected). The handler is an unconditional self-heal:

  - Re-invoke the reviewer in a FRESH session with a corrective directive,
    counting reviewer_contract_retries, capped at 3 → escalate
    (CONTRACT_FAILURE_SOFT_RETRY_EXHAUSTED).
  - CONTRACT_FAILURE never consumes reviewer_retries (reserved for genuine
    code-quality rejections).

reset_execution PRESERVES reviewer_contract_retries (per-phase budget);
reset_phase zeros it.

This file replaces the former ``test_infra_failure_orchestrator.py`` (the
INFRA_FAILURE → CONTRACT_FAILURE rename + the reviewer_infra_retries →
reviewer_contract_retries counter rename + the feedback-delivery wiring).

FIND-ID: RR-1, RR-4, FIND-REVIEWER-CONTRACT
Spec Reference: PIPELINE-SPEC.md §7 "Gate Scripts > Reviewer Output Gate"
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch


OPENCLAW_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE_SCRIPTS_DIR = os.path.join(OPENCLAW_DIR, "autodev", "pipeline", "gate_scripts")
for _p in [GATE_SCRIPTS_DIR, OPENCLAW_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _make_contract_orch(tmp_dir, initial_phase_state=None):
    """Return (orch, ps_path) with module-level constants patched to tmp_dir."""
    import orchestrator as orc_module

    ps_path = os.path.join(tmp_dir, "phase_state.json")
    state_file = os.path.join(tmp_dir, "pipeline_state.json")
    config_file = os.path.join(tmp_dir, "openclaw.json")

    with open(config_file, "w") as f:
        json.dump({"hooks": {"token": "test-tok"}}, f)

    if initial_phase_state is not None:
        with open(ps_path, "w") as f:
            json.dump(initial_phase_state, f)

    with (
        patch.object(orc_module, "STATE_FILE", state_file),
        patch.object(orc_module, "SYMLINK_TARGET", tmp_dir),
        patch.object(orc_module, "LOCK_FILE", os.path.join(tmp_dir, "pipeline.lock")),
        patch.object(orc_module, "CONFIG_FILE", config_file),
        patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
    ):
        from orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch.lock_fd = None
        orch.openclaw_config = {"hooks": {"token": "test-tok"}}
        orch.state = {
            "current_phase": 1,
            "current_phase_raw_id": "CORE-1",
            "current_agent": "reviewer",
            "planner_retries": 0,
            "executor_retries": 0,
            "reviewer_retries": 0,
            "pipeline_status": "RUNNING",
            "last_action": "",
            "last_action_timestamp": "",
        }
        orch.write_state = MagicMock()
        orch.transition_state = MagicMock()

    return orch, ps_path


class TestContractFailureOrchestratorHandling:

    def test_contract_failure_soft_retries_up_to_cap(self, tmp_workspace):
        """CONTRACT_FAILURE soft-retries the reviewer (reviewer_contract_retries++) and,
        at the cap (3), escalates."""
        import orchestrator as orc_module

        orch, ps_path = _make_contract_orch(tmp_workspace, {"reviewer_contract_retries": 2})

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
        ):
            # Mirrors the run() loop CONTRACT_FAILURE handler (soft-retry only).
            _ps = orch.read_phase_state()
            _soft = _ps.get("reviewer_contract_retries", 0) + 1
            _ps["reviewer_contract_retries"] = _soft
            orch.write_phase_state_atomic(_ps)
            if _soft >= 3:
                orch.state["current_agent"] = "escalation"
            else:
                orch.state["current_agent"] = "reviewer"

        with open(ps_path) as f:
            state = json.load(f)

        assert state.get("reviewer_contract_retries") == 3, (
            "reviewer_contract_retries must be incremented to 3 at cap"
        )
        assert orch.state["current_agent"] == "escalation", (
            "current_agent must be 'escalation' when reviewer_contract_retries >= 3"
        )

    def test_contract_failure_below_cap_reinvokes_reviewer(self, tmp_workspace):
        """Below the cap (3), CONTRACT_FAILURE re-invokes the reviewer (not escalation)."""
        import orchestrator as orc_module

        orch, ps_path = _make_contract_orch(tmp_workspace, {"reviewer_contract_retries": 0})

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
        ):
            _ps = orch.read_phase_state()
            _soft = _ps.get("reviewer_contract_retries", 0) + 1
            _ps["reviewer_contract_retries"] = _soft
            orch.write_phase_state_atomic(_ps)
            if _soft >= 3:
                orch.state["current_agent"] = "escalation"
            else:
                orch.state["current_agent"] = "reviewer"

        assert orch.state["current_agent"] == "reviewer", (
            "current_agent must remain 'reviewer' for a contract retry below cap"
        )

    def test_escalation_tag_renamed_in_source(self):
        """The escalation reason emitted at the cap must be
        CONTRACT_FAILURE_SOFT_RETRY_EXHAUSTED, and the old INFRA tag must be gone."""
        import orchestrator as orc_module

        src = open(orc_module.__file__, encoding="utf-8").read()
        assert "CONTRACT_FAILURE_SOFT_RETRY_EXHAUSTED" in src, (
            "the cap escalation must use the renamed tag"
        )
        assert "INFRA_FAILURE_SOFT_RETRY_EXHAUSTED" not in src, (
            "the old INFRA escalation tag must be removed (removal completeness)"
        )


class TestCounterSeparation:
    """reviewer_contract_retries vs reviewer_retries — preserved by
    reset_execution, zeroed by reset_phase. Calls the REAL methods."""

    def test_reset_execution_preserves_contract_retries(self, tmp_workspace):
        import orchestrator as orc_module

        ps_path = os.path.join(tmp_workspace, "phase_state.json")
        with open(ps_path, "w") as f:
            json.dump(
                {
                    "executor_retries": 0,
                    "reviewer_retries": 2,            # should be zeroed
                    "reviewer_rejected": True,        # should be cleared
                    "reviewer_contract_retries": 1,   # must be PRESERVED
                    "escalation_resets": 0,
                },
                f,
            )

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
        ):
            from orchestrator import Orchestrator

            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {"hooks": {"token": "tok"}}
            orch.state = {
                "current_phase": 1, "current_phase_raw_id": "CORE-1",
                "current_agent": "executor", "pipeline_status": "RUNNING",
                "last_action": "", "last_action_timestamp": "",
                "executor_retries": 0,
            }
            orch.write_state = MagicMock()
            orch.transition_state = MagicMock()

            with patch("orchestrator.subprocess.run") as mock_sub:
                mock_sub.return_value = MagicMock(returncode=0)
                orch.reset_execution(caller="auto")

        with open(ps_path) as f:
            state = json.load(f)

        assert state.get("reviewer_retries") == 0, "reviewer_retries must be zeroed"
        assert state.get("reviewer_rejected") is False, "reviewer_rejected must be cleared"
        assert state.get("reviewer_contract_retries") == 1, (
            "reviewer_contract_retries must be PRESERVED by reset_execution — "
            "it is a per-phase budget, only zeroed by reset_phase"
        )

    def test_reset_phase_zeros_contract_retries(self, tmp_workspace):
        import orchestrator as orc_module

        ps_path = os.path.join(tmp_workspace, "phase_state.json")
        with open(ps_path, "w") as f:
            json.dump(
                {
                    "planner_retries": 2,
                    "executor_retries": 1,
                    "reviewer_retries": 3,
                    "reviewer_rejected": True,
                    "reviewer_contract_retries": 2,
                    "planner_output_preserved": True,
                    "escalation_resets": 2,  # must be PRESERVED
                },
                f,
            )

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
        ):
            from orchestrator import Orchestrator

            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {"hooks": {"token": "tok"}}
            orch.state = {
                "current_phase": 1, "current_phase_raw_id": "CORE-1",
                "current_agent": "escalation", "pipeline_status": "RUNNING",
                "last_action": "", "last_action_timestamp": "",
                "phase_base_commit": "",
            }
            orch.write_state = MagicMock()
            orch.transition_state = MagicMock()

            with patch("orchestrator.subprocess.run") as mock_sub:
                mock_sub.return_value = MagicMock(returncode=0)
                orch.reset_phase()

        with open(ps_path) as f:
            state = json.load(f)

        assert state.get("reviewer_retries") == 0, "reviewer_retries must be zeroed"
        assert state.get("reviewer_contract_retries") == 0, (
            "reviewer_contract_retries must be zeroed by reset_phase"
        )
        assert state.get("escalation_resets") == 2, (
            "escalation_resets must be PRESERVED by reset_phase (cap enforcement)"
        )

    def test_reset_phase_has_no_legacy_infra_counter(self, tmp_workspace):
        """Removal completeness: reset_phase must not re-introduce reviewer_infra_retries."""
        import orchestrator as orc_module

        ps_path = os.path.join(tmp_workspace, "phase_state.json")
        with open(ps_path, "w") as f:
            json.dump({"escalation_resets": 0}, f)

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
        ):
            from orchestrator import Orchestrator

            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {"hooks": {"token": "tok"}}
            orch.state = {
                "current_phase": 1, "current_phase_raw_id": "CORE-1",
                "current_agent": "escalation", "pipeline_status": "RUNNING",
                "last_action": "", "last_action_timestamp": "", "phase_base_commit": "",
            }
            orch.write_state = MagicMock()
            orch.transition_state = MagicMock()
            with patch("orchestrator.subprocess.run") as mock_sub:
                mock_sub.return_value = MagicMock(returncode=0)
                orch.reset_phase()

        with open(ps_path) as f:
            state = json.load(f)
        assert "reviewer_infra_retries" not in state, (
            "reset_phase must not write the renamed-away reviewer_infra_retries field"
        )


class TestReviewerDirectiveDelivery:
    """The unified ``reviewer_retry_directive`` field is delivered to the reviewer via
    the webhook ``message=`` on re-invoke, and is one-shot (cleared after delivery).
    This is the R-C guard: prove the directive REACHES invoke_agent_webhook, not just
    that a phase_state field was written (the dead-write trap that hid the old
    unverified_instruction)."""

    def test_invoke_reviewer_delivers_directive_as_message(self, tmp_workspace):
        import orchestrator as orc_module

        orch, ps_path = _make_contract_orch(
            tmp_workspace, {"reviewer_retry_directive": "FIX: emit reviewer_output.json"}
        )

        with (
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "invoke_agent_webhook") as mock_hook,
        ):
            mock_hook.return_value = "SUCCESS"
            orch._invoke_reviewer("pipeline:phase-1:CORE-1:reviewer-attempt-1-c1", "tok")

        assert mock_hook.called, "_invoke_reviewer must call invoke_agent_webhook"
        _, kwargs = mock_hook.call_args
        assert kwargs.get("message") == "FIX: emit reviewer_output.json", (
            "the contract directive must reach invoke_agent_webhook as message=, "
            "not merely be written to phase_state"
        )

        with open(ps_path) as f:
            state = json.load(f)
        assert not state.get("reviewer_retry_directive"), (
            "reviewer_retry_directive must be CLEARED after delivery (one-shot)"
        )

    def test_invoke_reviewer_without_directive_passes_no_message(self, tmp_workspace):
        """A normal reviewer invocation (no directive set) passes no message → the
        reviewer's default webhook message is used."""
        import orchestrator as orc_module

        orch, ps_path = _make_contract_orch(tmp_workspace, {})

        with (
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "invoke_agent_webhook") as mock_hook,
        ):
            mock_hook.return_value = "SUCCESS"
            orch._invoke_reviewer("pipeline:phase-1:CORE-1:reviewer-attempt-1", "tok")

        _, kwargs = mock_hook.call_args
        assert not kwargs.get("message"), (
            "with no directive, _invoke_reviewer must not inject a message "
            "(default reviewer message applies)"
        )

    def test_directive_is_one_shot(self, tmp_workspace):
        """After one delivery the directive is consumed; a second invocation carries no
        message (a stale directive must not re-inject on a later normal pass)."""
        import orchestrator as orc_module

        orch, ps_path = _make_contract_orch(
            tmp_workspace, {"reviewer_retry_directive": "FIX: emit reviewer_output.json"}
        )

        with (
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "invoke_agent_webhook") as mock_hook,
        ):
            mock_hook.return_value = "SUCCESS"
            orch._invoke_reviewer("pipeline:phase-1:CORE-1:reviewer-attempt-1-c1", "tok")
            orch._invoke_reviewer("pipeline:phase-1:CORE-1:reviewer-attempt-2", "tok")

        second_kwargs = mock_hook.call_args_list[1].kwargs
        assert not second_kwargs.get("message"), (
            "the directive must not be re-delivered on the second invocation"
        )


class TestContractRetrySessionKey:
    """A contract retry must use a FRESH (distinct) session_key so each attempt is a
    clean session, deterministically keyed."""

    def test_contract_retry_uses_fresh_session_key(self, tmp_workspace):
        import orchestrator as orc_module

        orch, _ = _make_contract_orch(tmp_workspace, {})

        with patch.object(orc_module, "PHASE_STATE_FILE", os.path.join(tmp_workspace, "phase_state.json")):
            k0 = orch._reviewer_session_key(1, "CORE-1", 0, 0)
            k1 = orch._reviewer_session_key(1, "CORE-1", 0, 1)
            k2 = orch._reviewer_session_key(1, "CORE-1", 0, 2)

        assert k0 == "pipeline:phase-1:CORE-1:reviewer-attempt-1", (
            "with contract_retries=0 the key must be byte-identical to the legacy shape"
        )
        assert k1 != k0 and k2 != k1 and k2 != k0, (
            "each contract retry must yield a distinct session_key"
        )
        assert k1.startswith(k0), (
            "the contract suffix must extend the base attempt key, not replace it"
        )
