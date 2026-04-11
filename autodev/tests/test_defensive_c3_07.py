"""C3-07: executor_gate must fail closed when phase_base_commit is missing.

The deletion check is the only automated guard against MiniMax silently deleting
project files.  When phase_base_commit is unavailable, the gate must return FAIL
(not PASS) so the orchestrator retries with a fresh session rather than accepting
potentially corrupted executor output.
"""
import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE_SCRIPTS_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline", "gate_scripts")
for _p in [GATE_SCRIPTS_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import utils as utils_module
import executor_gate as executor_gate_module


def _minimal_executor_payload(workspace: Path) -> Path:
    """Write a valid executor_output.json with all tests passing."""
    payload = {
        "status": "complete",
        "tests_written": [],
        "test_results": {"all_passing": True},
        "file_manifest": [],
        "files_deleted": [],
    }
    p = workspace / "executor_output.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _minimal_planner(workspace: Path) -> None:
    planner = {"implementation_plan": [], "tdd_test_structure": [], "pass_criteria": []}
    (workspace / "planner_output.json").write_text(json.dumps(planner), encoding="utf-8")


def test_gate_fails_when_phase_base_commit_missing(tmp_path):
    """gate must return FAIL (not PASS) when phase_base_commit is absent.

    Previously the gate skipped the deletion check and returned PASS — this
    means a MiniMax deletion would go undetected."""
    workspace = tmp_path / "pipeline-project"
    workspace.mkdir()
    ws_str = str(workspace) + os.sep

    exec_path = _minimal_executor_payload(workspace)
    _minimal_planner(workspace)

    # NO pipeline_state.json at all → phase_base_commit will be None
    ps_path = str(workspace / "phase_state.json")

    with ExitStack() as stack:
        stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", ws_str))
        stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", ps_path))
        stack.enter_context(patch.object(executor_gate_module, "WORKSPACE_DIR", ws_str))
        stack.enter_context(patch.object(executor_gate_module, "PHASE_STATE_FILE", ps_path))

        result = executor_gate_module.evaluate_executor(str(exec_path))

    assert result == "FAIL", (
        f"Gate returned {result!r} when phase_base_commit was missing; "
        "it should return FAIL to fail closed and prevent potential MiniMax deletions going undetected."
    )


def test_gate_fails_when_pipeline_state_has_no_commit_key(tmp_path):
    """gate must return FAIL when pipeline_state.json exists but lacks phase_base_commit."""
    workspace = tmp_path / "pipeline-project"
    workspace.mkdir()
    ws_str = str(workspace) + os.sep

    exec_path = _minimal_executor_payload(workspace)
    _minimal_planner(workspace)

    # Write pipeline_state.json with no phase_base_commit in the same dir as workspace
    parent_dir = workspace.parent
    pipeline_state = parent_dir / "pipeline_state.json"
    pipeline_state.write_text(json.dumps({"status": "RUNNING", "current_phase": 1}))

    ps_path = str(workspace / "phase_state.json")

    with ExitStack() as stack:
        stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", ws_str))
        stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", ps_path))
        stack.enter_context(patch.object(executor_gate_module, "WORKSPACE_DIR", ws_str))
        stack.enter_context(patch.object(executor_gate_module, "PHASE_STATE_FILE", ps_path))

        result = executor_gate_module.evaluate_executor(str(exec_path))

    assert result == "FAIL", (
        f"Gate returned {result!r} when phase_base_commit key was missing from state; "
        "it should fail closed."
    )
