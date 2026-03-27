"""Executor gate: ERR_UNACCOUNTED_DELETION emits executor_gate_detail.json for failure_context."""

import json
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

OPENCLAW_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE_SCRIPTS_DIR = os.path.join(OPENCLAW_DIR, "autodev", "pipeline", "gate_scripts")
for _p in [GATE_SCRIPTS_DIR, OPENCLAW_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import utils as utils_module
import executor_gate as executor_gate_module


def _git(workspace: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )


def test_unaccounted_worktree_deletion_writes_executor_gate_detail(tmp_path):
    """Tracked file removed from disk without git rm -> detail JSON lists path."""
    root = tmp_path
    workspace = root / "pipeline-project"
    workspace.mkdir()
    ws_str = str(workspace) + os.sep

    _git(workspace, "init", "-b", "main")
    _git(workspace, "config", "user.email", "test@example.com")
    _git(workspace, "config", "user.name", "pytest")

    victim = workspace / "tracked_victim.txt"
    victim.write_text("v\n", encoding="utf-8")
    _git(workspace, "add", "tracked_victim.txt")
    _git(workspace, "commit", "-m", "init")
    phase_base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    victim.unlink()

    (root / "pipeline_state.json").write_text(
        json.dumps({"phase_base_commit": phase_base}),
        encoding="utf-8",
    )

    planner = {
        "implementation_plan": [],
        "tdd_test_structure": [],
        "pass_criteria": [],
    }
    (workspace / "planner_output.json").write_text(json.dumps(planner), encoding="utf-8")

    executor_payload = {
        "status": "complete",
        "tests_written": [],
        "test_results": {"all_passing": True},
        "file_manifest": [],
        "files_deleted": [],
    }
    exec_path = workspace / "executor_output.json"
    exec_path.write_text(json.dumps(executor_payload), encoding="utf-8")

    ps_path = str(workspace / "phase_state.json")
    stack = ExitStack()
    stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", ws_str))
    stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", ps_path))
    stack.enter_context(patch.object(executor_gate_module, "WORKSPACE_DIR", ws_str))
    stack.enter_context(patch.object(executor_gate_module, "PHASE_STATE_FILE", ps_path))

    with stack:
        result = executor_gate_module.evaluate_executor(str(exec_path))

    assert result == "FAIL"
    detail_path = workspace / executor_gate_module.EXECUTOR_GATE_DETAIL_JSON
    assert detail_path.is_file()
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    assert detail.get("gate_error") == "ERR_UNACCOUNTED_DELETION"
    assert "tracked_victim.txt" in detail.get("unaccounted_deletions", [])
