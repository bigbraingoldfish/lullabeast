"""C3-07: executor_gate must fail closed when phase_base_commit is missing.

The deletion check is the only automated guard against MiniMax silently deleting
project files.  When phase_base_commit is unavailable, the gate must return FAIL
(not PASS) so the orchestrator retries with a fresh session rather than accepting
potentially corrupted executor output.

Fixture layout (post-2026-04-27 ``.autodev/pipeline/`` migration): the gate reads
``planner_output.json`` and writes ``phase_state.json`` under
``WORKSPACE_DIR/.autodev/pipeline/`` (a.k.a. ``ARTIFACTS_DIR``). Tests therefore
patch ``WORKSPACE_DIR``, ``ARTIFACTS_DIR``, AND ``PHASE_STATE_FILE`` together so
the gate looks in the tmp workspace rather than the real filesystem.
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


def _make_workspace_layout(tmp_path: Path) -> tuple[Path, Path]:
    """Create the post-migration layout: ``<workspace>/.autodev/pipeline/``.

    Returns ``(workspace, artifacts_dir)``. The gate writes phase_state.json
    and reads planner_output.json from the artifacts directory; the test
    payloads must land there, not directly under the workspace root.
    """
    workspace = tmp_path / "pipeline-project"
    workspace.mkdir()
    artifacts_dir = workspace / ".autodev" / "pipeline"
    artifacts_dir.mkdir(parents=True)
    return workspace, artifacts_dir


def _minimal_executor_payload(artifacts_dir: Path) -> Path:
    """Write a valid executor_output.json with all tests passing."""
    payload = {
        "status": "complete",
        "tests_written": [],
        "test_results": {"all_passing": True},
        "file_manifest": [],
        "files_deleted": [],
    }
    p = artifacts_dir / "executor_output.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _minimal_planner(artifacts_dir: Path) -> None:
    planner = {"implementation_plan": [], "tdd_test_structure": [], "pass_criteria": []}
    (artifacts_dir / "planner_output.json").write_text(json.dumps(planner), encoding="utf-8")


def _patch_gate_paths(stack: ExitStack, workspace: Path, artifacts_dir: Path) -> str:
    """Patch the module-level path constants in both utils and executor_gate.

    Returns the path string used for PHASE_STATE_FILE so tests can assert
    on its absence/presence after the gate runs.
    """
    ws_str = str(workspace) + os.sep
    art_str = str(artifacts_dir) + os.sep
    ps_path = str(artifacts_dir / "phase_state.json")

    for module in (utils_module, executor_gate_module):
        stack.enter_context(patch.object(module, "WORKSPACE_DIR", ws_str))
        stack.enter_context(patch.object(module, "ARTIFACTS_DIR", art_str))
        stack.enter_context(patch.object(module, "PHASE_STATE_FILE", ps_path))
    return ps_path


def test_gate_fails_when_phase_base_commit_missing(tmp_path):
    """gate must return FAIL (not PASS) when phase_base_commit is absent.

    Previously the gate skipped the deletion check and returned PASS — this
    means a MiniMax deletion would go undetected."""
    workspace, artifacts_dir = _make_workspace_layout(tmp_path)
    exec_path = _minimal_executor_payload(artifacts_dir)
    _minimal_planner(artifacts_dir)

    # NO pipeline_state.json at all → phase_base_commit will be None
    with ExitStack() as stack:
        _patch_gate_paths(stack, workspace, artifacts_dir)
        result = executor_gate_module.evaluate_executor(str(exec_path))

    assert result == "FAIL", (
        f"Gate returned {result!r} when phase_base_commit was missing; "
        "it should return FAIL to fail closed and prevent potential MiniMax deletions going undetected."
    )


def test_gate_fails_when_pipeline_state_has_no_commit_key(tmp_path):
    """gate must return FAIL when pipeline_state.json exists but lacks phase_base_commit."""
    workspace, artifacts_dir = _make_workspace_layout(tmp_path)
    exec_path = _minimal_executor_payload(artifacts_dir)
    _minimal_planner(artifacts_dir)

    # Write pipeline_state.json next to workspace (the location the gate reads:
    # ``os.path.dirname(WORKSPACE_DIR.rstrip("/"))/pipeline_state.json``).
    pipeline_state = workspace.parent / "pipeline_state.json"
    pipeline_state.write_text(json.dumps({"status": "RUNNING", "current_phase": 1}))

    with ExitStack() as stack:
        _patch_gate_paths(stack, workspace, artifacts_dir)
        result = executor_gate_module.evaluate_executor(str(exec_path))

    assert result == "FAIL", (
        f"Gate returned {result!r} when phase_base_commit key was missing from state; "
        "it should fail closed."
    )
