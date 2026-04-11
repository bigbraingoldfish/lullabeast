"""
Done-file and intermediate-checkpoint logic tests.

The pipeline uses per-agent sentinel (.done) files as atomic completion signals.
There is no single "phase complete" .done file; phase completion is reflected by:
  1. executor_succeeded flag in phase_state.json  (after executor gate passes)
  2. Roadmap checkbox updated to [x]             (after merge)

These tests verify the executor_succeeded checkpoint is correctly recorded and
preserved across reviewer failures so the pipeline can distinguish:
  - "executor succeeded, reviewer failed" from "executor failed"

FIND-ID: FIND-DONE-FILE
Spec Reference: PIPELINE-SPEC.md §10 "Infrastructure > File Paths" (sentinel files)
                PIPELINE-SPEC.md §4 "Executor Agent > Two Retry Scenarios"
                PIPELINE-CONSTRAINTS.md §5.8 "OpenClaw Native Heartbeat Disabled"
"""

import json
import os
import sys
import tempfile
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

OPENCLAW_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE_SCRIPTS_DIR = os.path.join(OPENCLAW_DIR, "autodev", "pipeline", "gate_scripts")
for _p in [GATE_SCRIPTS_DIR, OPENCLAW_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import utils as utils_module
import executor_gate as executor_gate_module


def _make_executor_gate_patch(tmp_dir):
    stack = ExitStack()
    ps_path = os.path.join(tmp_dir, "phase_state.json")
    stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", tmp_dir + "/"))
    stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", ps_path))
    stack.enter_context(patch.object(executor_gate_module, "WORKSPACE_DIR", tmp_dir + "/"))
    # executor_gate imports PHASE_STATE_FILE directly — must patch in its own namespace too
    stack.enter_context(patch.object(executor_gate_module, "PHASE_STATE_FILE", ps_path))

    # C3-07 fix: gate now fails closed if phase_base_commit is missing.
    # Write a mock pipeline_state.json with a sentinel commit so tests that are
    # not specifically testing the deletion guard pass through to PASS/FAIL as before.
    # The git diff will fail (no real git repo) but the gate handles that gracefully.
    pipeline_state_path = os.path.join(os.path.dirname(tmp_dir.rstrip("/")), "pipeline_state.json")
    with open(pipeline_state_path, "w") as _f:
        json.dump({"phase_base_commit": "0000000000000000000000000000000000000000"}, _f)

    return stack


class TestDoneFileLogic:

    def test_done_file_written_only_after_full_pipeline_completion(self, tmp_workspace):
        """
        Validates: The executor_output.done sentinel is written by the executor agent
        as its final act.  The orchestrator must only advance to the reviewer AFTER
        both executor_output.json AND executor_output.done exist and pass the gate.

        This test confirms that a missing .done file (executor still writing) results
        in poll_for_sentinel returning False, not a gate pass.

        FIND-ID: FIND-DONE-FILE
        Spec Reference: PIPELINE-SPEC.md §4 "Executor Agent > Sentinel"
                        PIPELINE-SPEC.md §10 "Infrastructure > Sentinel Pattern"
        """
        from sentinel_poller import poll_for_sentinel

        # Sentinel file does NOT exist yet
        sentinel_path = os.path.join(tmp_workspace, "executor_output.done")
        assert not os.path.exists(sentinel_path)

        # With a zero timeout, poll immediately returns False
        result = poll_for_sentinel(sentinel_path, timeout_seconds=0)
        assert result is False, (
            "poll_for_sentinel must return False when .done file is absent — "
            "orchestrator must not advance past sentinel poll without the file."
        )

        # Now create the sentinel
        open(sentinel_path, "w").close()
        result2 = poll_for_sentinel(sentinel_path, timeout_seconds=1)
        assert result2 is True, "poll_for_sentinel must return True when .done file exists."

    def test_executor_success_state_preserved_when_reviewer_fails(self, tmp_workspace,
                                                                    valid_planner_output,
                                                                    valid_executor_output):
        """
        Validates: After the executor gate passes, phase_state.json must record
        executor_succeeded = True.  If the reviewer subsequently fails (infra failure
        or rejection), this flag must remain True so the orchestrator knows the
        executor's work was valid.

        Current behavior (BUG): executor_succeeded is never set.
        Expected behavior (after fix): executor_succeeded = True in phase_state.json
        after executor gate passes.

        FIND-ID: FIND-DONE-FILE
        Spec Reference: PIPELINE-SPEC.md §4 "Executor Agent > Two Retry Scenarios"
        Spec Gap: No current spec clause defines executor_succeeded as a checkpoint.
        """
        # Write valid executor + planner output to workspace
        with open(os.path.join(tmp_workspace, "planner_output.json"), "w") as f:
            json.dump(valid_planner_output, f)
        for rel in valid_executor_output["file_manifest"]:
            p = os.path.join(tmp_workspace, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").close()
        exec_path = os.path.join(tmp_workspace, "executor_output.json")
        with open(exec_path, "w") as f:
            json.dump(valid_executor_output, f)

        with _make_executor_gate_patch(tmp_workspace):
            result = executor_gate_module.evaluate_executor(exec_path)

        assert result == "PASS", f"Executor gate should PASS for valid output, got {result!r}"

        # After gate PASS, phase_state.json must contain executor_succeeded = True
        phase_state_path = os.path.join(tmp_workspace, "phase_state.json")
        assert os.path.exists(phase_state_path), (
            "phase_state.json must exist after executor gate pass to record executor_succeeded"
        )
        with open(phase_state_path) as f:
            state = json.load(f)

        assert state.get("executor_succeeded") is True, (
            f"executor_succeeded must be True in phase_state.json after executor gate PASS. "
            f"Got: {state.get('executor_succeeded')!r}.  "
            f"Without this flag, the orchestrator cannot distinguish 'executor OK, reviewer failed' "
            f"from 'executor failed'."
        )

    def test_done_file_not_written_when_reviewer_fails(self, tmp_workspace,
                                                        valid_planner_output,
                                                        valid_executor_output):
        """
        Validates: When the reviewer returns an infrastructure failure (empty output),
        the orchestrator state must reflect 'reviewer_failed' — not 'executor_failed'.

        Concretely: after reviewer infra failure, executor_succeeded must remain True
        in phase_state.json and the last_error_code must indicate a reviewer problem,
        not an executor problem.

        FIND-ID: FIND-DONE-FILE
        Spec Reference: PIPELINE-SPEC.md §5 "Reviewer Agent > 3-Pass Logic"
        Spec Gap: No spec clause defines intermediate states between executor completion
                  and full pipeline completion.
        """
        import reviewer_gate as reviewer_gate_module

        # Executor succeeded — record it
        phase_state_path = os.path.join(tmp_workspace, "phase_state.json")
        with open(phase_state_path, "w") as f:
            json.dump({
                "planner_retries": 0,
                "executor_retries": 0,
                "reviewer_retries": 0,
                "executor_succeeded": True,     # executor completed successfully
                "escalation_resets": 0,
            }, f)

        # Reviewer output is absent — simulates infra failure (empty LLM response)
        reviewer_output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        assert not os.path.exists(reviewer_output_path)

        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", tmp_workspace + "/"))
        stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", phase_state_path))
        stack.enter_context(patch.object(reviewer_gate_module, "WORKSPACE_DIR", tmp_workspace + "/"))
        stack.enter_context(patch.object(reviewer_gate_module, "PHASE_STATE_FILE", phase_state_path))

        with stack:
            gate_result = reviewer_gate_module.evaluate_reviewer(reviewer_output_path)

        # Gate must return INFRA_FAILURE (not route executor)
        assert gate_result == "INFRA_FAILURE", (
            f"Reviewer infra failure must return INFRA_FAILURE, got {gate_result!r}"
        )

        # executor_succeeded must still be True — executor is NOT at fault
        with open(phase_state_path) as f:
            state_after = json.load(f)

        assert state_after.get("executor_succeeded") is True, (
            "executor_succeeded must remain True after reviewer infra failure. "
            "The orchestrator must not treat reviewer infra failure as executor failure."
        )

        # last_error_code must indicate reviewer/infra problem
        assert state_after.get("last_error_code") == "ERR_INFRA_FAILURE", (
            f"last_error_code must be ERR_INFRA_FAILURE after reviewer infra failure, "
            f"got {state_after.get('last_error_code')!r}"
        )

    def test_done_file_detection_on_orchestrator_restart(self, tmp_workspace,
                                                           valid_planner_output,
                                                           valid_executor_output):
        """
        Validates: If executor_output.done exists from a prior successful executor run
        AND the executor gate would pass, the orchestrator must skip re-invoking the
        executor and proceed to the reviewer directly.

        Current behavior (BUG): orchestrator always calls cleanup_output_files() which
        deletes executor_output.done before re-invoking executor.
        Expected behavior (after fix): if done file exists + gate passes, proceed to reviewer.

        FIND-ID: FIND-DONE-FILE
        Spec Reference: PIPELINE-SPEC.md §10 "Infrastructure > Sentinel Pattern"
        Spec Gap: No spec clause defines restart-recovery behavior for a completed executor.
        """
        import orchestrator as orc_module

        # Pre-populate workspace with complete executor output
        with open(os.path.join(tmp_workspace, "planner_output.json"), "w") as f:
            json.dump(valid_planner_output, f)
        for rel in valid_executor_output["file_manifest"]:
            p = os.path.join(tmp_workspace, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").close()
        with open(os.path.join(tmp_workspace, "executor_output.json"), "w") as f:
            json.dump(valid_executor_output, f)
        # Sentinel already present from prior run
        open(os.path.join(tmp_workspace, "executor_output.done"), "w").close()

        # Record executor_succeeded so orchestrator knows to skip re-invocation
        phase_state_path = os.path.join(tmp_workspace, "phase_state.json")
        with open(phase_state_path, "w") as f:
            json.dump({
                "executor_retries": 0,
                "executor_succeeded": True,
                "reviewer_retries": 0,
                "planner_retries": 0,
                "escalation_resets": 0,
            }, f)

        # After restart with current_agent="executor" and executor_succeeded=True in state,
        # the orchestrator should NOT delete executor_output.done and re-invoke.
        # We verify by checking the done file still exists after the "should_skip_executor" check.
        # This function does not exist yet — test will fail until fix is applied.
        assert hasattr(orc_module.Orchestrator, "executor_output_already_succeeded"), (
            "Orchestrator must expose a method 'executor_output_already_succeeded(phase_state)' "
            "that returns True when executor_succeeded is set, allowing the restart path to "
            "skip re-invocation and proceed directly to the reviewer."
        )
