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
# Environment hygiene — prevent the developer's real `.env` (sourced into the
# shell before running pytest) from leaking into hermetic tests. Individual
# tests that set these via monkeypatch.setenv still work because the autouse
# fixture runs before the per-test monkeypatch.
# ---------------------------------------------------------------------------

_ENV_KEYS_TO_SCRUB = (
    "OPENCLAW_ROOT",
    "AUTODEV_PIPELINE_ROOT",
    "AUTODEV_HOOKS_TOKEN",
)


@pytest.fixture(autouse=True)
def _scrub_autodev_env(monkeypatch):
    """Remove AutoDev env vars inherited from the host shell so tests that set
    them via monkeypatch are not shadowed by a value already present in the
    shell after ``source .env``. Only canonical names are listed; the legacy
    aliases were removed in the hard cut."""
    for key in _ENV_KEYS_TO_SCRUB:
        monkeypatch.delenv(key, raising=False)


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
    """Baseline reviewer output. Includes the minimal ``behavioral_verification``
    object required by the gate on phases whose ``current_phase.json`` carries
    a populated Behavioral Verification block.

    Tests that exercise non-behavioral phases (no block in current_phase.json)
    will still pass through this fixture because the gate's behavioral check
    is content-driven on the phase, not on the reviewer output.

    Tests that *require* the evidence paths to exist on disk should mutate
    ``behavioral_verification.evidence[*].file_or_screenshot_or_log`` and
    create the files before invoking the gate.
    """
    return {
        "blocking_issues": [],
        "suggestions": ["Consider adding docstrings"],
        "integration_tests_passing": True,
        "behavioral_verification": {
            "verdict": "pass",
            "evidence": [
                {
                    "claim": "Public surface item 1 exercised",
                    "file_or_screenshot_or_log": "behavioral-smoke/anchor-1.txt",
                    "method": "stdout_capture",
                },
                {
                    "claim": "Public surface item 2 exercised",
                    "file_or_screenshot_or_log": "behavioral-smoke/anchor-2.txt",
                    "method": "stdout_capture",
                },
                {
                    "claim": "Public surface item 3 exercised",
                    "file_or_screenshot_or_log": "behavioral-smoke/anchor-3.txt",
                    "method": "stdout_capture",
                },
            ],
            "how_to_check_followed": True,
        },
    }


@pytest.fixture
def current_phase_with_behavioral():
    """Build a ``current_phase.json`` payload populated with the P0 Behavioral
    Verification block. Returns a dict the test writes into ``tmp_workspace``.

    Mirrors the canonical shape produced by ``phase_resolver.py`` after Stage D.
    Tests use this directly (write it as JSON into ``tmp_workspace``) when they
    need to exercise the gate's behavioral-verification path."""
    return {
        "phase_number": 1,
        "detail": "Phase CORE-E1: Implement task list",
        "category": "CORE",
        "exit_criteria": ["Task list renders without errors"],
        "status": "PENDING",
        "raw_id": "CORE-E1",
        "behavioral_verification": {
            "user_observable": "The user sees a list of tasks on /tasks.",
            "how_to_check": "Navigate to /tasks; expect at least one row rendered.",
            "failure_language": "The /tasks page does not load.",
        },
        "entry_criteria": "",
        "exit_criteria_block": "",
        "tdd_requirements": [],
        "done_criteria": [],
        "verification_path": "/tmp/verification.md",
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
