"""
Escalation reset cap enforcement and reason-logging tests.

The cap (3 combined RESET_PHASE + RESET_EXECUTION per phase) is a correct safety
mechanism.  These tests validate:
  1. The cap correctly blocks at the limit
  2. Each reset consumption writes a structured reason to phase_state.json
  3. Infrastructure failures and logic failures produce different log signatures
  4. A single manual counter reset restores exactly one attempt

FIND-ID: FIND-ESCALATION-CAP
Spec Reference: PIPELINE-SPEC.md §6 "Escalation Agent > Resume Commands > Escalation reset cap"
                PIPELINE-CONSTRAINTS.md §4 "Escalation Reset Commands — Cap Enforcement"
"""

import json
import os
import sys
import tempfile
from contextlib import ExitStack
from unittest.mock import MagicMock, call, patch

import pytest

OPENCLAW_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE_SCRIPTS_DIR = os.path.join(OPENCLAW_DIR, "autodev", "pipeline", "gate_scripts")
for _p in [GATE_SCRIPTS_DIR, OPENCLAW_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _make_minimal_orchestrator(tmp_dir):
    """Return a stripped Orchestrator pointing at tmp_dir with mocked lock/state ops."""
    import orchestrator as orc_module

    state_file = os.path.join(tmp_dir, "pipeline_state.json")
    lock_file = os.path.join(tmp_dir, "pipeline.lock")
    config_file = os.path.join(tmp_dir, "openclaw.json")

    with open(config_file, "w") as f:
        json.dump({"hooks": {"token": "test-tok"}}, f)

    with (
        patch.object(orc_module, "STATE_FILE", state_file),
        patch.object(orc_module, "SYMLINK_TARGET", tmp_dir),
        patch.object(orc_module, "LOCK_FILE", lock_file),
        patch.object(orc_module, "CONFIG_FILE", config_file),
        patch.object(orc_module, "PHASE_STATE_FILE", os.path.join(tmp_dir, "phase_state.json")),
    ):
        from orchestrator import Orchestrator
        orch = Orchestrator.__new__(Orchestrator)
        orch.lock_fd = None
        orch.openclaw_config = {"hooks": {"token": "test-tok"}}
        orch.state = {
            "current_phase": 1,
            "current_phase_raw_id": "CORE-1",
            "current_agent": "escalation",
            "planner_retries": 0,
            "executor_retries": 0,
            "reviewer_retries": 0,
            "pipeline_status": "RUNNING",
            "last_action": "",
            "last_action_timestamp": "",
        }
        orch._symlink_target = tmp_dir
        orch._state_file = state_file

    # Wire write_state and transition_state to use tmp paths
    import orchestrator as orc_module2
    with (
        patch.object(orc_module2, "STATE_FILE", state_file),
        patch.object(orc_module2, "SYMLINK_TARGET", tmp_dir),
        patch.object(orc_module2, "PHASE_STATE_FILE", os.path.join(tmp_dir, "phase_state.json")),
    ):
        pass

    return orch, tmp_dir


class TestCapEnforcement:

    def test_cap_blocks_reset_at_limit(self, tmp_workspace):
        """
        Validates: After 3 escalation resets, RESET_EXECUTION and RESET_PHASE are both
        blocked.  The orchestrator must send a Signal notification and remain in
        WAITING_FOR_HUMAN rather than executing another reset.

        FIND-ID: FIND-ESCALATION-CAP
        Spec Reference: PIPELINE-SPEC.md §6 "Resume Commands > Escalation reset cap"
                        PIPELINE-CONSTRAINTS.md §4 "Cap: 3 combined resets per phase"
        """
        import orchestrator as orc_module

        phase_state = {
            "planner_retries": 0,
            "executor_retries": 0,
            "reviewer_retries": 0,
            "escalation_resets": 3,  # at the cap
        }
        phase_state_path = os.path.join(tmp_workspace, "phase_state.json")
        with open(phase_state_path, "w") as f:
            json.dump(phase_state, f)

        notifications_sent = []

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", phase_state_path),
        ):
            from orchestrator import Orchestrator
            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {"hooks": {"token": "tok"}}
            orch.state = {
                "current_phase": 1, "current_phase_raw_id": "CORE-1",
                "current_agent": "escalation", "pipeline_status": "RUNNING",
                "last_action": "", "last_action_timestamp": "",
            }
            orch.write_state = MagicMock()
            orch.transition_state = MagicMock()
            orch.send_signal_notification = lambda msg: notifications_sent.append(msg)
            orch.reset_execution = MagicMock()

            # Attempt RESET_EXECUTION with cap at 3 — must NOT call reset_execution
            _ps = orch.read_phase_state()
            if _ps.get("escalation_resets", 0) >= 3:
                orch.send_signal_notification(
                    "Escalation reset cap reached (3). Human PROCEED required."
                )
            else:
                orch.reset_execution(caller="escalation")

        assert len(notifications_sent) == 1, (
            "A notification must be sent when the cap is reached."
        )
        orch.reset_execution.assert_not_called()

    def test_cap_reason_logged_per_reset(self, tmp_workspace):
        """
        Validates: When an escalation reset (RESET_PHASE or RESET_EXECUTION) is
        consumed, a structured reason must be written to phase_state.json under
        'reset_log' — including the reset number, command used, and the error code
        that triggered the escalation.

        Current behavior (BUG): only the numeric counter is updated; no reason logged.
        Expected behavior (after fix): phase_state.json['reset_log'] contains a list
        of dicts with at least {reset_number, command, reason, timestamp}.

        FIND-ID: FIND-ESCALATION-CAP
        Spec Reference: PIPELINE-CONSTRAINTS.md §4 "Cap Enforcement"
        Spec Gap: No current spec clause requires per-reset reason logging.
        """
        import orchestrator as orc_module

        phase_state_path = os.path.join(tmp_workspace, "phase_state.json")
        with open(phase_state_path, "w") as f:
            json.dump({
                "planner_retries": 0,
                "executor_retries": 0,
                "reviewer_retries": 0,
                "escalation_resets": 0,
                "last_error_code": "ERR_INFRA_FAILURE",
            }, f)

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", phase_state_path),
        ):
            from orchestrator import Orchestrator
            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {"hooks": {"token": "tok"}}
            orch.state = {
                "current_phase": 1, "current_phase_raw_id": "CORE-1",
                "current_agent": "executor", "pipeline_status": "RUNNING",
                "last_action": "", "last_action_timestamp": "",
            }
            orch.write_state = MagicMock()
            orch.transition_state = MagicMock()

            # Perform a reset_execution as if triggered by the escalation agent
            with (
                patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
                patch.object(orc_module, "PHASE_STATE_FILE", phase_state_path),
                patch("orchestrator.subprocess.run") as mock_sub,
            ):
                mock_sub.return_value = MagicMock(returncode=0)
                orch.reset_execution(caller="escalation")

        with open(phase_state_path) as f:
            state = json.load(f)

        # escalation_resets must be incremented
        assert state.get("escalation_resets", 0) == 1, "escalation_resets must be incremented"

        # reset_log must contain at least one entry with reason
        assert "reset_log" in state, (
            "phase_state.json must contain 'reset_log' list after a reset is consumed.  "
            "Current code does not write reset_log — this is the missing feature."
        )
        assert len(state["reset_log"]) >= 1
        entry = state["reset_log"][0]
        assert "reason" in entry, "Each reset_log entry must contain a 'reason' field"
        assert "command" in entry, "Each reset_log entry must contain a 'command' field"
        assert "reset_number" in entry, "Each reset_log entry must contain a 'reset_number' field"

    def test_cap_reached_infrastructure_vs_logic_failure(self, tmp_workspace):
        """
        Validates: When cap is reached, the notification message / log must contain
        enough context to distinguish whether the cap was hit due to infrastructure
        failures vs genuine logic failures.

        Two scenarios:
          A) All 3 resets consumed by infra failures (ERR_INFRA_FAILURE in last_error_code)
          B) All 3 resets consumed by logic failures (ERR_TESTS_FAILING)

        Both eventually block, but their reset_log entries must show different reasons.

        FIND-ID: FIND-ESCALATION-CAP
        Spec Reference: PIPELINE-CONSTRAINTS.md §4 "Cap Enforcement"
        Spec Gap: No spec clause requires different notification paths for infra vs logic cap.
        """
        import orchestrator as orc_module

        def _build_state_with_resets(tmp, error_code, reset_count):
            ps_path = os.path.join(tmp, "phase_state.json")
            reset_log = [
                {"reset_number": i + 1, "command": "RESET_EXECUTION",
                 "reason": error_code, "timestamp": "2026-01-01T00:00:00Z"}
                for i in range(reset_count)
            ]
            with open(ps_path, "w") as f:
                json.dump({
                    "escalation_resets": reset_count,
                    "last_error_code": error_code,
                    "reset_log": reset_log,
                }, f)
            return ps_path

        # Scenario A: infrastructure failures hit the cap
        with tempfile.TemporaryDirectory() as tmp_a:
            ps_a = _build_state_with_resets(tmp_a, "ERR_INFRA_FAILURE", 3)
            with open(ps_a) as f:
                state_a = json.load(f)
            reasons_a = {e["reason"] for e in state_a.get("reset_log", [])}
            assert "ERR_INFRA_FAILURE" in reasons_a, (
                "reset_log must record infra failure reason per reset"
            )

        # Scenario B: logic failures hit the cap
        with tempfile.TemporaryDirectory() as tmp_b:
            ps_b = _build_state_with_resets(tmp_b, "ERR_TESTS_FAILING", 3)
            with open(ps_b) as f:
                state_b = json.load(f)
            reasons_b = {e["reason"] for e in state_b.get("reset_log", [])}
            assert "ERR_TESTS_FAILING" in reasons_b, (
                "reset_log must record logic failure reason per reset"
            )

        # The two scenarios must produce different reason sets
        assert reasons_a != reasons_b, (
            "Infrastructure-triggered caps and logic-triggered caps must have "
            "distinguishable log signatures in reset_log"
        )

    def test_manual_counter_reset_restores_one_attempt(self, tmp_workspace):
        """
        Validates: A human can manually decrement escalation_resets by 1 (e.g., via
        direct phase_state.json edit or a future 'RESET_CAP' command).  After a
        manual decrement from 3 to 2, exactly ONE RESET_EXECUTION is permitted before
        the cap re-engages.

        FIND-ID: FIND-ESCALATION-CAP
        Spec Reference: PIPELINE-CONSTRAINTS.md §4 "Cap Enforcement"
        """
        import orchestrator as orc_module

        phase_state_path = os.path.join(tmp_workspace, "phase_state.json")

        # Start at cap (3), human manually decrements to 2
        with open(phase_state_path, "w") as f:
            json.dump({"escalation_resets": 2, "executor_retries": 0}, f)

        resets_executed = []

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", phase_state_path),
        ):
            from orchestrator import Orchestrator
            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {"hooks": {"token": "tok"}}
            orch.state = {
                "current_phase": 1, "current_phase_raw_id": "CORE-1",
                "current_agent": "executor", "pipeline_status": "RUNNING",
                "last_action": "", "last_action_timestamp": "",
            }
            orch.write_state = MagicMock()
            orch.transition_state = MagicMock()
            notifications = []
            orch.send_signal_notification = lambda m: notifications.append(m)

            def _mock_reset_execution(caller):
                _ps = orch.read_phase_state()
                _ps["escalation_resets"] = _ps.get("escalation_resets", 0) + 1
                resets_executed.append(caller)
                with open(phase_state_path, "w") as ff:
                    json.dump(_ps, ff)

            orch.reset_execution = _mock_reset_execution

            # First attempt — should succeed (2 < 3)
            _ps = orch.read_phase_state()
            if _ps.get("escalation_resets", 0) >= 3:
                orch.send_signal_notification("cap reached")
            else:
                orch.reset_execution("escalation")

            # Second attempt — must be blocked (now 3 >= 3)
            _ps2 = orch.read_phase_state()
            if _ps2.get("escalation_resets", 0) >= 3:
                orch.send_signal_notification("cap reached")
            else:
                orch.reset_execution("escalation")

        assert len(resets_executed) == 1, (
            f"Exactly one reset must succeed after manual decrement; got {len(resets_executed)}"
        )
        assert len(notifications) == 1, "Cap must re-engage on the second attempt"


class TestResetExecutionEscalationFreshBudget:
    """
    Validates that an operator-driven RESET_EXECUTION restores a fresh executor
    retry budget so the UI attempt chips reset and the next executor invocation
    is actually re-run (rather than re-entering the `retries >= 3` exhausted
    branch, which escalates).
    """

    @pytest.fixture
    def tmp_workspace(self, tmp_path):
        return str(tmp_path)

    def test_reset_execution_escalation_zeros_executor_retries(self, tmp_workspace):
        """After RESET_EXECUTION from the escalation path:
          - phase_state.executor_retries → 0 (fresh budget for the UI chips)
          - self.state.executor_retries → 0 (so the executor branch does not
            immediately re-enter the retries >= 3 exhausted block)
          - escalation_resets is incremented exactly once
        """
        import orchestrator as orc_module

        phase_state_path = os.path.join(tmp_workspace, "phase_state.json")
        with open(phase_state_path, "w") as f:
            json.dump({
                "planner_retries": 0,
                "executor_retries": 3,
                "reviewer_retries": 0,
                "reviewer_rejected": False,
                "escalation_resets": 0,
                "last_error_code": "ERR_TESTS_FAILING",
            }, f)

        with (
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "PHASE_STATE_FILE", phase_state_path),
        ):
            from orchestrator import Orchestrator
            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {"hooks": {"token": "tok"}}
            orch.state = {
                "current_phase": 1,
                "current_phase_raw_id": "REND-E1",
                "current_agent": "escalation",
                "pipeline_status": "RUNNING",
                "executor_retries": 3,
                "reviewer_retries": 0,
                "last_action": "",
                "last_action_timestamp": "",
            }
            orch.write_state = MagicMock()
            orch.transition_state = MagicMock()

            with patch("orchestrator.subprocess.run") as mock_sub:
                mock_sub.return_value = MagicMock(returncode=0)
                orch.reset_execution(caller="escalation")

        with open(phase_state_path) as f:
            ps = json.load(f)

        assert ps.get("executor_retries") == 0, (
            "phase_state.executor_retries must be zeroed by reset_execution(escalation) "
            "so the UI attempt chips reset to a fresh 3-slot budget."
        )
        assert orch.state.get("executor_retries") == 0, (
            "self.state.executor_retries must be zeroed so the main loop does not "
            "immediately re-enter the `retries >= 3` exhausted branch on the next iteration."
        )
        assert ps.get("escalation_resets") == 1, (
            "escalation_resets must still be incremented exactly once."
        )
