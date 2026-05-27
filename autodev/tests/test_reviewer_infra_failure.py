"""
Reviewer infrastructure failure classification tests.

Validates that the reviewer gate correctly distinguishes between:
  - Infrastructure failures (empty/unparseable LLM response) → "INFRA_FAILURE"
  - Genuine reviewer rejections (well-formed JSON with blocking issues) → routing codes

FIND-ID: FIND-REVIEWER-INFRA
Spec Reference: PIPELINE-SPEC.md §7 "Gate Scripts > Reviewer Output Gate"
                PIPELINE-CONSTRAINTS.md §5 "Design Rationale Archive > JSON Parse = Structural Validation Failure"
                PIPELINE-SPEC.md §5 "Reviewer Agent > 3-Pass Logic"
"""

import json
import os
import sys
import tempfile
from contextlib import ExitStack
from unittest.mock import patch

import pytest

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


class TestReviewerInfraFailureClassification:

    def test_empty_llm_response_classified_as_infra_failure_not_rejection(self, tmp_workspace):
        """
        Validates: When the reviewer_output.json is missing (empty LLM response / model
        returned nothing and never wrote the output file), the gate must return
        "INFRA_FAILURE", not "ROUTE_EXECUTOR".

        An empty-response infra failure is NOT a code quality rejection — the reviewer
        never examined the code.  Routing to ROUTE_EXECUTOR would force the executor to
        redo all its work unnecessarily.

        FIND-ID: FIND-REVIEWER-INFRA
        Spec Reference: PIPELINE-SPEC.md §7 > Reviewer Output Gate
        Spec Gap: No spec clause currently distinguishes INFRA_FAILURE from rejection.
        """
        # reviewer_output.json is deliberately absent (never written by reviewer)
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        assert not os.path.exists(output_path), "Pre-condition: no output file present"

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        assert result == "INFRA_FAILURE", (
            f"Expected 'INFRA_FAILURE' for missing reviewer output (empty LLM response), "
            f"got {result!r}.  Current code incorrectly routes to ROUTE_EXECUTOR/ROUTE_PLANNER."
        )

    def test_json_parse_error_classified_as_infra_failure(self, tmp_workspace):
        """
        Validates: When the reviewer wrote malformed/unparseable JSON (partial write,
        model produced garbage tokens), the gate must return "INFRA_FAILURE".

        FIND-ID: FIND-REVIEWER-INFRA
        Spec Reference: PIPELINE-SPEC.md §7 > Reviewer Output Gate
        Spec Gap: Existing spec groups JSON parse errors with structural validation failures
                  (PIPELINE-CONSTRAINTS §5 Design Rationale), but that applies to
                  planner/executor.  Reviewer parse errors are infra failures.
        """
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            f.write("{malformed json :::}")

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        assert result == "INFRA_FAILURE", (
            f"Expected 'INFRA_FAILURE' for malformed reviewer JSON, got {result!r}."
        )

    def test_valid_rejection_classified_correctly(self, tmp_workspace):
        """
        Validates: When the reviewer returns well-formed JSON with blocking issues,
        the gate correctly routes to ROUTE_EXECUTOR / ROUTE_PLANNER / ROUTE_ESCALATE
        (not INFRA_FAILURE).

        This confirms the fix doesn't break normal rejection routing.

        FIND-ID: FIND-REVIEWER-INFRA (control case — must stay green)
        Spec Reference: PIPELINE-SPEC.md §7 > Reviewer Output Gate > Branching
        """
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

        # Start from a clean phase_state so we get pass 1 routing
        phase_state = {"reviewer_retries": 0, "executor_retries": 0, "planner_retries": 0}
        with open(os.path.join(tmp_workspace, "phase_state.json"), "w") as f:
            json.dump(phase_state, f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        # On pass 1, a valid rejection routes to executor
        assert result == "ROUTE_EXECUTOR", (
            f"Expected 'ROUTE_EXECUTOR' for a well-formed rejection on pass 1, got {result!r}."
        )
        assert result != "INFRA_FAILURE", "A valid rejection must NOT be classified as INFRA_FAILURE."

    def test_infra_failure_does_not_consume_reviewer_retry_budget(self, tmp_workspace):
        """
        Validates: Infrastructure failures (missing/malformed output) must NOT increment
        the reviewer_retries counter in phase_state.json.  Only genuine rejections
        (well-formed JSON with blocking_issues) should consume retry budget.

        FIND-ID: FIND-REVIEWER-INFRA
        Spec Reference: PIPELINE-SPEC.md §7 > Reviewer Output Gate ("On FAIL: Increment reviewer_retries")
        Spec Gap: The spec does not carve out infra failures from the retry counter.
        """
        # No reviewer output — simulates model returning empty response
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")

        # Write a fresh phase_state with 0 retries
        phase_state_path = os.path.join(tmp_workspace, "phase_state.json")
        with open(phase_state_path, "w") as f:
            json.dump({"reviewer_retries": 0}, f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        assert result == "INFRA_FAILURE"

        # The gate must NOT have written a reviewer_retries increment
        with open(phase_state_path) as f:
            state = json.load(f)
        assert state.get("reviewer_retries", 0) == 0, (
            f"reviewer_retries must remain 0 after infra failure; got "
            f"{state.get('reviewer_retries')}.  Infra failures must not consume the retry budget."
        )

    def test_infra_failure_writes_error_code_to_phase_state(self, tmp_workspace):
        """
        Validates: An INFRA_FAILURE result must write a distinct error code
        ("ERR_INFRA_FAILURE") to phase_state.json last_error_code, so the orchestrator
        and human operators can distinguish infra from logic failures.

        FIND-ID: FIND-REVIEWER-INFRA
        Spec Reference: PIPELINE-SPEC.md §7 > Gate Scripts (error codes)
        """
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        # File is absent — infra failure

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        assert result == "INFRA_FAILURE"

        phase_state_path = os.path.join(tmp_workspace, "phase_state.json")
        assert os.path.exists(phase_state_path), "phase_state.json must be written on INFRA_FAILURE"
        with open(phase_state_path) as f:
            state = json.load(f)
        assert state.get("last_error_code") == "ERR_INFRA_FAILURE", (
            f"Expected last_error_code='ERR_INFRA_FAILURE', got {state.get('last_error_code')!r}"
        )
