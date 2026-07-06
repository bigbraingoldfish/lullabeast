"""Gate-side phase_state.json corruption handling (audit M2).

The gate-side merge-and-rewrite sites (``record_error_code_only``,
``update_phase_state_error``, the executor gate's ``executor_succeeded``
write) used to swallow a parse failure and rebuild from ``{}``, atomically
replacing a corrupt phase_state.json with a *valid* file that had lost every
governance counter — ``executor_retries``, the cap-governed
``escalation_resets`` / ``nuclear_resets``, ``reviewer_unverified_retries``,
``reset_log``, the ``phase_merged`` marker. Because a gate runs before the
orchestrator's next read, the orchestrator's own quarantine-and-raise
(``read_phase_state``) never saw the corruption; it saw a healthy file with
all budgets reset to zero.

The shared ``read_phase_state_for_rewrite`` now distinguishes "file absent"
(legitimate fresh start, ``{}``) from "present but unparseable / non-dict"
(``None`` — the caller skips its rewrite and leaves the corrupt file in
place for the orchestrator's quarantine path).

Idiom mirrors test_executor_gate_demoted_warnings.py: patch the workspace
globals, stub subprocess.run so the deletion guard PASSes, call
``evaluate_executor`` directly.
"""

import json
import os
from contextlib import ExitStack
from unittest.mock import patch

import utils as utils_module
import executor_gate as executor_gate_module


CORRUPT_BYTES = '{"executor_retries": 2, "escalation_res'  # truncated write


def _patch_phase_state(tmp_dir):
    stack = ExitStack()
    tmp_dir_with_sep = tmp_dir.rstrip(os.sep) + os.sep
    ps = os.path.join(tmp_dir_with_sep.rstrip(os.sep), "phase_state.json")
    stack.enter_context(patch.object(utils_module, "ARTIFACTS_DIR", tmp_dir_with_sep))
    stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", ps))
    return stack, ps


# ---------------------------------------------------------------------------
# read_phase_state_for_rewrite — the shared reader
# ---------------------------------------------------------------------------


def test_reader_absent_file_returns_empty_dict(tmp_workspace):
    stack, _ = _patch_phase_state(tmp_workspace)
    with stack:
        assert utils_module.read_phase_state_for_rewrite() == {}


def test_reader_valid_file_returns_contents(tmp_workspace):
    stack, ps = _patch_phase_state(tmp_workspace)
    with open(ps, "w") as f:
        json.dump({"executor_retries": 2, "escalation_resets": 3}, f)
    with stack:
        assert utils_module.read_phase_state_for_rewrite() == {
            "executor_retries": 2,
            "escalation_resets": 3,
        }


def test_reader_corrupt_file_returns_none_and_leaves_file(tmp_workspace, capsys):
    stack, ps = _patch_phase_state(tmp_workspace)
    with open(ps, "w") as f:
        f.write(CORRUPT_BYTES)
    with stack:
        assert utils_module.read_phase_state_for_rewrite() is None
    assert open(ps).read() == CORRUPT_BYTES, "corrupt file must be left in place"
    assert "unreadable" in capsys.readouterr().err


def test_reader_non_dict_json_returns_none(tmp_workspace, capsys):
    """A parseable-but-non-dict file (a JSON list) is the same hazard as
    unparseable: merging keys into it would crash or wipe state."""
    stack, ps = _patch_phase_state(tmp_workspace)
    with open(ps, "w") as f:
        json.dump([1, 2, 3], f)
    with stack:
        assert utils_module.read_phase_state_for_rewrite() is None
    assert "not a dict" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# record_error_code_only
# ---------------------------------------------------------------------------


def test_record_error_code_skips_rewrite_on_corrupt_state(tmp_workspace):
    stack, ps = _patch_phase_state(tmp_workspace)
    with open(ps, "w") as f:
        f.write(CORRUPT_BYTES)
    with stack:
        utils_module.record_error_code_only("executor", "ERR_TESTS_FAILING")
    assert open(ps).read() == CORRUPT_BYTES, (
        "a corrupt phase_state must NOT be replaced by a rebuilt-from-{} file "
        "(that silently zeroes every governance counter)"
    )


def test_record_error_code_absent_file_still_creates(tmp_workspace):
    """Absent file stays a legitimate fresh start — pre-M2 behavior preserved."""
    stack, ps = _patch_phase_state(tmp_workspace)
    with stack:
        utils_module.record_error_code_only("executor", "ERR_TESTS_FAILING")
    with open(ps) as f:
        assert json.load(f)["last_error_code"] == "ERR_TESTS_FAILING"


def test_record_error_code_valid_file_merges_and_preserves_counters(tmp_workspace):
    stack, ps = _patch_phase_state(tmp_workspace)
    with open(ps, "w") as f:
        json.dump({"executor_retries": 2, "nuclear_resets": 1}, f)
    with stack:
        utils_module.record_error_code_only(
            "reviewer", "ERR_VISUAL_UNVERIFIED",
            detail={"problems": ["x"]}, detail_field="reviewer_unverified_detail",
        )
    with open(ps) as f:
        state = json.load(f)
    assert state["executor_retries"] == 2
    assert state["nuclear_resets"] == 1
    assert state["last_error_code"] == "ERR_VISUAL_UNVERIFIED"
    assert state["reviewer_unverified_detail"] == {"problems": ["x"]}


# ---------------------------------------------------------------------------
# update_phase_state_error
# ---------------------------------------------------------------------------


def test_update_phase_state_error_skips_rewrite_on_corrupt_state(tmp_workspace):
    stack, ps = _patch_phase_state(tmp_workspace)
    with open(ps, "w") as f:
        f.write(CORRUPT_BYTES)
    with stack:
        result = utils_module.update_phase_state_error("executor", "ERR_TESTS_FAILING")
    assert result is None
    assert open(ps).read() == CORRUPT_BYTES


def test_update_phase_state_error_valid_file_bumps_and_returns(tmp_workspace):
    stack, ps = _patch_phase_state(tmp_workspace)
    with open(ps, "w") as f:
        json.dump({"executor_retries": 1, "escalation_resets": 2}, f)
    with stack:
        result = utils_module.update_phase_state_error("executor", "ERR_TESTS_FAILING")
    assert result == 2
    with open(ps) as f:
        state = json.load(f)
    assert state["executor_retries"] == 2
    assert state["escalation_resets"] == 2, "unrelated counters must survive the merge"


# ---------------------------------------------------------------------------
# executor gate PASS path — the executor_succeeded write
# ---------------------------------------------------------------------------


def _drive_gate_to_pass(tmp_workspace, monkeypatch):
    """Minimal all-green fixture set so evaluate_executor reaches its PASS tail."""
    import subprocess

    class _Sub:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Sub())

    parent = os.path.dirname(tmp_workspace.rstrip(os.sep))
    with open(os.path.join(parent, "pipeline_state.json"), "w") as f:
        json.dump({"phase_base_commit": "abc123"}, f)
    with open(os.path.join(tmp_workspace, "current_phase.json"), "w") as f:
        json.dump({"phase_number": 1, "raw_id": "CORE-E1", "detail": "x",
                   "behavioral_verification": None}, f)
    with open(os.path.join(tmp_workspace, "planner_output.json"), "w") as f:
        json.dump({"implementation_plan": ["x"], "tdd_test_structure": [],
                   "pass_criteria": [{"condition": "x"}]}, f)
    src = os.path.join(tmp_workspace, "src")
    os.makedirs(src, exist_ok=True)
    with open(os.path.join(src, "present.py"), "w") as f:
        f.write("ok\n")
    out = os.path.join(tmp_workspace, "executor_output.json")
    with open(out, "w") as f:
        json.dump({"status": "complete", "tests_written": [],
                   "test_results": {"all_passing": True},
                   "file_manifest": ["src/present.py"], "files_deleted": []}, f)
    return out


def _patch_gate_workspace(tmp_dir):
    stack = ExitStack()
    tmp_dir_with_sep = tmp_dir.rstrip(os.sep) + os.sep
    ps = os.path.join(tmp_dir_with_sep.rstrip(os.sep), "phase_state.json")
    for mod in (utils_module, executor_gate_module):
        stack.enter_context(patch.object(mod, "WORKSPACE_DIR", tmp_dir_with_sep))
        stack.enter_context(patch.object(mod, "ARTIFACTS_DIR", tmp_dir_with_sep))
        stack.enter_context(patch.object(mod, "PHASE_STATE_FILE", ps))
    return stack, ps


def test_gate_pass_with_corrupt_phase_state_does_not_wipe_it(tmp_workspace, monkeypatch):
    """The PASS-tail executor_succeeded write must skip, not heal, a corrupt
    phase_state.json — the gate still PASSes (verdict is stdout-independent of
    this bookkeeping write) and the corrupt bytes stay for the orchestrator's
    quarantine path."""
    out = _drive_gate_to_pass(tmp_workspace, monkeypatch)
    stack, ps = _patch_gate_workspace(tmp_workspace)
    with open(ps, "w") as f:
        f.write(CORRUPT_BYTES)

    with stack:
        result = executor_gate_module.evaluate_executor(out)

    assert result == "PASS"
    assert open(ps).read() == CORRUPT_BYTES, (
        "corrupt phase_state must not be replaced by {'executor_succeeded': true}"
    )


def test_gate_pass_with_valid_phase_state_records_succeeded(tmp_workspace, monkeypatch):
    """Control: with a healthy phase_state the PASS tail still records
    executor_succeeded and preserves existing counters."""
    out = _drive_gate_to_pass(tmp_workspace, monkeypatch)
    stack, ps = _patch_gate_workspace(tmp_workspace)
    with open(ps, "w") as f:
        json.dump({"executor_retries": 1}, f)

    with stack:
        result = executor_gate_module.evaluate_executor(out)

    assert result == "PASS"
    with open(ps) as f:
        state = json.load(f)
    assert state["executor_succeeded"] is True
    assert state["executor_retries"] == 1
