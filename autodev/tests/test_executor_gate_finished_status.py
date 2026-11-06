"""The executor's finished status: "complete" and "completed" mean the same.

Models write either word when they finish a phase. The gate used to accept only
"complete", so a finished phase reported as "completed" failed with
ERR_STATUS_NOT_COMPLETE and spent a retry. Both words now pass the status check
(case and surrounding spaces don't matter). Anything else still fails it:
"stuck", "failed", an unknown word, or a missing or non-string status.

Idiom mirrors test_executor_gate_demoted_warnings.py: patch the workspace
globals, stub ``subprocess.run`` so the git deletion check is a no-op, and call
``evaluate_executor`` directly.
"""

import json
import os
from contextlib import ExitStack
from unittest.mock import patch

import pytest

import utils as utils_module
import executor_gate as executor_gate_module


def _patch_workspace(tmp_dir):
    stack = ExitStack()
    tmp_dir_with_sep = tmp_dir.rstrip(os.sep) + os.sep
    stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", tmp_dir_with_sep))
    stack.enter_context(patch.object(utils_module, "ARTIFACTS_DIR", tmp_dir_with_sep))
    ps = os.path.join(tmp_dir_with_sep.rstrip(os.sep), "phase_state.json")
    stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", ps))
    stack.enter_context(patch.object(executor_gate_module, "WORKSPACE_DIR", tmp_dir_with_sep))
    stack.enter_context(patch.object(executor_gate_module, "ARTIFACTS_DIR", tmp_dir_with_sep))
    stack.enter_context(patch.object(executor_gate_module, "PHASE_STATE_FILE", ps))
    return stack


def _stub_git_no_deletions(monkeypatch):
    """Stub subprocess.run so the deletion guard sees no deleted files and PASSes."""
    import subprocess

    class _Sub:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Sub())


def _write_phase_inputs(workspace):
    """pipeline_state.json (one dir up), current_phase.json and planner_output.json."""
    parent = os.path.dirname(workspace.rstrip(os.sep))
    with open(os.path.join(parent, "pipeline_state.json"), "w") as f:
        json.dump({"phase_base_commit": "abc123"}, f)
    with open(os.path.join(workspace, "current_phase.json"), "w") as f:
        json.dump({"phase_number": 1, "detail": "Phase CORE-E1", "raw_id": "CORE-E1",
                   "behavioral_verification": None}, f)
    with open(os.path.join(workspace, "planner_output.json"), "w") as f:
        json.dump({"implementation_plan": ["x"], "tdd_test_structure": [],
                   "pass_criteria": [{"condition": "x"}]}, f)


_MISSING = object()


def _write_executor_output(workspace, status=_MISSING):
    for rel in ("src/app.py", "tests/test_app.py"):
        abs_path = os.path.join(workspace, rel)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w") as f:
            f.write("ok\n")
    out = {
        "tests_written": ["tests/test_app.py"],
        "test_results": {"all_passing": True},
        "file_manifest": ["src/app.py"],
        "files_deleted": [],
    }
    if status is not _MISSING:
        out["status"] = status
    path = os.path.join(workspace, "executor_output.json")
    with open(path, "w") as f:
        json.dump(out, f)
    return path


def _last_error_code(workspace):
    path = os.path.join(workspace, "phase_state.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f).get("last_error_code")


def _run_gate(workspace, monkeypatch, status=_MISSING):
    _stub_git_no_deletions(monkeypatch)
    _write_phase_inputs(workspace)
    out = _write_executor_output(workspace, status)
    with _patch_workspace(workspace):
        return executor_gate_module.evaluate_executor(out)


@pytest.mark.parametrize("status", ["complete", "completed", "Completed", "COMPLETE", " completed "])
def test_finished_status_passes_the_gate(tmp_workspace, monkeypatch, status):
    """Both words, in any case and with stray spaces, count as finished."""
    result = _run_gate(tmp_workspace, monkeypatch, status)
    assert result == "PASS", f"status {status!r} means finished; got {result!r}"
    assert _last_error_code(tmp_workspace) != "ERR_STATUS_NOT_COMPLETE"


@pytest.mark.parametrize("status", ["stuck", "failed", "incomplete", "completely stuck", "", None, 1, True, ["complete"]])
def test_unfinished_status_fails_with_status_code(tmp_workspace, monkeypatch, status):
    """Anything other than the two finished words still fails, as before."""
    result = _run_gate(tmp_workspace, monkeypatch, status)
    assert result == "FAIL", f"status {status!r} is not a finished status; got {result!r}"
    assert _last_error_code(tmp_workspace) == "ERR_STATUS_NOT_COMPLETE"


def test_missing_status_fails_with_status_code(tmp_workspace, monkeypatch):
    result = _run_gate(tmp_workspace, monkeypatch)
    assert result == "FAIL"
    assert _last_error_code(tmp_workspace) == "ERR_STATUS_NOT_COMPLETE"


def test_is_finished_status_helper():
    """The gate's one definition of finished, shared by every caller."""
    finished = executor_gate_module.is_finished_status
    assert finished("complete") and finished("completed")
    assert finished(" Completed\n")
    for value in ("stuck", "failed", "done", "", None, 0, ["complete"]):
        assert not finished(value), value
    assert executor_gate_module.FINISHED_STATUSES == frozenset({"complete", "completed"})
