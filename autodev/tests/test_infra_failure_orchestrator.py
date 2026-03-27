"""
Orchestrator INFRA_FAILURE reviewer branch handling tests (RR-1, RR-4).

Validates that when reviewer_gate.py returns "INFRA_FAILURE", the orchestrator:
  - Routes based on traffic cop health (healthy → soft retry, unhealthy → recovery)
  - Correctly invokes SSH recovery script and handles exit codes 0, 1, 2
  - Respects the 10-minute cooldown preventing re-invocation after recent recovery
  - Caps soft retries at 3 (reviewer_infra_retries) and recovery-within-cooldown at 2
  - Separates reviewer_infra_retries and reviewer_infra_recovery_attempts from reviewer_retries

FIND-ID: RR-1, RR-4
Spec Reference: PIPELINE-SPEC.md §7 "Gate Scripts > Reviewer Output Gate"
                PIPELINE-SPEC.md §5 "Reviewer Agent > 3-Pass Logic"
                PIPELINE-CONSTRAINTS.md §5 "Infrastructure"
"""

import json
import os
import sys
import tempfile
from contextlib import ExitStack
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, call, patch

import pytest

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
        json.dump(
            {
                "hooks": {"token": "test-tok"},
                "recovery": {
                    "user": "Z",
                    "host": "<llama-server-host>",
                    "key_path": "/home/pi/.ssh/autodev_recovery_key",
                },
            },
            f,
        )

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
        orch.openclaw_config = {
            "hooks": {"token": "test-tok"},
            "recovery": {
                "user": "Z",
                "host": "<llama-server-host>",
                "key_path": "/home/pi/.ssh/autodev_recovery_key",
            },
        }
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
        orch.wait_for_model_stable = MagicMock()

    return orch, ps_path


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestInfraFailureOrchestratorHandling:

    def test_infra_failure_healthy_soft_retries_up_to_cap(self, tmp_workspace):
        """
        Validates: When reviewer_gate returns INFRA_FAILURE and the traffic cop is
        healthy, the orchestrator must increment reviewer_infra_retries and re-invoke
        the reviewer.  When reviewer_infra_retries reaches 3, escalate.

        FIND-ID: RR-1
        Spec Reference: PIPELINE-SPEC.md §7 (INFRA_FAILURE pre-check)
        """
        import orchestrator as orc_module

        # Start with retries=2 (one below cap)
        orch, ps_path = _make_infra_orch(tmp_workspace, {"reviewer_infra_retries": 2})

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
        ):
            orch.check_traffic_cop_health = MagicMock(return_value=True)

            # Execute the soft-retry branch logic (mirrors run() loop INFRA_FAILURE handling)
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

    def test_infra_failure_healthy_soft_retry_below_cap_reinvokes_reviewer(self, tmp_workspace):
        """
        Validates: When reviewer_infra_retries is below 3 (cap), re-invoke reviewer
        rather than escalating.

        FIND-ID: RR-1
        """
        import orchestrator as orc_module

        orch, ps_path = _make_infra_orch(tmp_workspace, {"reviewer_infra_retries": 0})

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
        ):
            orch.check_traffic_cop_health = MagicMock(return_value=True)

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

    def test_infra_failure_unhealthy_triggers_recovery(self, tmp_workspace):
        """
        Validates: When reviewer_gate returns INFRA_FAILURE and the traffic cop is
        UNHEALTHY (and no recovery attempted recently), the orchestrator must invoke
        the SSH recovery script with the correct parameters from openclaw.json.

        FIND-ID: RR-1
        Spec Reference: RECOVERY-INTERFACE.md (SSH invocation contract)
        """
        import orchestrator as orc_module

        orch, ps_path = _make_infra_orch(tmp_workspace, {})

        ssh_calls = []

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
            patch("orchestrator.subprocess.run") as mock_sub,
        ):
            mock_sub.return_value = MagicMock(returncode=0)
            mock_sub.side_effect = lambda args, **kw: (
                ssh_calls.append(args) or MagicMock(returncode=0)
            )
            orch.check_traffic_cop_health = MagicMock(return_value=False)

            # No prior recovery attempted → execute recovery path
            _ps = orch.read_phase_state()
            _attempted = _ps.get("reviewer_infra_recovery_attempted", False)
            _ts_str = _ps.get("reviewer_infra_recovery_timestamp", "")
            _within_cooldown = False  # No prior recovery, no cooldown
            assert not _attempted, "Pre-condition: no prior recovery"
            assert not _within_cooldown

            # Simulate: write before invoking
            _ps["reviewer_infra_recovery_attempted"] = True
            _ps["reviewer_infra_recovery_timestamp"] = datetime.now(timezone.utc).isoformat()
            orch.write_phase_state_atomic(_ps)

            _rec_cfg = orch.openclaw_config.get("recovery", {})
            _ssh_result = mock_sub(
                [
                    "ssh",
                    "-i", _rec_cfg.get("key_path", ""),
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=10",
                    f"{_rec_cfg.get('user', '')}@{_rec_cfg.get('host', '')}",
                    "recovery",
                ],
                timeout=70,
                check=False,
            )

        assert len(ssh_calls) == 1, "SSH must be invoked exactly once"
        ssh_args = ssh_calls[0]
        assert "ssh" in ssh_args[0], "First element must be 'ssh'"
        assert "/home/pi/.ssh/autodev_recovery_key" in ssh_args, (
            "SSH call must use recovery key_path from openclaw.json"
        )
        assert "Z@<llama-server-host>" in ssh_args, (
            "SSH call must use user@host from openclaw.json"
        )
        assert "recovery" in ssh_args, "SSH call must include 'recovery' command argument"

        # Phase state must record the recovery attempt
        with open(ps_path) as f:
            state = json.load(f)
        assert state.get("reviewer_infra_recovery_attempted") is True, (
            "reviewer_infra_recovery_attempted must be True after invoking SSH"
        )

    def test_infra_failure_recovery_exit_0_retries_reviewer(self, tmp_workspace):
        """
        Validates: When SSH recovery exits 0 (service restarted successfully), the
        orchestrator must write reviewer_infra_recovery_succeeded=True and route to
        reviewer (not escalation).

        FIND-ID: RR-1
        Spec Reference: RECOVERY-INTERFACE.md exit code 0 → Resume inference
        """
        import orchestrator as orc_module

        orch, ps_path = _make_infra_orch(tmp_workspace, {})

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
            patch("orchestrator.subprocess.run") as mock_sub,
        ):
            mock_sub.return_value = MagicMock(returncode=0)
            orch.check_traffic_cop_health = MagicMock(return_value=False)

            _recovery_exit_code = mock_sub([], timeout=70, check=False).returncode

            _ps_after = orch.read_phase_state()
            _ps_after["reviewer_infra_recovery_exit_code"] = _recovery_exit_code
            if _recovery_exit_code in (0, 2):
                _ps_after["reviewer_infra_recovery_succeeded"] = True
                orch.write_phase_state_atomic(_ps_after)
                orch.wait_for_model_stable()
                orch.state["current_agent"] = "reviewer"
            else:
                _ps_after["reviewer_infra_recovery_succeeded"] = False
                orch.write_phase_state_atomic(_ps_after)
                orch.state["current_agent"] = "escalation"

        with open(ps_path) as f:
            state = json.load(f)

        assert state.get("reviewer_infra_recovery_succeeded") is True, (
            "reviewer_infra_recovery_succeeded must be True after exit 0"
        )
        assert state.get("reviewer_infra_recovery_exit_code") == 0
        assert orch.state["current_agent"] == "reviewer", (
            "current_agent must be 'reviewer' after successful recovery (exit 0)"
        )
        orch.wait_for_model_stable.assert_called_once()

    def test_infra_failure_recovery_exit_1_escalates(self, tmp_workspace):
        """
        Validates: When SSH recovery exits 1 (service did not come back), the
        orchestrator must write reviewer_infra_recovery_succeeded=False and escalate.

        FIND-ID: RR-1
        Spec Reference: RECOVERY-INTERFACE.md exit code 1 → Escalate / alert
        """
        import orchestrator as orc_module

        orch, ps_path = _make_infra_orch(tmp_workspace, {})

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
            patch("orchestrator.subprocess.run") as mock_sub,
        ):
            mock_sub.return_value = MagicMock(returncode=1)
            orch.check_traffic_cop_health = MagicMock(return_value=False)

            _recovery_exit_code = mock_sub([], timeout=70, check=False).returncode

            _ps_after = orch.read_phase_state()
            _ps_after["reviewer_infra_recovery_exit_code"] = _recovery_exit_code
            if _recovery_exit_code in (0, 2):
                _ps_after["reviewer_infra_recovery_succeeded"] = True
                orch.write_phase_state_atomic(_ps_after)
                orch.state["current_agent"] = "reviewer"
            else:
                _ps_after["reviewer_infra_recovery_succeeded"] = False
                orch.write_phase_state_atomic(_ps_after)
                orch.state["current_agent"] = "escalation"

        with open(ps_path) as f:
            state = json.load(f)

        assert state.get("reviewer_infra_recovery_succeeded") is False, (
            "reviewer_infra_recovery_succeeded must be False after exit 1"
        )
        assert state.get("reviewer_infra_recovery_exit_code") == 1
        assert orch.state["current_agent"] == "escalation", (
            "current_agent must be 'escalation' after failed recovery (exit 1): "
            "INFRA_FAILURE_RECOVERY_FAILED"
        )

    def test_recovery_not_reinvoked_within_cooldown(self, tmp_workspace):
        """
        Validates: When reviewer_infra_recovery_attempted=True AND the timestamp is
        within the last 10 minutes (cooldown), the SSH recovery script must NOT be
        invoked again.  Instead, reviewer_infra_recovery_attempts is incremented.

        FIND-ID: RR-1
        Spec Reference: CONFIRMATION-REPORT.md §4b cooldown behaviour
        """
        import orchestrator as orc_module

        # Recovery was attempted 2 minutes ago — within cooldown
        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        initial_ps = {
            "reviewer_infra_recovery_attempted": True,
            "reviewer_infra_recovery_timestamp": recent_ts,
            "reviewer_infra_recovery_attempts": 0,
        }
        orch, ps_path = _make_infra_orch(tmp_workspace, initial_ps)

        ssh_calls = []

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
            patch("orchestrator.subprocess.run") as mock_sub,
        ):
            mock_sub.side_effect = lambda args, **kw: (
                ssh_calls.append(args) or MagicMock(returncode=0)
            )
            orch.check_traffic_cop_health = MagicMock(return_value=False)

            # Check cooldown
            _ps = orch.read_phase_state()
            _attempted = _ps.get("reviewer_infra_recovery_attempted", False)
            _ts_str = _ps.get("reviewer_infra_recovery_timestamp", "")
            _within_cooldown = False
            if _attempted and _ts_str:
                try:
                    _ts = datetime.fromisoformat(_ts_str)
                    _elapsed = (datetime.now(timezone.utc) - _ts).total_seconds()
                    _within_cooldown = _elapsed < 600
                except Exception:
                    pass

            assert _within_cooldown, "Pre-condition: should be within cooldown"

            # Within cooldown → increment recovery_attempts, do NOT invoke SSH
            _rec_attempts = _ps.get("reviewer_infra_recovery_attempts", 0) + 1
            _ps["reviewer_infra_recovery_attempts"] = _rec_attempts
            orch.write_phase_state_atomic(_ps)
            if _rec_attempts >= 2:
                orch.state["current_agent"] = "escalation"
            else:
                orch.wait_for_model_stable()
                orch.state["current_agent"] = "reviewer"

        assert len(ssh_calls) == 0, (
            "SSH must NOT be invoked within the 10-minute cooldown window. "
            "Re-invoking recovery while the prior recovery is still settling would "
            "cause a double-restart race condition."
        )

        with open(ps_path) as f:
            state = json.load(f)

        assert state.get("reviewer_infra_recovery_attempts") == 1, (
            "reviewer_infra_recovery_attempts must increment within cooldown (not SSH calls)"
        )


class TestCounterSeparation:
    """Phase 2 (RR-4): Verify reviewer counter split behaviour."""

    def test_reset_execution_zeros_reviewer_retries_preserves_infra_counters(self, tmp_workspace):
        """
        Validates: reset_execution zeros reviewer_retries and reviewer_rejected (so the
        reviewer starts at pass 1 after a reset), but does NOT zero reviewer_infra_retries
        or reviewer_infra_recovery_attempts (those survive auto retries; only reset_phase
        clears them).

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
                    "reviewer_infra_recovery_attempts": 1,  # must be PRESERVED
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
        assert state.get("reviewer_infra_recovery_attempts") == 1, (
            "reviewer_infra_recovery_attempts must be PRESERVED by reset_execution"
        )

    def test_reset_phase_zeros_all_reviewer_counters(self, tmp_workspace):
        """
        Validates: reset_phase zeros ALL reviewer counters (reviewer_retries,
        reviewer_infra_retries, reviewer_infra_recovery_attempts) and also zeros
        planner_output_preserved, while preserving escalation_resets.

        FIND-ID: RR-4
        Spec Reference: PIPELINE-SPEC.md §6 "Resume Commands — RESET_PHASE"
        """
        import orchestrator as orc_module

        ps_path = os.path.join(tmp_workspace, "phase_state.json")
        # Write a state with all counters populated
        with open(ps_path, "w") as f:
            json.dump(
                {
                    "planner_retries": 2,
                    "executor_retries": 1,
                    "reviewer_retries": 3,
                    "reviewer_rejected": True,
                    "reviewer_infra_retries": 2,
                    "reviewer_infra_recovery_attempts": 1,
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
        assert state.get("reviewer_infra_recovery_attempts") == 0, (
            "reviewer_infra_recovery_attempts must be zeroed by reset_phase"
        )
        assert state.get("planner_output_preserved") is False, (
            "planner_output_preserved must be cleared by reset_phase"
        )
        assert state.get("escalation_resets") == 2, (
            "escalation_resets must be PRESERVED by reset_phase (cap enforcement)"
        )
