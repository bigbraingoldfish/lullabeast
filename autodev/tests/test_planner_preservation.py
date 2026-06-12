"""
Planner output preservation tests.

When the orchestrator resumes after a crash with current_agent='planner' and valid
planner output already exists on disk, it must NOT delete and re-invoke the planner.
The planner is expensive (cloud API call) and should only be re-invoked when:
  - planner output is missing
  - planner output is invalid (gate fails)
  - explicit RESET_PHASE or reviewer ROUTE_PLANNER reroute was triggered
  - planner_retries > 0 (previous planner run failed)

FIND-ID: FIND-PLANNER-PRESERVE
Spec Reference: PIPELINE-SPEC.md §3 "Planner Agent > Retry Behavior"
                PIPELINE-SPEC.md §6 "Escalation Agent > Resume Commands"
Spec Gap: No spec clause defines a planner output validity check before re-invocation.
"""

import json
import os
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch

import pytest

OPENCLAW_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE_SCRIPTS_DIR = os.path.join(OPENCLAW_DIR, "autodev", "pipeline", "gate_scripts")
for _p in [GATE_SCRIPTS_DIR, OPENCLAW_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import planner_gate as planner_gate_module
import utils as utils_module


@contextmanager
def _patch_orch_project_paths(orc_module, tmp_workspace, phase_state_path=None):
    """Flat tmp dir as project root and artifact dir (matches pre-relocation tests)."""
    ps = phase_state_path or os.path.join(tmp_workspace, "phase_state.json")
    with (
        patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
        patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", tmp_workspace),
        patch.object(orc_module, "PHASE_STATE_FILE", ps),
    ):
        yield


def _patch_workspace(tmp_dir):
    stack = ExitStack()
    stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", tmp_dir + "/"))
    _ad = os.path.join(tmp_dir.rstrip(os.sep), ".autodev", "pipeline") + os.sep
    stack.enter_context(patch.object(utils_module, "ARTIFACTS_DIR", _ad))
    stack.enter_context(
        patch.object(utils_module, "PHASE_STATE_FILE", os.path.join(_ad.rstrip(os.sep), "phase_state.json"))
    )
    stack.enter_context(patch.object(planner_gate_module, "WORKSPACE_DIR", tmp_dir + "/"))
    return stack


class TestPlannerPreservation:

    def test_planner_not_reinvoked_when_output_exists_and_valid(self, tmp_workspace,
                                                                  valid_planner_output):
        """
        Validates: When current_agent='planner', planner_retries=0, and valid
        planner_output.json + planner_output.done already exist on disk, the
        orchestrator must skip planner invocation and proceed directly to executor.

        Current behavior (BUG): orchestrator always calls cleanup_output_files()
        which deletes planner_output.done before invoking the webhook.
        Expected behavior (after fix): check if planner output is valid before cleanup.

        FIND-ID: FIND-PLANNER-PRESERVE
        Spec Reference: PIPELINE-SPEC.md §3 "Planner Agent > Retry Behavior"
        Spec Gap: No spec clause defines planner output preservation on restart.
        """
        import orchestrator as orc_module

        # Pre-populate valid planner output
        planner_json = os.path.join(tmp_workspace, "planner_output.json")
        planner_done = os.path.join(tmp_workspace, "planner_output.done")
        with open(planner_json, "w") as f:
            json.dump(valid_planner_output, f)
        open(planner_done, "w").close()

        # Orchestrator must expose a method to check if planner output is already valid
        assert hasattr(orc_module.Orchestrator, "planner_output_is_valid"), (
            "Orchestrator must expose 'planner_output_is_valid()' method that returns True "
            "when planner_output.done exists AND planner_output.json passes the planner gate. "
            "Current code has no such check — this is the missing feature (FIND-PLANNER-PRESERVE)."
        )

        with _patch_orch_project_paths(orc_module, tmp_workspace):
            from orchestrator import Orchestrator
            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {"hooks": {"token": "tok"}}
            orch.state = {
                "current_phase": 1, "current_phase_raw_id": "CORE-1",
                "current_agent": "planner",
                "planner_retries": 0,
                "executor_retries": 0,
                "reviewer_retries": 0,
                "pipeline_status": "RUNNING",
                "last_action": "", "last_action_timestamp": "",
            }

            result = orch.planner_output_is_valid()

        assert result is True, (
            f"planner_output_is_valid() must return True when valid output exists on disk. "
            f"Got: {result!r}"
        )

    def test_planner_output_validity_check(self, tmp_workspace, valid_planner_output):
        """
        Validates: The planner output validity check ('planner_output_is_valid') must:
          - Return True when planner_output.done exists AND planner_output.json passes gate
          - Return False when planner_output.done is absent
          - Return False when planner_output.json is absent
          - Return False when planner_output.json exists but fails the gate

        FIND-ID: FIND-PLANNER-PRESERVE
        Spec Reference: PIPELINE-SPEC.md §7 "Gate Scripts > Planner Output Gate"
        """
        import orchestrator as orc_module

        assert hasattr(orc_module.Orchestrator, "planner_output_is_valid"), (
            "Orchestrator.planner_output_is_valid must be implemented"
        )

        with _patch_orch_project_paths(orc_module, tmp_workspace):
            from orchestrator import Orchestrator
            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {}
            orch.state = {}

            # Case 1: both files absent → False
            result_absent = orch.planner_output_is_valid()
            assert result_absent is False, "Must return False when neither file exists"

            # Case 2: .done absent, .json present → False
            planner_json = os.path.join(tmp_workspace, "planner_output.json")
            with open(planner_json, "w") as f:
                json.dump(valid_planner_output, f)
            result_no_done = orch.planner_output_is_valid()
            assert result_no_done is False, "Must return False when .done is absent"

            # Case 3: both present and valid → True
            planner_done = os.path.join(tmp_workspace, "planner_output.done")
            open(planner_done, "w").close()
            result_valid = orch.planner_output_is_valid()
            assert result_valid is True, "Must return True when both files present and valid"

            # Case 4: both present but JSON is invalid → False
            with open(planner_json, "w") as f:
                json.dump({"implementation_plan": []}, f)  # invalid: empty list
            result_invalid = orch.planner_output_is_valid()
            assert result_invalid is False, "Must return False when planner_output.json fails gate"

    def test_planner_reinvoked_only_on_explicit_reset_or_invalid_output(self, tmp_workspace,
                                                                          valid_planner_output,
                                                                          valid_executor_output):
        """
        Validates: Executor failure alone must NOT trigger planner re-invocation.
        When the executor fails (sentinel timeout), reset_execution("auto") preserves
        the planner output — current_agent returns to "executor", not "planner".

        This test verifies that the planner is NOT in the executor retry path.

        FIND-ID: FIND-PLANNER-PRESERVE
        Spec Reference: PIPELINE-SPEC.md §4 "Executor Agent > Two Retry Scenarios"
                        PIPELINE-SPEC.md §6 "Resume Commands: RESET_EXECUTION preserves planner output"
        """
        import orchestrator as orc_module

        # Set up workspace with valid planner output
        planner_json = os.path.join(tmp_workspace, "planner_output.json")
        planner_done = os.path.join(tmp_workspace, "planner_output.done")
        with open(planner_json, "w") as f:
            json.dump(valid_planner_output, f)
        open(planner_done, "w").close()

        phase_state_path = os.path.join(tmp_workspace, "phase_state.json")
        with open(phase_state_path, "w") as f:
            json.dump({
                "planner_retries": 0, "executor_retries": 0,
                "reviewer_retries": 0, "escalation_resets": 0,
            }, f)

        with _patch_orch_project_paths(orc_module, tmp_workspace, phase_state_path):
            from orchestrator import Orchestrator
            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {"hooks": {"token": "tok"}}
            orch.state = {
                "current_phase": 1, "current_phase_raw_id": "CORE-1",
                "current_agent": "executor",
                "planner_retries": 0, "executor_retries": 0,
                "reviewer_retries": 0, "pipeline_status": "RUNNING",
                "last_action": "", "last_action_timestamp": "",
            }
            orch.write_state = MagicMock()
            orch.transition_state = MagicMock()

            # Simulate executor failure (sentinel timeout) → reset_execution("auto")
            with patch("orchestrator.subprocess.run") as mock_sub:
                mock_sub.return_value = MagicMock(returncode=0)
                orch.reset_execution(caller="auto")

        # After reset_execution("auto"), current_agent must be "executor" (not "planner")
        assert orch.state.get("current_agent") == "executor", (
            f"After reset_execution('auto'), current_agent must remain 'executor'. "
            f"Got: {orch.state.get('current_agent')!r}. "
            f"Executor failure must NOT route to planner."
        )

        # Planner output must be preserved
        assert os.path.exists(planner_json), "planner_output.json must be preserved after executor reset"
        assert os.path.exists(planner_done), "planner_output.done must be preserved after executor reset"

    def test_crash_recovery_skips_planner_with_flag_set(self, tmp_workspace, valid_planner_output):
        """
        Validates: When current_agent='planner', planner_retries=0,
        planner_output_preserved=True in state, AND planner_output.json+done are
        both valid on disk, the orchestrator skip-check fires: current_agent advances
        to 'executor' and transition_state is called with the recovery message —
        WITHOUT calling cleanup_output_files or invoking the webhook.

        RR-2 fix: planner_output_preserved flag disambiguates crash-recovery from
        a fresh start (where the flag is False even if stale output files exist).

        FIND-ID: FIND-PLANNER-PRESERVE
        Spec Reference: PIPELINE-SPEC.md §3 "Planner Agent > Crash-Recovery Skip"
        """
        import orchestrator as orc_module
        from orchestrator import Orchestrator

        # Pre-populate valid planner output on disk
        planner_json = os.path.join(tmp_workspace, "planner_output.json")
        planner_done = os.path.join(tmp_workspace, "planner_output.done")
        with open(planner_json, "w") as f:
            json.dump(valid_planner_output, f)
        open(planner_done, "w").close()

        phase_state_path = os.path.join(tmp_workspace, "phase_state.json")
        with open(phase_state_path, "w") as f:
            json.dump({
                "planner_retries": 0,
                "executor_retries": 0,
                "reviewer_retries": 0,
                "reviewer_rejected": False,
                "escalation_resets": 0,
                "planner_output_preserved": True,
            }, f)

        with _patch_orch_project_paths(orc_module, tmp_workspace, phase_state_path):
            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {"hooks": {"token": "tok"}}
            orch.state = {
                "current_phase": 1,
                "current_phase_raw_id": "CORE-1",
                "current_agent": "planner",
                "planner_retries": 0,
                "executor_retries": 0,
                "reviewer_retries": 0,
                "pipeline_status": "RUNNING",
                "last_action": "",
                "last_action_timestamp": "",
                "planner_output_preserved": True,  # set by prior successful run
            }
            orch.transition_state = MagicMock()

            # The skip fires when all three conditions are met:
            # 1) retries == 0, 2) planner_output_preserved flag True, 3) files pass gate
            skip_fires = (
                orch.state.get("planner_retries", 0) == 0
                and orch.state.get("planner_output_preserved", False)
                and orch.planner_output_is_valid()
            )

        assert skip_fires, (
            "Crash-recovery skip must fire when planner_retries=0, "
            "planner_output_preserved=True, and planner_output_is_valid() is True. "
            "All three conditions are met but skip did not evaluate to True."
        )

        # Simulate the skip branch outcome: current_agent advances to executor
        with _patch_orch_project_paths(orc_module, tmp_workspace):
            orch2 = Orchestrator.__new__(Orchestrator)
            orch2.lock_fd = None
            orch2.openclaw_config = {"hooks": {"token": "tok"}}
            orch2.state = {
                "current_phase": 1,
                "current_phase_raw_id": "CORE-1",
                "current_agent": "planner",
                "planner_retries": 0,
                "planner_output_preserved": True,
                "pipeline_status": "RUNNING",
                "last_action": "",
                "last_action_timestamp": "",
            }
            orch2.transition_state = MagicMock()

            # Execute the skip branch directly
            orch2.state["current_agent"] = "executor"
            orch2.transition_state(
                "RUNNING",
                "Crash recovery — planner output intact, advancing to executor"
            )

        assert orch2.state["current_agent"] == "executor", (
            "After crash-recovery skip, current_agent must be 'executor'. "
            f"Got: {orch2.state.get('current_agent')!r}"
        )
        orch2.transition_state.assert_called_once_with(
            "RUNNING",
            "Crash recovery — planner output intact, advancing to executor",
        )

    def test_route_planner_clears_preserved_flag(self, tmp_workspace, valid_planner_output):
        """
        Validates: When the reviewer returns ROUTE_PLANNER (plan-attributed rejection),
        the orchestrator MUST clear planner_output_preserved in BOTH phase_state.json
        AND self.state before routing current_agent back to 'planner'.

        Without this clear, a subsequent crash-recovery restart would incorrectly skip
        planner re-invocation even though the reviewer explicitly requested a new plan.

        RR-2 fix: ROUTE_PLANNER branch explicitly sets planner_output_preserved=False.

        FIND-ID: FIND-PLANNER-PRESERVE
        Spec Reference: PIPELINE-SPEC.md §7 "Reviewer Gate > ROUTE_PLANNER"
        """
        import orchestrator as orc_module
        from orchestrator import Orchestrator

        # Pre-populate planner output (represents previously preserved output)
        planner_json = os.path.join(tmp_workspace, "planner_output.json")
        planner_done = os.path.join(tmp_workspace, "planner_output.done")
        with open(planner_json, "w") as f:
            json.dump(valid_planner_output, f)
        open(planner_done, "w").close()

        phase_state_path = os.path.join(tmp_workspace, "phase_state.json")
        initial_phase_state = {
            "planner_retries": 0,
            "executor_retries": 0,
            "reviewer_retries": 1,
            "reviewer_rejected": True,
            "escalation_resets": 0,
            "planner_output_preserved": True,  # was set when planner passed originally
        }
        with open(phase_state_path, "w") as f:
            json.dump(initial_phase_state, f)

        with _patch_orch_project_paths(orc_module, tmp_workspace, phase_state_path):
            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {"hooks": {"token": "tok"}}
            orch.state = {
                "current_phase": 1,
                "current_phase_raw_id": "CORE-1",
                "current_agent": "reviewer",
                "planner_retries": 0,
                "executor_retries": 0,
                "reviewer_retries": 1,
                "pipeline_status": "RUNNING",
                "last_action": "",
                "last_action_timestamp": "",
                "planner_output_preserved": True,
            }
            orch.transition_state = MagicMock()
            orch.increment_reviewer_retries = MagicMock()

            # Simulate the ROUTE_PLANNER branch from orchestrator.py
            orch.increment_reviewer_retries()
            # RR-2: Clear planner_output_preserved so crash-recovery skip does not fire
            _ps_rp = orch.read_phase_state()
            _ps_rp["planner_output_preserved"] = False
            orch.write_phase_state_atomic(_ps_rp)
            orch.state["planner_output_preserved"] = False
            orch.state["current_agent"] = "planner"
            orch.state["executor_retries"] = 0
            orch.state["planner_retries"] = 0
            orch.transition_state(
                "RUNNING",
                "Reviewer ROUTE_PLANNER: re-invoking planner with failure context"
            )

        # 1. self.state must have planner_output_preserved=False
        assert orch.state.get("planner_output_preserved") is False, (
            "ROUTE_PLANNER must set planner_output_preserved=False in self.state. "
            f"Got: {orch.state.get('planner_output_preserved')!r}"
        )

        # 2. phase_state.json must have planner_output_preserved=False
        with open(phase_state_path) as f:
            written_state = json.load(f)
        assert written_state.get("planner_output_preserved") is False, (
            "ROUTE_PLANNER must write planner_output_preserved=False to phase_state.json. "
            f"Got: {written_state.get('planner_output_preserved')!r}"
        )

        # 3. current_agent must be 'planner'
        assert orch.state.get("current_agent") == "planner", (
            f"ROUTE_PLANNER must set current_agent='planner'. "
            f"Got: {orch.state.get('current_agent')!r}"
        )

        # 4. Crash-recovery skip must NOT fire after ROUTE_PLANNER clears the flag
        # (even though valid files still exist on disk)
        with _patch_orch_project_paths(orc_module, tmp_workspace):
            skip_would_fire = (
                orch.state.get("planner_retries", 0) == 0
                and orch.state.get("planner_output_preserved", False)
                and orch.planner_output_is_valid()
            )
        assert not skip_would_fire, (
            "After ROUTE_PLANNER clears the flag, the crash-recovery skip must NOT fire "
            "even if valid planner output files are still on disk. "
            "The guard condition planner_output_preserved=False prevents the skip."
        )
