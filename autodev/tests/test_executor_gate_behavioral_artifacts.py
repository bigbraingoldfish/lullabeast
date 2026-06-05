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
import tempfile
from contextlib import ExitStack
from unittest.mock import patch

import pytest

import utils as utils_module
import executor_gate as executor_gate_module


def _make_escape_symlink(workspace, link_name="sneaky", target_file="file.txt"):
    """Create an in-workspace symlink that resolves OUTSIDE the workspace, plus a
    real file at its target. Returns the workspace-relative path
    ``"<link_name>/<target_file>"`` whose lexical ``abspath`` stays inside the
    workspace (so the old guard accepts it) but whose ``realpath`` escapes (so the
    hardened guard must reject it). The precondition assert keeps the test honest
    on symlinked-TMPDIR hosts (e.g. macOS, where ``$TMPDIR`` is itself a symlink):
    the escape target must genuinely fall outside ``realpath(workspace)``."""
    parent = os.path.dirname(workspace.rstrip(os.sep))
    outside = tempfile.mkdtemp(dir=parent, prefix="ws_escape_")
    assert os.path.commonpath(
        [os.path.realpath(outside), os.path.realpath(workspace)]
    ) != os.path.realpath(workspace), "escape target must be outside the workspace"
    with open(os.path.join(outside, target_file), "w") as f:
        f.write("secret\n")
    os.symlink(outside, os.path.join(workspace, link_name))
    return f"{link_name}/{target_file}"


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


# ===========================================================================
# Phase 1 defensive hardening — gate output & boundary hardening
# (defensive-hardening-roadmap.md PHASE 1: T1.1 manifest type-coercion,
#  T1.3 tdd-list coercion, T1.4 realpath boundary guard). Co-located here for
# the executor-gate scaffolding above (_patch_workspace / _executor_output /
# _write_pipeline_state / _write_planner_output / _make_escape_symlink).
# ===========================================================================


def test_non_list_file_manifest_fails_validation(tmp_workspace):
    """T1.1 — a non-list ``file_manifest`` (MiniMax emits ``"foo.py"`` instead of
    ``["foo.py"]``) must yield a clean ``FAIL`` + ``ERR_VALIDATION_FAILED``, NOT a
    ``TypeError`` crash. The crash path returns no ``last_error_code`` and skips
    the MiniMax file-deletion guard; the error-code assertion proves the guard
    ran rather than the gate merely crashing.

    RED on current code: ``"foo.py" + []`` raises ``TypeError`` at line 308."""
    out = _executor_output(tmp_workspace, include_planner_files=False)
    out["file_manifest"] = "src/module.py"  # string, not a list
    output_path = os.path.join(tmp_workspace, "executor_output.json")
    with open(output_path, "w") as f:
        json.dump(out, f)

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(output_path)
    assert result == "FAIL"

    with open(os.path.join(tmp_workspace, "phase_state.json")) as f:
        state = json.load(f)
    assert state.get("last_error_code") == "ERR_VALIDATION_FAILED", (
        f"A non-list file_manifest must record ERR_VALIDATION_FAILED so the "
        f"self-heal feedback survives; got {state.get('last_error_code')!r}"
    )


def test_non_list_tests_written_fails_validation(tmp_workspace):
    """T1.1 — symmetric to the file_manifest case: a non-list ``tests_written``
    must also short-circuit to ``FAIL`` + ``ERR_VALIDATION_FAILED`` (the guard
    validates both operands of the concatenation).

    RED on current code: ``[...] + 123`` raises ``TypeError`` at line 308."""
    out = _executor_output(tmp_workspace)
    out["tests_written"] = 123  # int, not a list
    output_path = os.path.join(tmp_workspace, "executor_output.json")
    with open(output_path, "w") as f:
        json.dump(out, f)

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(output_path)
    assert result == "FAIL"

    with open(os.path.join(tmp_workspace, "phase_state.json")) as f:
        state = json.load(f)
    assert state.get("last_error_code") == "ERR_VALIDATION_FAILED"


def test_string_tdd_structure_no_spurious_coverage_warning(tmp_workspace, monkeypatch):
    """T1.3 — a string ``tdd_test_structure`` must be coerced to an empty list so
    the coverage comprehension does NOT iterate it per-character (which produces a
    garbage ``missing`` list and a spurious ``ERR_TDD_COVERAGE_MISMATCH`` warning
    that pollutes the gate-feedback channel the reviewer adjudicates).

    RED on current code: ``[t for t in "render_button" ...]`` flags every char as
    missing, so ``gate_warnings.json`` carries ERR_TDD_COVERAGE_MISMATCH."""
    _write_current_phase_no_behavioral(tmp_workspace)
    _write_pipeline_state(tmp_workspace)
    _write_planner_output(tmp_workspace, tdd_paths="render_button")  # string, not list

    out = _executor_output(tmp_workspace)
    out["tests_written"] = []
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
    assert result == "PASS", f"expected PASS, got {result!r}"

    gw_path = os.path.join(tmp_workspace, "gate_warnings.json")
    codes = []
    if os.path.exists(gw_path):
        with open(gw_path) as f:
            codes = [w.get("code") for w in json.load(f).get("warnings", [])]
    assert "ERR_TDD_COVERAGE_MISMATCH" not in codes, (
        f"A string tdd_test_structure must not emit a per-character coverage "
        f"warning; got warning codes {codes!r}"
    )


def test_int_tdd_structure_does_not_crash(tmp_workspace, monkeypatch):
    """T1.3 — a non-iterable ``tdd_test_structure`` (int) must be coerced, not
    iterated, so the gate returns a normal verdict instead of crashing.

    RED on current code: ``[t for t in 5 ...]`` raises ``TypeError`` at line 350."""
    _write_current_phase_no_behavioral(tmp_workspace)
    _write_pipeline_state(tmp_workspace)
    _write_planner_output(tmp_workspace, tdd_paths=5)  # int, not a list

    out = _executor_output(tmp_workspace)
    out["tests_written"] = []
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
    assert result == "PASS", f"expected a clean verdict, got {result!r}"


def test_manifest_symlink_escape_rejected(tmp_workspace, monkeypatch):
    """T1.4 — an in-workspace symlink in ``file_manifest`` that resolves OUTSIDE
    the workspace must be rejected (``FAIL`` + ``ERR_PATH_TRAVERSAL``). The lexical
    ``abspath`` guard accepted it (the documented-but-unimplemented ``realpath``
    contract); the hardened guard resolves symlinks on both sides.

    RED on current code: ``abspath`` does not follow ``sneaky`` → boundary passes
    → file exists via the symlink → gate reaches PASS."""
    rel = _make_escape_symlink(tmp_workspace)
    _write_current_phase_no_behavioral(tmp_workspace)
    _write_pipeline_state(tmp_workspace)
    _write_planner_output(tmp_workspace)

    out = _executor_output(tmp_workspace, include_planner_files=False)
    out["file_manifest"] = [rel]
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
        f"A manifest path whose realpath escapes the workspace must FAIL with "
        f"ERR_PATH_TRAVERSAL; got {state.get('last_error_code')!r}"
    )


def test_behavioral_artifact_symlink_escape_rejected(tmp_workspace, monkeypatch):
    """T1.4 — the same symlink-escape guard on the second boundary loop
    (``behavioral_smoke_artifacts``). RED on current code for the same reason as
    the manifest case."""
    rel = _make_escape_symlink(tmp_workspace)
    _write_current_phase_with_behavioral(tmp_workspace)
    _write_pipeline_state(tmp_workspace)
    _write_planner_output(tmp_workspace)

    out = _executor_output(tmp_workspace, behavioral_artifacts=[
        {"path": rel, "description": "resolves outside the workspace"},
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
    assert state.get("last_error_code") == "ERR_PATH_TRAVERSAL"
