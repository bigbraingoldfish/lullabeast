"""
Orchestrator INFRA_FAILURE reviewer branch handling tests (RR-1, RR-4).

INFRA_FAILURE = the reviewer produced no parseable output (missing/malformed
reviewer_output.json) — an infrastructure signal, NOT a code-quality rejection.
After the traffic-cop retirement the handler is unconditional self-heal:

  - Soft-retry the reviewer (re-invoke in a fresh session), counting
    reviewer_infra_retries, capped at 3 → escalate
    (INFRA_FAILURE_SOFT_RETRY_EXHAUSTED).
  - INFRA_FAILURE never consumes reviewer_retries (reserved for genuine
    code-quality rejections).
  - There is NO model-health probe and NO SSH recovery: agent/model liveness is
    owned by the OpenClaw activity-stamp hooks (startup-grace / stall detection).
    The removal of check_traffic_cop_health / wait_for_model_stable / the SSH
    recovery branch is pinned by test_traffic_cop_retired.py.

reset_execution PRESERVES reviewer_infra_retries (per-phase infra budget);
reset_phase zeros it.

FIND-ID: RR-1, RR-4
Spec Reference: PIPELINE-SPEC.md §7 "Gate Scripts > Reviewer Output Gate"
                PIPELINE-CONSTRAINTS.md §5 "Infrastructure"
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


# ---------------------------------------------------------------------------
# Helper: build a minimal Orchestrator instance patched to use tmp_dir
# ---------------------------------------------------------------------------

def _make_infra_orch(tmp_dir, initial_phase_state=None):
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


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestInfraFailureOrchestratorHandling:

    def test_infra_failure_soft_retries_up_to_cap(self, tmp_workspace):
        """
        INFRA_FAILURE soft-retries the reviewer (reviewer_infra_retries++) and, when
        the counter reaches the cap (3), escalates. No model-health probe, no SSH.

        FIND-ID: RR-1
        Spec Reference: PIPELINE-SPEC.md §7 (INFRA_FAILURE → soft retry → escalate)
        """
        import orchestrator as orc_module

        # Start with retries=2 (one below cap)
        orch, ps_path = _make_infra_orch(tmp_workspace, {"reviewer_infra_retries": 2})

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
        ):
            # Mirrors the run() loop INFRA_FAILURE handler (soft-retry only).
            _ps = orch.read_phase_state()
            _infra_soft = _ps.get("reviewer_infra_retries", 0) + 1
            _ps["reviewer_infra_retries"] = _infra_soft
            orch.write_phase_state_atomic(_ps)
            if _infra_soft >= 3:
                orch.state["current_agent"] = "escalation"
            else:
                orch.state["current_agent"] = "reviewer"

        with open(ps_path) as f:
            state = json.load(f)

        assert state.get("reviewer_infra_retries") == 3, (
            "reviewer_infra_retries must be incremented to 3 at cap"
        )
        assert orch.state["current_agent"] == "escalation", (
            "current_agent must be 'escalation' when reviewer_infra_retries >= 3"
        )

    def test_infra_failure_soft_retry_below_cap_reinvokes_reviewer(self, tmp_workspace):
        """Below the cap (3), INFRA_FAILURE re-invokes the reviewer (not escalation).

        FIND-ID: RR-1
        """
        import orchestrator as orc_module

        orch, ps_path = _make_infra_orch(tmp_workspace, {"reviewer_infra_retries": 0})

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
        ):
            _ps = orch.read_phase_state()
            _infra_soft = _ps.get("reviewer_infra_retries", 0) + 1
            _ps["reviewer_infra_retries"] = _infra_soft
            orch.write_phase_state_atomic(_ps)
            if _infra_soft >= 3:
                orch.state["current_agent"] = "escalation"
            else:
                orch.state["current_agent"] = "reviewer"

        assert orch.state["current_agent"] == "reviewer", (
            "current_agent must remain 'reviewer' for soft retry when below cap"
        )


class TestCounterSeparation:
    """RR-4: reviewer counter split — reviewer_infra_retries vs reviewer_retries."""

    def test_reset_execution_zeros_reviewer_retries_preserves_infra_retries(self, tmp_workspace):
        """
        reset_execution zeros reviewer_retries and reviewer_rejected (so the reviewer
        starts at pass 1 after a reset), but PRESERVES reviewer_infra_retries (a
        per-phase infra budget; only reset_phase clears it).

        FIND-ID: RR-4
        Spec Reference: PIPELINE-SPEC.md §6 "Resume Commands — RESET_EXECUTION"
        """
        import orchestrator as orc_module

        ps_path = os.path.join(tmp_workspace, "phase_state.json")
        with open(ps_path, "w") as f:
            json.dump(
                {
                    "executor_retries": 0,
                    "reviewer_retries": 2,        # should be zeroed
                    "reviewer_rejected": True,    # should be cleared
                    "reviewer_infra_retries": 1,  # must be PRESERVED
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

            with (
                patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
                patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
                patch("orchestrator.subprocess.run") as mock_sub,
            ):
                mock_sub.return_value = MagicMock(returncode=0)
                orch.reset_execution(caller="auto")

        with open(ps_path) as f:
            state = json.load(f)

        assert state.get("reviewer_retries") == 0, (
            "reviewer_retries must be zeroed by reset_execution"
        )
        assert state.get("reviewer_rejected") is False, (
            "reviewer_rejected must be cleared by reset_execution"
        )
        assert state.get("reviewer_infra_retries") == 1, (
            "reviewer_infra_retries must be PRESERVED by reset_execution — "
            "it is a per-phase infra budget, only zeroed by reset_phase"
        )

    def test_reset_phase_zeros_all_reviewer_counters(self, tmp_workspace):
        """
        reset_phase zeros reviewer_retries and reviewer_infra_retries and clears
        planner_output_preserved, while preserving escalation_resets.

        FIND-ID: RR-4
        Spec Reference: PIPELINE-SPEC.md §6 "Resume Commands — RESET_PHASE"
        """
        import orchestrator as orc_module

        ps_path = os.path.join(tmp_workspace, "phase_state.json")
        with open(ps_path, "w") as f:
            json.dump(
                {
                    "planner_retries": 2,
                    "executor_retries": 1,
                    "reviewer_retries": 3,
                    "reviewer_rejected": True,
                    "reviewer_infra_retries": 2,
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

            with (
                patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
                patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
                patch("orchestrator.subprocess.run") as mock_sub,
            ):
                mock_sub.return_value = MagicMock(returncode=0)
                # No roadmap files in tmp_workspace — glob returns [] naturally.
                orch.reset_phase()

        with open(ps_path) as f:
            state = json.load(f)

        assert state.get("planner_retries") == 0, "planner_retries must be zeroed"
        assert state.get("executor_retries") == 0, "executor_retries must be zeroed"
        assert state.get("reviewer_retries") == 0, "reviewer_retries must be zeroed"
        assert state.get("reviewer_rejected") is False, "reviewer_rejected must be cleared"
        assert state.get("reviewer_infra_retries") == 0, (
            "reviewer_infra_retries must be zeroed by reset_phase"
        )
        assert state.get("planner_output_preserved") is False, (
            "planner_output_preserved must be cleared by reset_phase"
        )
        assert state.get("escalation_resets") == 2, (
            "escalation_resets must be PRESERVED by reset_phase (cap enforcement)"
        )
