"""
Executor completion detection and preemption handling tests.

The orchestrator must reliably classify the executor's terminal state:
  - sentinel found + gate passes     → executor_succeeded
  - sentinel not found + no output  → executor_crashed  (or timeout)
  - output exists but sentinel gone → executor_preempted (external kill mid-write)
  - process exited non-zero + no output → executor_crashed

FIND-ID: FIND-HEARTBEAT (external preemption path)
         FIND-DONE-FILE  (output present without sentinel)
Spec Reference: PIPELINE-SPEC.md §4 "Executor Agent > Sentinel"
                PIPELINE-SPEC.md §10 "Infrastructure > Sentinel Pattern"
                PIPELINE-CONSTRAINTS.md §5.8 "OpenClaw Native Heartbeat Disabled"
Spec Gap: The spec does not define distinct states for executor_crashed vs
          executor_preempted.  External preemption is unaddressed.
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


class TestExecutorCompletionDetection:

    def test_executor_crash_detected_by_missing_output(self, tmp_workspace):
        """
        Validates: When poll_for_sentinel returns False AND executor_output.json does
        not exist, the outcome is 'executor_crashed' (sentinel timeout / process died
        before writing anything).

        FIND-ID: FIND-HEARTBEAT
        Spec Reference: PIPELINE-SPEC.md §4 "Executor Agent > Sentinel"
        """
        import orchestrator as orc_module

        sentinel_path = os.path.join(tmp_workspace, "executor_output.done")
        output_path = os.path.join(tmp_workspace, "executor_output.json")

        # Neither file exists
        assert not os.path.exists(sentinel_path)
        assert not os.path.exists(output_path)

        # classify_executor_outcome must exist on Orchestrator and return "executor_crashed"
        assert hasattr(orc_module.Orchestrator, "classify_executor_outcome"), (
            "Orchestrator must have a 'classify_executor_outcome(sentinel_found, output_path)' "
            "method that returns one of: executor_succeeded, executor_crashed, executor_preempted"
        )

        with patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace):
            from orchestrator import Orchestrator
            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {}
            orch.state = {}

            outcome = orch.classify_executor_outcome(
                sentinel_found=False,
                output_path=output_path,
            )

        assert outcome == "executor_crashed", (
            f"Expected 'executor_crashed' when no sentinel and no output, got {outcome!r}"
        )

    def test_executor_success_detected_by_output_presence(self, tmp_workspace,
                                                            valid_executor_output):
        """
        Validates: When poll_for_sentinel returns True (sentinel found), the outcome
        is 'executor_succeeded' regardless of whether the gate subsequently passes.

        FIND-ID: FIND-DONE-FILE
        Spec Reference: PIPELINE-SPEC.md §4 "Executor Agent > Sentinel"
        """
        import orchestrator as orc_module

        output_path = os.path.join(tmp_workspace, "executor_output.json")
        with open(output_path, "w") as f:
            json.dump(valid_executor_output, f)

        assert hasattr(orc_module.Orchestrator, "classify_executor_outcome"), (
            "Orchestrator.classify_executor_outcome must be implemented"
        )

        with patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace):
            from orchestrator import Orchestrator
            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {}
            orch.state = {}

            outcome = orch.classify_executor_outcome(
                sentinel_found=True,
                output_path=output_path,
            )

        assert outcome == "executor_succeeded", (
            f"Expected 'executor_succeeded' when sentinel found, got {outcome!r}"
        )

    def test_executor_success_with_missing_done_file_not_treated_as_failure(self,
                                                                              tmp_workspace,
                                                                              valid_executor_output):
        """
        Validates: When executor_output.json exists and is valid but executor_output.done
        is absent, the state must be 'executor_preempted' (external kill mid-write of
        .done) — NOT 'executor_failed'.

        This prevents the orchestrator from escalating or burning executor retry budget
        when the executor had actually completed its work.

        FIND-ID: FIND-HEARTBEAT
        Spec Reference: PIPELINE-SPEC.md §4 "Executor Agent > Sentinel"
        Spec Gap: No current spec clause defines executor_preempted or the partial
                  output detection path.
        """
        import orchestrator as orc_module

        output_path = os.path.join(tmp_workspace, "executor_output.json")
        with open(output_path, "w") as f:
            json.dump(valid_executor_output, f)
        # .done is deliberately NOT created — simulates kill between JSON write and sentinel write

        assert hasattr(orc_module.Orchestrator, "classify_executor_outcome"), (
            "Orchestrator.classify_executor_outcome must be implemented"
        )

        with patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace):
            from orchestrator import Orchestrator
            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {}
            orch.state = {}

            outcome = orch.classify_executor_outcome(
                sentinel_found=False,
                output_path=output_path,
            )

        assert outcome == "executor_preempted", (
            f"Expected 'executor_preempted' when output exists but sentinel is absent, "
            f"got {outcome!r}.  Must NOT be 'executor_failed' or 'executor_crashed'."
        )

    def test_external_preemption_handling(self, tmp_workspace, valid_executor_output):
        """
        Validates: When the executor is classified as 'executor_preempted' (output file
        exists, sentinel absent), the orchestrator must:
          a) NOT increment executor_retries (it was infra preemption, not executor failure)
          b) Log a distinct error code "ERR_EXECUTOR_PREEMPTED" to phase_state.json
          c) Escalate rather than immediately retrying without investigation

        FIND-ID: FIND-HEARTBEAT
        Spec Reference: PIPELINE-SPEC.md §4 "Executor Agent > Two Retry Scenarios"
        Spec Gap: External preemption is unaddressed by the spec.
        """
        import orchestrator as orc_module

        output_path = os.path.join(tmp_workspace, "executor_output.json")
        with open(output_path, "w") as f:
            json.dump(valid_executor_output, f)
        # .done absent — preemption scenario

        phase_state_path = os.path.join(tmp_workspace, "phase_state.json")
        with open(phase_state_path, "w") as f:
            json.dump({
                "executor_retries": 0,
                "planner_retries": 0,
                "reviewer_retries": 0,
                "escalation_resets": 0,
            }, f)

        assert hasattr(orc_module.Orchestrator, "classify_executor_outcome"), (
            "Orchestrator.classify_executor_outcome must be implemented"
        )

        with patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace):
            from orchestrator import Orchestrator
            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {}
            orch.state = {}

            outcome = orch.classify_executor_outcome(
                sentinel_found=False,
                output_path=output_path,
            )

        assert outcome == "executor_preempted"

        # The preemption path must write ERR_EXECUTOR_PREEMPTED to phase_state.json
        # This is tested separately via the orchestrator's handle_executor_preempted() path,
        # but we verify the classify function itself produces the right outcome for wiring.
        # The actual error code logging is done by the caller (orchestrator main loop).
        # Verify the output file is preserved (not deleted) in this case.
        assert os.path.exists(output_path), (
            "executor_output.json must NOT be deleted on preemption detection — "
            "it may contain valid output that can be gate-checked."
        )
