"""Phase 3 (gate-feedback methodology) — interpretive checks demoted to warnings.

Three checks that used to hard-**FAIL** the executor gate now PASS the gate and
record a non-blocking **warning** to ``gate_warnings.json`` for the reviewer to
adjudicate:

* ``ERR_MANIFEST_FILE_MISSING``       — a declared file isn't on disk.
* ``ERR_TDD_COVERAGE_MISMATCH``       — a planner-listed test isn't in tests_written.
* ``ERR_BEHAVIORAL_ARTIFACTS_MISSING`` — behavioral_smoke_artifacts empty/malformed/missing-on-disk.

The **interleaved** ``ERR_PATH_TRAVERSAL`` security branches in the same loops
MUST still hard-FAIL — demoting the existence/shape checks must not weaken the
workspace-boundary guard (CLAUDE.md Security Constraints).

These tests fail against pre-Phase-3 code (which returns "FAIL" + stamps
``last_error_code``) and pass once the demotion lands.

Idiom mirrors test_executor_gate_behavioral_artifacts.py: patch the workspace
globals, stub ``subprocess.run`` so the git deletion check is a no-op, and call
``evaluate_executor`` directly.
"""

import json
import os
from contextlib import ExitStack
from unittest.mock import patch

import utils as utils_module
import executor_gate as executor_gate_module


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


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


def _write_pipeline_state(workspace, base_commit="abc123"):
    """phase_base_commit lives in pipeline_state.json one dir above WORKSPACE_DIR."""
    parent = os.path.dirname(workspace.rstrip(os.sep))
    with open(os.path.join(parent, "pipeline_state.json"), "w") as f:
        json.dump({"phase_base_commit": base_commit}, f)


def _write_current_phase(workspace, raw_id="CORE-E1", behavioral=False):
    payload = {"phase_number": 1, "detail": f"Phase {raw_id}", "raw_id": raw_id}
    payload["behavioral_verification"] = (
        {"user_observable": "x", "how_to_check": "y", "failure_language": "z"}
        if behavioral
        else None
    )
    with open(os.path.join(workspace, "current_phase.json"), "w") as f:
        json.dump(payload, f)


def _write_planner_output(workspace, tdd_paths=None):
    with open(os.path.join(workspace, "planner_output.json"), "w") as f:
        json.dump({"implementation_plan": ["x"],
                   "tdd_test_structure": tdd_paths or [],
                   "pass_criteria": [{"condition": "x"}]}, f)


def _make_file(workspace, rel_path):
    abs_path = os.path.join(workspace, rel_path)
    os.makedirs(os.path.dirname(abs_path) or workspace, exist_ok=True)
    with open(abs_path, "w") as f:
        f.write("ok\n")
    return rel_path


def _write_executor_output(workspace, *, file_manifest=None, tests_written=None,
                           behavioral_artifacts=None, create_manifest_files=True):
    file_manifest = file_manifest or []
    tests_written = tests_written or []
    if create_manifest_files:
        for rel in list(file_manifest) + list(tests_written):
            _make_file(workspace, rel)
    out = {
        "status": "complete",
        "tests_written": tests_written,
        "test_results": {"all_passing": True},
        "file_manifest": file_manifest,
        "files_deleted": [],
    }
    if behavioral_artifacts is not None:
        out["behavioral_smoke_artifacts"] = behavioral_artifacts
    path = os.path.join(workspace, "executor_output.json")
    with open(path, "w") as f:
        json.dump(out, f)
    return path


def _read_warnings(workspace):
    path = os.path.join(workspace, "gate_warnings.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _read_state(workspace):
    path = os.path.join(workspace, "phase_state.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _codes(warnings_doc):
    return {w.get("code") for w in (warnings_doc or {}).get("warnings", [])}


# ---------------------------------------------------------------------------
# Demoted check #1 — ERR_MANIFEST_FILE_MISSING
# ---------------------------------------------------------------------------


def test_manifest_missing_file_passes_with_warning(tmp_workspace, monkeypatch):
    """A declared-but-absent (in-bounds) file no longer FAILs: gate PASSes and
    records an ERR_MANIFEST_FILE_MISSING warning naming the file. last_error_code
    is NOT stamped (no retry budget consumed)."""
    _stub_git_no_deletions(monkeypatch)
    _write_pipeline_state(tmp_workspace)
    _write_current_phase(tmp_workspace)
    _write_planner_output(tmp_workspace)
    # src/present.py exists; src/ghost.py is declared but never created.
    out = _write_executor_output(tmp_workspace, file_manifest=["src/present.py"])
    with open(os.path.join(tmp_workspace, "executor_output.json")) as f:
        payload = json.load(f)
    payload["file_manifest"] = ["src/present.py", "src/ghost.py"]
    with open(out, "w") as f:
        json.dump(payload, f)

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(out)

    assert result == "PASS", f"missing-manifest-file must no longer FAIL; got {result!r}"
    warnings = _read_warnings(tmp_workspace)
    assert warnings is not None, "gate_warnings.json must be written on a demoted warning"
    assert "ERR_MANIFEST_FILE_MISSING" in _codes(warnings)
    manifest_w = next(w for w in warnings["warnings"]
                      if w["code"] == "ERR_MANIFEST_FILE_MISSING")
    assert "src/ghost.py" in manifest_w.get("files", [])
    state = _read_state(tmp_workspace)
    assert state.get("last_error_code") != "ERR_MANIFEST_FILE_MISSING"
    assert state.get("executor_succeeded") is True


# ---------------------------------------------------------------------------
# Demoted check #2 — ERR_TDD_COVERAGE_MISMATCH
# ---------------------------------------------------------------------------


def test_tdd_coverage_mismatch_passes_with_warning(tmp_workspace, monkeypatch):
    """A planner-listed test absent from tests_written no longer FAILs."""
    _stub_git_no_deletions(monkeypatch)
    _write_pipeline_state(tmp_workspace)
    _write_current_phase(tmp_workspace)
    _write_planner_output(tmp_workspace, tdd_paths=["tests/test_a.py", "tests/test_b.py"])
    # Only test_a written; test_b is the gap.
    out = _write_executor_output(tmp_workspace, tests_written=["tests/test_a.py"])

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(out)

    assert result == "PASS"
    warnings = _read_warnings(tmp_workspace)
    assert "ERR_TDD_COVERAGE_MISMATCH" in _codes(warnings)
    tdd_w = next(w for w in warnings["warnings"] if w["code"] == "ERR_TDD_COVERAGE_MISMATCH")
    assert "tests/test_b.py" in tdd_w.get("missing_tests", [])
    assert _read_state(tmp_workspace).get("last_error_code") != "ERR_TDD_COVERAGE_MISMATCH"


# ---------------------------------------------------------------------------
# Demoted check #3 — ERR_BEHAVIORAL_ARTIFACTS_MISSING (empty / missing / malformed)
# ---------------------------------------------------------------------------


def test_behavioral_artifacts_empty_passes_with_warning(tmp_workspace, monkeypatch):
    _stub_git_no_deletions(monkeypatch)
    _write_pipeline_state(tmp_workspace)
    _write_current_phase(tmp_workspace, behavioral=True)
    _write_planner_output(tmp_workspace)
    out = _write_executor_output(tmp_workspace, file_manifest=["src/m.py"],
                                 behavioral_artifacts=[])

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(out)

    assert result == "PASS"
    assert "ERR_BEHAVIORAL_ARTIFACTS_MISSING" in _codes(_read_warnings(tmp_workspace))
    assert _read_state(tmp_workspace).get("last_error_code") != "ERR_BEHAVIORAL_ARTIFACTS_MISSING"


def test_behavioral_artifacts_absent_passes_with_warning(tmp_workspace, monkeypatch):
    """behavioral_smoke_artifacts field omitted entirely (None) → same demoted
    branch as empty-list: PASS + warning, not FAIL."""
    _stub_git_no_deletions(monkeypatch)
    _write_pipeline_state(tmp_workspace)
    _write_current_phase(tmp_workspace, behavioral=True)
    _write_planner_output(tmp_workspace)
    out = _write_executor_output(tmp_workspace, file_manifest=["src/m.py"],
                                 behavioral_artifacts=None)

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(out)

    assert result == "PASS"
    assert "ERR_BEHAVIORAL_ARTIFACTS_MISSING" in _codes(_read_warnings(tmp_workspace))


def test_behavioral_artifact_path_missing_on_disk_passes_with_warning(tmp_workspace, monkeypatch):
    _stub_git_no_deletions(monkeypatch)
    _write_pipeline_state(tmp_workspace)
    _write_current_phase(tmp_workspace, behavioral=True)
    _write_planner_output(tmp_workspace)
    out = _write_executor_output(
        tmp_workspace, file_manifest=["src/m.py"],
        behavioral_artifacts=[{"path": "behavioral-smoke/ghost.png", "description": "ghost"}],
    )

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(out)

    assert result == "PASS"
    assert "ERR_BEHAVIORAL_ARTIFACTS_MISSING" in _codes(_read_warnings(tmp_workspace))


def test_behavioral_artifact_entry_not_dict_passes_with_warning(tmp_workspace, monkeypatch):
    """A malformed (non-dict) artifact entry is a warning now, not a FAIL — and
    must not crash the (still hard) path-traversal check on sibling entries."""
    _stub_git_no_deletions(monkeypatch)
    _write_pipeline_state(tmp_workspace)
    _write_current_phase(tmp_workspace, behavioral=True)
    _write_planner_output(tmp_workspace)
    out = _write_executor_output(
        tmp_workspace, file_manifest=["src/m.py"],
        behavioral_artifacts=["behavioral-smoke/string-not-dict.txt"],
    )

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(out)

    assert result == "PASS"
    assert "ERR_BEHAVIORAL_ARTIFACTS_MISSING" in _codes(_read_warnings(tmp_workspace))


# ---------------------------------------------------------------------------
# Aggregation + clean-pass + stale-clear
# ---------------------------------------------------------------------------


def test_multiple_demoted_failures_aggregate(tmp_workspace, monkeypatch):
    """Manifest-missing AND tdd-mismatch in one run → PASS + both codes present."""
    _stub_git_no_deletions(monkeypatch)
    _write_pipeline_state(tmp_workspace)
    _write_current_phase(tmp_workspace)
    _write_planner_output(tmp_workspace, tdd_paths=["tests/test_a.py", "tests/test_missing.py"])
    out = _write_executor_output(
        tmp_workspace,
        file_manifest=["src/present.py"],
        tests_written=["tests/test_a.py"],
    )
    with open(out) as f:
        payload = json.load(f)
    payload["file_manifest"] = ["src/present.py", "src/ghost.py"]  # ghost not created
    with open(out, "w") as f:
        json.dump(payload, f)

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(out)

    assert result == "PASS"
    codes = _codes(_read_warnings(tmp_workspace))
    assert "ERR_MANIFEST_FILE_MISSING" in codes
    assert "ERR_TDD_COVERAGE_MISMATCH" in codes


def test_clean_pass_writes_no_warnings_file(tmp_workspace, monkeypatch):
    """A fully-valid output → PASS and NO gate_warnings.json on disk."""
    _stub_git_no_deletions(monkeypatch)
    _write_pipeline_state(tmp_workspace)
    _write_current_phase(tmp_workspace)
    _write_planner_output(tmp_workspace, tdd_paths=["tests/test_a.py"])
    out = _write_executor_output(
        tmp_workspace, file_manifest=["src/m.py"], tests_written=["tests/test_a.py"],
    )

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(out)

    assert result == "PASS"
    assert _read_warnings(tmp_workspace) is None, "no warnings → no gate_warnings.json"


def test_stale_gate_warnings_cleared_at_start(tmp_workspace, monkeypatch):
    """A stale gate_warnings.json from a prior attempt must be wiped at gate
    start, so a now-clean run leaves no file."""
    _stub_git_no_deletions(monkeypatch)
    _write_pipeline_state(tmp_workspace)
    _write_current_phase(tmp_workspace)
    _write_planner_output(tmp_workspace)
    out = _write_executor_output(tmp_workspace, file_manifest=["src/m.py"])
    # Pre-seed a stale warnings file.
    with open(os.path.join(tmp_workspace, "gate_warnings.json"), "w") as f:
        json.dump({"phase_raw_id": "OLD", "warnings": [{"code": "ERR_TDD_COVERAGE_MISMATCH"}]}, f)

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(out)

    assert result == "PASS"
    assert _read_warnings(tmp_workspace) is None, "stale gate_warnings.json must be cleared"


# ---------------------------------------------------------------------------
# Hard-keep: interleaved ERR_PATH_TRAVERSAL must STILL FAIL (load-bearing)
# ---------------------------------------------------------------------------


def test_path_traversal_in_manifest_still_fails_hard(tmp_workspace, monkeypatch):
    """The security boundary is not demoted: a manifest path escaping the
    workspace must hard-FAIL with ERR_PATH_TRAVERSAL and write NO warnings file."""
    _stub_git_no_deletions(monkeypatch)
    _write_pipeline_state(tmp_workspace)
    _write_current_phase(tmp_workspace)
    _write_planner_output(tmp_workspace)
    out = _write_executor_output(tmp_workspace, file_manifest=[], create_manifest_files=False)
    with open(out) as f:
        payload = json.load(f)
    payload["file_manifest"] = ["../escape.py"]
    with open(out, "w") as f:
        json.dump(payload, f)

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(out)

    assert result == "FAIL", "path traversal in file_manifest must remain a hard FAIL"
    assert _read_state(tmp_workspace).get("last_error_code") == "ERR_PATH_TRAVERSAL"
    assert _read_warnings(tmp_workspace) is None, "a hard FAIL must not emit gate_warnings.json"


def test_path_traversal_in_behavioral_artifacts_still_fails_hard(tmp_workspace, monkeypatch):
    _stub_git_no_deletions(monkeypatch)
    _write_pipeline_state(tmp_workspace)
    _write_current_phase(tmp_workspace, behavioral=True)
    _write_planner_output(tmp_workspace)
    out = _write_executor_output(
        tmp_workspace, file_manifest=["src/m.py"],
        behavioral_artifacts=[{"path": "../escape.png", "description": "escapes"}],
    )

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(out)

    assert result == "FAIL", "path traversal in behavioral_smoke_artifacts must remain a hard FAIL"
    assert _read_state(tmp_workspace).get("last_error_code") == "ERR_PATH_TRAVERSAL"
