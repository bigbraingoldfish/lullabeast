"""
Reviewer contract-failure classification tests.

The reviewer gate returns ``CONTRACT_FAILURE`` when the session ended without a
parseable ``reviewer_output.json`` (missing or malformed). This is NOT a
code-quality rejection — the reviewer never produced a usable verdict. It is
also NOT an "infrastructure failure": genuine transport/provider failures are
peeled off upstream in the orchestrator (stall detection, dead-on-arrival,
provider-rejection) before this verdict is ever consumed, and the plugin's
``agent_end`` backstop writes the ``.done`` sentinel unconditionally — so the
session may have given up cleanly OR been aborted/crashed. Either way the
reviewer breached its output contract, and the recovery is identical (fresh
session + corrective directive + retry). See PIPELINE-SPEC.md §7.

This file replaces the former ``test_reviewer_infra_failure.py``: the
``INFRA_FAILURE`` verdict and ``ERR_INFRA_FAILURE`` code were renamed to
``CONTRACT_FAILURE`` / ``ERR_REVIEWER_CONTRACT_FAILURE`` because "INFRA" was a
mislabel. No assertion in this file may reference the old verdict/code.

FIND-ID: FIND-REVIEWER-CONTRACT
Spec Reference: PIPELINE-SPEC.md §7 "Gate Scripts > Reviewer Output Gate"
"""

import json
import os
from contextlib import ExitStack
from unittest.mock import patch

# Path wiring handled by conftest.py
import utils as utils_module
import reviewer_gate as reviewer_gate_module


def _patch_workspace(tmp_dir):
    """Return an ExitStack that redirects gate workspace paths to tmp_dir."""
    stack = ExitStack()
    tmp_dir = tmp_dir.rstrip(os.sep) + os.sep
    stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", tmp_dir))
    stack.enter_context(patch.object(utils_module, "ARTIFACTS_DIR", tmp_dir))
    ps = os.path.join(tmp_dir.rstrip(os.sep), "phase_state.json")
    stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", ps))
    stack.enter_context(patch.object(reviewer_gate_module, "WORKSPACE_DIR", tmp_dir))
    stack.enter_context(patch.object(reviewer_gate_module, "ARTIFACTS_DIR", tmp_dir))
    stack.enter_context(patch.object(reviewer_gate_module, "PHASE_STATE_FILE", ps))
    return stack


class TestReviewerContractFailureClassification:

    def test_missing_reviewer_output_returns_contract_failure(self, tmp_workspace):
        """Missing reviewer_output.json (session ended without writing it) → the gate
        returns ``CONTRACT_FAILURE``, NOT a routing/rejection code. The reviewer never
        examined the code, so re-running the executor would be wrong."""
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        assert not os.path.exists(output_path), "Pre-condition: no output file present"

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        assert result == "CONTRACT_FAILURE", (
            f"Expected 'CONTRACT_FAILURE' for missing reviewer output, got {result!r}."
        )

    def test_malformed_reviewer_json_returns_contract_failure(self, tmp_workspace):
        """Malformed/unparseable reviewer_output.json (partial write, garbage tokens)
        → the gate returns ``CONTRACT_FAILURE``."""
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            f.write("{malformed json :::}")

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        assert result == "CONTRACT_FAILURE", (
            f"Expected 'CONTRACT_FAILURE' for malformed reviewer JSON, got {result!r}."
        )

    def test_valid_rejection_not_classified_as_contract_failure(self, tmp_workspace):
        """Control case (must stay green): a well-formed rejection with blocking issues
        routes to ROUTE_EXECUTOR on pass 1 — never CONTRACT_FAILURE."""
        reviewer_output = {
            "blocking_issues": [
                {"description": "Logic error in render loop",
                 "attribution": "impl",
                 "affected_file": "src/game.py"}
            ],
            "suggestions": [],
            "integration_tests_passing": False,
        }
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(reviewer_output, f)

        phase_state = {"reviewer_retries": 0, "executor_retries": 0, "planner_retries": 0}
        with open(os.path.join(tmp_workspace, "phase_state.json"), "w") as f:
            json.dump(phase_state, f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        assert result == "ROUTE_EXECUTOR", (
            f"Expected 'ROUTE_EXECUTOR' for a well-formed rejection on pass 1, got {result!r}."
        )
        assert result != "CONTRACT_FAILURE", (
            "A valid rejection must NOT be classified as CONTRACT_FAILURE."
        )

    def test_contract_failure_does_not_consume_reviewer_retry_budget(self, tmp_workspace):
        """A contract failure must NOT increment reviewer_retries (reserved for genuine
        code-quality rejections)."""
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        phase_state_path = os.path.join(tmp_workspace, "phase_state.json")
        with open(phase_state_path, "w") as f:
            json.dump({"reviewer_retries": 0}, f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        assert result == "CONTRACT_FAILURE"
        with open(phase_state_path) as f:
            state = json.load(f)
        assert state.get("reviewer_retries", 0) == 0, (
            f"reviewer_retries must remain 0 after a contract failure; got "
            f"{state.get('reviewer_retries')}."
        )

    def test_contract_failure_writes_error_code_to_phase_state(self, tmp_workspace):
        """A CONTRACT_FAILURE result writes ``ERR_REVIEWER_CONTRACT_FAILURE`` to
        phase_state.last_error_code so the orchestrator and operators can distinguish it
        from a code-quality rejection."""
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        assert result == "CONTRACT_FAILURE"
        phase_state_path = os.path.join(tmp_workspace, "phase_state.json")
        assert os.path.exists(phase_state_path), (
            "phase_state.json must be written on CONTRACT_FAILURE"
        )
        with open(phase_state_path) as f:
            state = json.load(f)
        assert state.get("last_error_code") == "ERR_REVIEWER_CONTRACT_FAILURE", (
            f"Expected last_error_code='ERR_REVIEWER_CONTRACT_FAILURE', "
            f"got {state.get('last_error_code')!r}"
        )

    def test_old_infra_failure_verdict_is_gone(self, tmp_workspace):
        """Removal completeness: the gate must never emit the renamed-away verdict.
        A green test for the old verdict would be a liability."""
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result != "INFRA_FAILURE", (
            "The gate must no longer emit 'INFRA_FAILURE' — it was renamed to "
            "'CONTRACT_FAILURE'."
        )
