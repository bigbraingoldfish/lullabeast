"""
Shared fixtures and sys.path wiring for all pipeline regression tests.

Every test module imports from this conftest automatically via pytest's fixture
discovery.  The path setup ensures gate_scripts and orchestrator are importable
without installing them as packages.
"""

import json
import os
import sys
import tempfile

import pytest

# ---------------------------------------------------------------------------
# sys.path wiring — must happen at import time so module-level imports in
# test files resolve correctly.
# ---------------------------------------------------------------------------

OPENCLAW_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE_SCRIPTS_DIR = os.path.join(OPENCLAW_DIR, "autodev", "pipeline", "gate_scripts")
PIPELINE_DIR = os.path.join(OPENCLAW_DIR, "autodev", "pipeline")

for _p in [GATE_SCRIPTS_DIR, PIPELINE_DIR, OPENCLAW_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Shared data fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace(tmp_path):
    """Return a fresh temporary directory that acts as ~/.openclaw/pipeline-project."""
    return str(tmp_path)


@pytest.fixture
def valid_planner_output():
    return {
        "implementation_plan": ["Step 1: create module", "Step 2: add tests"],
        "tdd_test_structure": ["tests/test_core.py", "tests/test_utils.py"],
        "pass_criteria": [{"condition": "All tests pass"}, {"condition": "No lint errors"}],
    }


@pytest.fixture
def valid_executor_output():
    return {
        "status": "complete",
        "tests_written": ["tests/test_core.py", "tests/test_utils.py"],
        "test_results": {"all_passing": True},
        "file_manifest": ["tests/test_core.py", "tests/test_utils.py"],
        "failure_reason": None,
    }


@pytest.fixture
def valid_reviewer_output():
    return {
        "blocking_issues": [],
        "suggestions": ["Consider adding docstrings"],
        "integration_tests_passing": True,
        "phase_intent_validated": True,
    }


@pytest.fixture
def fresh_phase_state():
    return {
        "planner_retries": 0,
        "executor_retries": 0,
        "reviewer_retries": 0,
        "reviewer_rejected": False,
        "escalation_resets": 0,
    }


@pytest.fixture
def workspace_with_planner_output(tmp_workspace, valid_planner_output):
    """Workspace pre-populated with valid planner output + sentinel."""
    planner_json = os.path.join(tmp_workspace, "planner_output.json")
    planner_done = os.path.join(tmp_workspace, "planner_output.done")
    with open(planner_json, "w") as f:
        json.dump(valid_planner_output, f)
    open(planner_done, "w").close()
    return tmp_workspace


@pytest.fixture
def workspace_with_executor_output(workspace_with_planner_output, valid_executor_output):
    """Workspace pre-populated with valid executor output + sentinel."""
    ws = workspace_with_planner_output
    # Create the files declared in the manifest
    for rel in valid_executor_output["file_manifest"]:
        abs_path = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        open(abs_path, "w").close()
    exec_json = os.path.join(ws, "executor_output.json")
    exec_done = os.path.join(ws, "executor_output.done")
    with open(exec_json, "w") as f:
        json.dump(valid_executor_output, f)
    open(exec_done, "w").close()
    return ws
