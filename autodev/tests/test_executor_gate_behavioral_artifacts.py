"""Executor-gate behavioural-artifact invariants that survive the Phase 3 demotion.

Phase 3 (gate-feedback methodology) demoted the *interpretive* behavioural-artifact
checks — empty / absent / malformed-shape / path-missing-on-disk — from hard
**FAIL**s to non-blocking **warnings** the reviewer adjudicates. Those PASS+warning
cases now live in ``test_executor_gate_demoted_warnings.py``.

This file retains only the behavioural-specific invariants that are NOT part of
the demotion:

* **E1** — a phase with no behavioural block requires no artifacts (PASS).
* **E3** — the workspace-boundary guard is a security check, NOT interpretive: a
  behavioural-artifact path escaping the workspace still hard-FAILs with
  ``ERR_PATH_TRAVERSAL`` (CLAUDE.md Security Constraints — must not be demoted).
* **E4** — valid behavioural artifacts pass cleanly with no warning.

Idiom: patch the workspace globals, stub ``subprocess.run`` so the deletion
check is a no-op, and call ``evaluate_executor`` directly.
"""

import json
import os
import sys
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


def _write_current_phase_with_behavioral(workspace, raw_id="CORE-E1"):
    payload = {
        "phase_number": 1,
        "detail": f"Phase {raw_id}",
        "raw_id": raw_id,
        "behavioral_verification": {
            "user_observable": "x",
            "how_to_check": "y",
            "failure_language": "z",
        },
    }
    with open(os.path.join(workspace, "current_phase.json"), "w") as f:
        json.dump(payload, f)


def _write_current_phase_no_behavioral(workspace, raw_id="CORE-E1"):
    payload = {
        "phase_number": 1,
        "detail": f"Phase {raw_id}",
        "raw_id": raw_id,
        "behavioral_verification": None,
    }
    with open(os.path.join(workspace, "current_phase.json"), "w") as f:
        json.dump(payload, f)


def _write_pipeline_state(workspace, base_commit="abc123"):
    """The executor gate reads phase_base_commit from pipeline_state.json
    from the PARENT of WORKSPACE_DIR (it treats WORKSPACE_DIR as the
    pipeline-project symlink and looks one directory up for pipeline state).
    We must write there so the deletion check finds the base commit and
    doesn't bail with ERR_MISSING_BASE_COMMIT."""
    parent = os.path.dirname(workspace.rstrip(os.sep))
    payload = {"phase_base_commit": base_commit}
    with open(os.path.join(parent, "pipeline_state.json"), "w") as f:
        json.dump(payload, f)


def _make_artifact(workspace, rel_path):
    """Create the file at rel_path under workspace and return the rel_path."""
    abs_path = os.path.join(workspace, rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w") as f:
        f.write("ok\n")
    return rel_path


def _executor_output(workspace, *, behavioral_artifacts=None,
                     include_planner_files=True):
    """Baseline executor output. The gate validates file_manifest existence;
    create the files so we don't trip an unrelated warning."""
    file_manifest = ["src/module.py"] if include_planner_files else []
    for rel in file_manifest:
        abs_p = os.path.join(workspace, rel)
        os.makedirs(os.path.dirname(abs_p), exist_ok=True)
        open(abs_p, "w").close()
    out = {
        "status": "complete",
        "tests_written": [],
        "test_results": {"all_passing": True},
        "file_manifest": file_manifest,
        "files_deleted": [],
    }
    if behavioral_artifacts is not None:
        out["behavioral_smoke_artifacts"] = behavioral_artifacts
    return out


def _write_planner_output(workspace, tdd_paths=None):
    """The executor gate cross-references tests_written against the planner's
    tdd_test_structure. With an empty list, both sides match."""
    payload = {
        "implementation_plan": ["x"],
        "tdd_test_structure": tdd_paths or [],
        "pass_criteria": [{"condition": "x"}],
    }
    with open(os.path.join(workspace, "planner_output.json"), "w") as f:
        json.dump(payload, f)


# ---------------------------------------------------------------------------
# E1 — no block on the phase, no requirement
# ---------------------------------------------------------------------------


def test_no_behavioral_block_means_no_artifact_requirement(tmp_workspace, monkeypatch):
    """Legacy/transitional phases: current_phase.behavioral_verification is
    None → executor gate must NOT require behavioral_smoke_artifacts. (Phases
    queued before P0 ships are exempt — see parent plan §2.9.)"""
    _write_current_phase_no_behavioral(tmp_workspace)
    _write_pipeline_state(tmp_workspace)
    _write_planner_output(tmp_workspace)

    out = _executor_output(tmp_workspace, behavioral_artifacts=None)
    output_path = os.path.join(tmp_workspace, "executor_output.json")
    with open(output_path, "w") as f:
        json.dump(out, f)

    # Stub git diff to report no deletions so we skip that branch.
    import subprocess
    class _Sub:
        returncode = 0
        stdout = ""
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Sub())

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(output_path)
    assert result == "PASS", (
        f"Phase without a behavioural block must not require "
        f"behavioral_smoke_artifacts; got {result!r}"
    )


# ---------------------------------------------------------------------------
# E3 — path safety (security guard — NOT demoted by Phase 3)
# ---------------------------------------------------------------------------


def test_behavioral_artifact_path_traversal_rejected(tmp_workspace, monkeypatch):
    """Workspace-bound check must reject ``../escape.txt`` — same guard
    pattern as file_manifest validation. This is a security boundary and
    stays a hard FAIL even though the existence/shape checks are now warnings."""
    _write_current_phase_with_behavioral(tmp_workspace)
    _write_pipeline_state(tmp_workspace)
    _write_planner_output(tmp_workspace)

    out = _executor_output(tmp_workspace, behavioral_artifacts=[
        {"path": "../escape.txt", "description": "would escape"},
    ])
    output_path = os.path.join(tmp_workspace, "executor_output.json")
    with open(output_path, "w") as f:
        json.dump(out, f)

    import subprocess
    class _Sub:
        returncode = 0
        stdout = ""
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Sub())

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(output_path)
    assert result == "FAIL"

    with open(os.path.join(tmp_workspace, "phase_state.json")) as f:
        state = json.load(f)
    assert state.get("last_error_code") == "ERR_PATH_TRAVERSAL", (
        f"Path traversal in behavioral_smoke_artifacts must be rejected with "
        f"the same code as file_manifest traversal; got {state.get('last_error_code')!r}"
    )


# ---------------------------------------------------------------------------
# E4 — happy path
# ---------------------------------------------------------------------------


def test_valid_behavioral_smoke_artifacts_pass(tmp_workspace, monkeypatch):
    _write_current_phase_with_behavioral(tmp_workspace)
    _write_pipeline_state(tmp_workspace)
    _write_planner_output(tmp_workspace)

    rel1 = _make_artifact(tmp_workspace, "behavioral-smoke/CORE-E1-step1.txt")
    rel2 = _make_artifact(tmp_workspace, "behavioral-smoke/CORE-E1-step2.txt")
    out = _executor_output(tmp_workspace, behavioral_artifacts=[
        {"path": rel1, "description": "Step 1 ran clean"},
        {"path": rel2, "description": "Step 2 ran clean"},
    ])
    output_path = os.path.join(tmp_workspace, "executor_output.json")
    with open(output_path, "w") as f:
        json.dump(out, f)

    import subprocess
    class _Sub:
        returncode = 0
        stdout = ""
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Sub())

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(output_path)
    assert result == "PASS", (
        f"Valid behavioral_smoke_artifacts must pass the gate; got {result!r}"
    )
    # A fully-valid behavioural phase emits no warning.
    assert not os.path.exists(os.path.join(tmp_workspace, "gate_warnings.json"))
