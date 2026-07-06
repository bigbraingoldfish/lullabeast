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
    art = workspace / ".autodev" / "pipeline"
    art.mkdir(parents=True)

    (art / "planner_output.json").write_text(json.dumps(planner), encoding="utf-8")

    executor_payload = {
        "status": "complete",
        "tests_written": [],
        "test_results": {"all_passing": True},
        "file_manifest": [],
        "files_deleted": [],
    }
    exec_path = art / "executor_output.json"
    exec_path.write_text(json.dumps(executor_payload), encoding="utf-8")

    art_str = str(art) + os.sep
    ps_path = str(art / "phase_state.json")
    stack = ExitStack()
    stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", ws_str))
    stack.enter_context(patch.object(utils_module, "ARTIFACTS_DIR", art_str))
    stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", ps_path))
    stack.enter_context(patch.object(executor_gate_module, "WORKSPACE_DIR", ws_str))
    stack.enter_context(patch.object(executor_gate_module, "ARTIFACTS_DIR", art_str))
    stack.enter_context(patch.object(executor_gate_module, "PHASE_STATE_FILE", ps_path))

    with stack:
        result = executor_gate_module.evaluate_executor(str(exec_path))

    assert result == "FAIL"
    detail_path = art / executor_gate_module.EXECUTOR_GATE_DETAIL_JSON
    assert detail_path.is_file()
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    assert detail.get("gate_error") == "ERR_UNACCOUNTED_DELETION"
    assert "tracked_victim.txt" in detail.get("unaccounted_deletions", [])


def test_git_diff_failure_returns_fail_not_skip(tmp_path):
    """F3: When git diff exits non-zero, gate must return FAIL (not skip the check).

    Previously the else-branch printed [GATE WARN] and allowed the gate to pass,
    meaning a git diff infrastructure failure silently disabled the deletion guard.
    """
    root = tmp_path
    workspace = root / "pipeline-project"
    workspace.mkdir()
    ws_str = str(workspace) + os.sep

    (root / "pipeline_state.json").write_text(
        json.dumps({"phase_base_commit": "deadbeef0000000000000000000000000000000000"}),
        encoding="utf-8",
    )

    art = workspace / ".autodev" / "pipeline"
    art.mkdir(parents=True)
    art_str = str(art) + os.sep

    planner = {"implementation_plan": [], "tdd_test_structure": [], "pass_criteria": []}
    (art / "planner_output.json").write_text(json.dumps(planner), encoding="utf-8")

    executor_payload = {
        "status": "complete",
        "tests_written": [],
        "test_results": {"all_passing": True},
        "file_manifest": [],
        "files_deleted": [],
    }
    exec_path = art / "executor_output.json"
    exec_path.write_text(json.dumps(executor_payload), encoding="utf-8")

    ps_path = str(art / "phase_state.json")

    # Simulate git diff failing with returncode=128 (not a git repo)
    import subprocess as _subprocess

    class _FailResult:
        returncode = 128
        stdout = ""
        stderr = "fatal: not a git repo"

    stack = ExitStack()
    stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", ws_str))
    stack.enter_context(patch.object(utils_module, "ARTIFACTS_DIR", art_str))
    stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", ps_path))
    stack.enter_context(patch.object(executor_gate_module, "WORKSPACE_DIR", ws_str))
    stack.enter_context(patch.object(executor_gate_module, "ARTIFACTS_DIR", art_str))
    stack.enter_context(patch.object(executor_gate_module, "PHASE_STATE_FILE", ps_path))
    stack.enter_context(
        patch.object(_subprocess, "run", return_value=_FailResult())
    )

    with stack:
        result = executor_gate_module.evaluate_executor(str(exec_path))

    assert result == "FAIL", (
        "git diff failure should return FAIL to prevent the deletion guard being silently disabled"
    )

    # Error code must be recorded
    if os.path.exists(ps_path):
        ps = json.loads(Path(ps_path).read_text(encoding="utf-8"))
        assert ps.get("last_error_code") == "ERR_GIT_DIFF_FAILED"


def test_deletion_check_crash_returns_fail_not_skip(tmp_path):
    """F6: When the deletion-check git invocation RAISES (FileNotFoundError /
    TimeoutExpired / OSError — git missing, killed, or timing out), the gate must
    fail CLOSED, not print '[GATE WARN] ... skipping' and PASS.

    The surrounding `except Exception` previously swallowed the error and let the
    gate fall through to `return "PASS"`, silently disabling the MiniMax deletion
    guard whenever git itself crashed. This test pins the fail-closed contract:
    return FAIL + record ERR_DELETION_CHECK_CRASHED, matching the rc!=0 and
    missing-base siblings.
    """
    root = tmp_path
    workspace = root / "pipeline-project"
    workspace.mkdir()
    ws_str = str(workspace) + os.sep

    # A truthy phase_base_commit so the guard enters the git-diff `try` (past the
    # missing-base fail-closed branch); the value is irrelevant because the git
    # call is mocked to raise before it is used.
    (root / "pipeline_state.json").write_text(
        json.dumps({"phase_base_commit": "deadbeef0000000000000000000000000000000000"}),
        encoding="utf-8",
    )

    art = workspace / ".autodev" / "pipeline"
    art.mkdir(parents=True)
    art_str = str(art) + os.sep

    planner = {"implementation_plan": [], "tdd_test_structure": [], "pass_criteria": []}
    (art / "planner_output.json").write_text(json.dumps(planner), encoding="utf-8")

    executor_payload = {
        "status": "complete",
        "tests_written": [],
        "test_results": {"all_passing": True},
        "file_manifest": [],
        "files_deleted": [],
    }
    exec_path = art / "executor_output.json"
    exec_path.write_text(json.dumps(executor_payload), encoding="utf-8")

    ps_path = str(art / "phase_state.json")

    # The deletion-check git diff (executor_gate.py:487) is the FIRST subprocess.run
    # reached in evaluate_executor, so a blanket side_effect raises exactly there.
    import subprocess as _subprocess

    stack = ExitStack()
    stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", ws_str))
    stack.enter_context(patch.object(utils_module, "ARTIFACTS_DIR", art_str))
    stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", ps_path))
    stack.enter_context(patch.object(executor_gate_module, "WORKSPACE_DIR", ws_str))
    stack.enter_context(patch.object(executor_gate_module, "ARTIFACTS_DIR", art_str))
    stack.enter_context(patch.object(executor_gate_module, "PHASE_STATE_FILE", ps_path))
    stack.enter_context(
        patch.object(_subprocess, "run", side_effect=OSError("git: command not found"))
    )

    with stack:
        result = executor_gate_module.evaluate_executor(str(exec_path))

    assert result == "FAIL", (
        "a crashed deletion-check git invocation must fail closed, not skip the "
        "guard and PASS"
    )
    assert os.path.exists(ps_path), "the gate must record an error code on crash"
    ps = json.loads(Path(ps_path).read_text(encoding="utf-8"))
    assert ps.get("last_error_code") == "ERR_DELETION_CHECK_CRASHED"


def test_ls_files_deleted_failure_returns_fail_not_skip(tmp_path):
    """Audit M4: when the committed-diff probe succeeds but the working-tree
    probe (`git ls-files --deleted`) exits non-zero, the gate must fail CLOSED —
    not silently omit uncommitted deletions from the guard.

    Previously only `if _wt_result.returncode == 0:` guarded the second probe
    (no else): git succeeding for the diff call but failing for ls-files (repo
    lock acquired between the two, transient OOM kill) let an executor that
    deleted project files without committing pass the gate undetected.
    """
    root = tmp_path
    workspace = root / "pipeline-project"
    workspace.mkdir()
    ws_str = str(workspace) + os.sep

    (root / "pipeline_state.json").write_text(
        json.dumps({"phase_base_commit": "deadbeef0000000000000000000000000000000000"}),
        encoding="utf-8",
    )

    art = workspace / ".autodev" / "pipeline"
    art.mkdir(parents=True)
    art_str = str(art) + os.sep

    planner = {"implementation_plan": [], "tdd_test_structure": [], "pass_criteria": []}
    (art / "planner_output.json").write_text(json.dumps(planner), encoding="utf-8")

    executor_payload = {
        "status": "complete",
        "tests_written": [],
        "test_results": {"all_passing": True},
        "file_manifest": [],
        "files_deleted": [],
    }
    exec_path = art / "executor_output.json"
    exec_path.write_text(json.dumps(executor_payload), encoding="utf-8")

    ps_path = str(art / "phase_state.json")

    import subprocess as _subprocess

    class _OkResult:
        returncode = 0
        stdout = ""
        stderr = ""

    class _FailResult:
        returncode = 128
        stdout = ""
        stderr = "fatal: index locked"

    def _dispatch(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["git", "ls-files"]:
            return _FailResult()
        return _OkResult()

    stack = ExitStack()
    stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", ws_str))
    stack.enter_context(patch.object(utils_module, "ARTIFACTS_DIR", art_str))
    stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", ps_path))
    stack.enter_context(patch.object(executor_gate_module, "WORKSPACE_DIR", ws_str))
    stack.enter_context(patch.object(executor_gate_module, "ARTIFACTS_DIR", art_str))
    stack.enter_context(patch.object(executor_gate_module, "PHASE_STATE_FILE", ps_path))
    stack.enter_context(patch.object(_subprocess, "run", side_effect=_dispatch))

    with stack:
        result = executor_gate_module.evaluate_executor(str(exec_path))

    assert result == "FAIL", (
        "a failed working-tree deletion probe must fail closed, not silently "
        "drop uncommitted deletions from the guard"
    )
    assert os.path.exists(ps_path), "the gate must record an error code"
    ps = json.loads(Path(ps_path).read_text(encoding="utf-8"))
    assert ps.get("last_error_code") == "ERR_GIT_DIFF_FAILED"
