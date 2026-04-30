"""
W1-D: failure_history.jsonl — append log of overwritten failure_context.json entries.

Tests mix source-text/AST checks (structural) with runtime tests using tmp_path
(functional — the file writer must actually work).
"""
import json
import os
import pathlib
import sys
import importlib

import pytest

_ORCH_PATH = pathlib.Path(__file__).parent.parent / "pipeline" / "orchestrator.py"
_SRC = _ORCH_PATH.read_text()
_LINES = _SRC.splitlines()

# ---------------------------------------------------------------------------
# Structural checks (source text)
# ---------------------------------------------------------------------------

def test_append_failure_history_helper_exists():
    """_append_failure_history must be defined in orchestrator source."""
    assert "_append_failure_history" in _SRC, (
        "_append_failure_history helper not found in orchestrator.py. "
        "Add a method or module-level function that appends old failure_context "
        "to failure_history.jsonl before overwriting it."
    )


def test_failure_history_jsonl_path_in_source():
    """'failure_history.jsonl' must appear in orchestrator source."""
    assert "failure_history.jsonl" in _SRC, (
        "failure_history.jsonl not referenced in orchestrator. "
        "Add append logic writing to PROJECT_ARTIFACTS_DIR/failure_history.jsonl."
    )


def test_failure_history_uses_append_mode():
    """failure_history.jsonl must be opened in append mode ('a'), not atomic-rename."""
    # Find the _append_failure_history helper body
    helper_start = None
    for i, ln in enumerate(_LINES):
        if "def _append_failure_history" in ln:
            helper_start = i
            break
    assert helper_start is not None, "_append_failure_history not found."

    # Extract ~50 lines of the helper body
    helper_body = _LINES[helper_start : helper_start + 50]
    helper_src = "\n".join(helper_body)

    # The helper must open a file in append mode
    assert ('"a"' in helper_src or "'a'" in helper_src), (
        "No open(..., 'a') (append mode) found inside _append_failure_history. "
        "Use O_APPEND — not atomic-rename — for this append log."
    )


def test_failure_history_os_replace_not_used_for_history():
    """os.replace must NOT be used for the failure_history.jsonl write path."""
    lines = _LINES
    # Find lines referencing failure_history that also use os.replace
    bad_lines = [
        ln for ln in lines
        if "failure_history" in ln and "os.replace" in ln
    ]
    assert not bad_lines, (
        "os.replace found on a line mentioning failure_history. "
        "failure_history.jsonl is an append log — use open('a'), not atomic-rename."
    )


def test_append_failure_history_called_before_os_replace_in_write_failure_context():
    """_append_failure_history must be called before os.replace in write_failure_context."""
    # Find write_failure_context method body
    wfc_start = None
    for i, ln in enumerate(_LINES):
        if "def write_failure_context" in ln:
            wfc_start = i
            break
    assert wfc_start is not None, "write_failure_context method not found."

    # Extract method body (up to next def at same or lower indent)
    method_lines = []
    for i, ln in enumerate(_LINES[wfc_start + 1:], wfc_start + 1):
        stripped = ln.lstrip()
        if stripped.startswith("def ") and not _LINES[i].startswith(" "):
            break
        method_lines.append((i, ln))

    append_call_lines = [i for i, ln in method_lines if "_append_failure_history" in ln]
    replace_call_lines = [i for i, ln in method_lines if "os.replace" in ln]

    assert append_call_lines, (
        "_append_failure_history not called inside write_failure_context. "
        "Insert the call before the os.replace that overwrites failure_context.json."
    )
    assert replace_call_lines, "os.replace not found in write_failure_context."

    assert min(append_call_lines) < min(replace_call_lines), (
        f"_append_failure_history (line {min(append_call_lines)}) appears AFTER "
        f"os.replace (line {min(replace_call_lines)}) in write_failure_context. "
        "The helper must run first so it can read the old file before it is overwritten."
    )


def test_failure_history_includes_phase_state_diagnostics_in_source():
    """_append_failure_history must reference last_error_code and escalation_trigger_reason."""
    assert "last_error_code" in _SRC or "last_error_code" in _SRC, True  # always true sanity
    lines_with_helper = []
    in_helper = False
    for ln in _LINES:
        if "_append_failure_history" in ln and "def " in ln:
            in_helper = True
        if in_helper:
            lines_with_helper.append(ln)
            if len(lines_with_helper) > 60:
                break

    helper_src = "\n".join(lines_with_helper)
    assert "last_error_code" in helper_src, (
        "_append_failure_history does not reference last_error_code. "
        "Include last_error_code from phase_state in each history entry."
    )
    assert "escalation_trigger_reason" in helper_src, (
        "_append_failure_history does not reference escalation_trigger_reason. "
        "Include escalation_trigger_reason from phase_state in each history entry."
    )


# ---------------------------------------------------------------------------
# Runtime tests (functional)
# ---------------------------------------------------------------------------

def _import_orchestrator_module():
    """Import orchestrator module, resolving its sibling imports via sys.path."""
    pipeline_dir = str(_ORCH_PATH.parent)
    if pipeline_dir not in sys.path:
        sys.path.insert(0, pipeline_dir)
    # Force reload to pick up any freshly-patched version
    if "orchestrator" in sys.modules:
        del sys.modules["orchestrator"]
    return importlib.import_module("orchestrator")


@pytest.fixture()
def orch_module():
    return _import_orchestrator_module()


def _minimal_orchestrator(orch_mod, tmp_path):
    """Construct a bare Orchestrator instance with all paths redirected to tmp_path."""
    import unittest.mock as mock
    # Patch module-level constants so file writes land in tmp_path
    with mock.patch.object(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path)):
        with mock.patch.object(orch_mod, "PHASE_STATE_FILE",
                               str(tmp_path / "phase_state.json")):
            with mock.patch.object(orch_mod, "OPENCLAW_ROOT", str(tmp_path)):
                with mock.patch.object(orch_mod, "SYMLINK_TARGET", str(tmp_path)):
                    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
                    orch.state = {
                        "pipeline_status": "RUNNING",
                        "status": "RUNNING",
                        "current_agent": "executor",
                        "current_phase": 1,
                        "current_phase_raw_id": "CORE-E1",
                        "executor_retries": 0,
                        "planner_retries": 0,
                        "reviewer_retries": 0,
                    }
                    orch.openclaw_config = {"hooks": {"token": ""}}
                    return orch


def test_failure_history_appends_existing_content(tmp_path, orch_module):
    """write_failure_context must append the OLD failure_context to failure_history.jsonl."""
    import unittest.mock as mock

    failure_context_path = tmp_path / "failure_context.json"
    history_path = tmp_path / "failure_history.jsonl"
    phase_state_path = tmp_path / "phase_state.json"

    # Pre-populate an existing failure_context.json
    old_entry = {"phase_raw_id": "CORE-E1", "failing_agent": "executor", "attempt_number": 1}
    failure_context_path.write_text(json.dumps(old_entry))

    # Write phase_state.json
    phase_state_path.write_text(json.dumps({"executor_retries": 1}))

    orch = _minimal_orchestrator(orch_module, tmp_path)

    with (
        mock.patch.object(orch_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
        mock.patch.object(orch_module, "PHASE_STATE_FILE", str(phase_state_path)),
        mock.patch.object(orch_module, "SYMLINK_TARGET", str(tmp_path)),
        mock.patch.object(orch_module, "OPENCLAW_ROOT", str(tmp_path)),
        mock.patch.object(orch_module, "AUTODEV_PIPELINE_ROOT", str(tmp_path), create=True),
    ):
        # Call _append_failure_history directly
        orch._append_failure_history(str(failure_context_path))

    assert history_path.exists(), "failure_history.jsonl was not created."
    lines = [l for l in history_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1, f"Expected 1 history entry, got {len(lines)}."
    entry = json.loads(lines[0])
    assert entry.get("phase_raw_id") == "CORE-E1", "Old failure_context fields not preserved."
    assert entry.get("failing_agent") == "executor"


def test_failure_history_accumulates_across_calls(tmp_path, orch_module):
    """Multiple write_failure_context calls must accumulate lines in failure_history.jsonl."""
    import unittest.mock as mock

    failure_context_path = tmp_path / "failure_context.json"
    history_path = tmp_path / "failure_history.jsonl"
    phase_state_path = tmp_path / "phase_state.json"
    phase_state_path.write_text(json.dumps({}))

    orch = _minimal_orchestrator(orch_module, tmp_path)

    with (
        mock.patch.object(orch_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
        mock.patch.object(orch_module, "PHASE_STATE_FILE", str(phase_state_path)),
        mock.patch.object(orch_module, "SYMLINK_TARGET", str(tmp_path)),
        mock.patch.object(orch_module, "OPENCLAW_ROOT", str(tmp_path)),
        mock.patch.object(orch_module, "AUTODEV_PIPELINE_ROOT", str(tmp_path), create=True),
    ):
        for attempt in range(1, 4):
            failure_context_path.write_text(json.dumps({"attempt": attempt}))
            orch._append_failure_history(str(failure_context_path))

    lines = [l for l in history_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 3, f"Expected 3 history entries, got {len(lines)}."


def test_failure_history_graceful_on_no_prior_file(tmp_path, orch_module):
    """_append_failure_history must silently return when failure_context.json doesn't exist."""
    import unittest.mock as mock

    failure_context_path = tmp_path / "failure_context.json"
    history_path = tmp_path / "failure_history.jsonl"
    phase_state_path = tmp_path / "phase_state.json"
    phase_state_path.write_text(json.dumps({}))

    orch = _minimal_orchestrator(orch_module, tmp_path)

    with (
        mock.patch.object(orch_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
        mock.patch.object(orch_module, "PHASE_STATE_FILE", str(phase_state_path)),
        mock.patch.object(orch_module, "SYMLINK_TARGET", str(tmp_path)),
        mock.patch.object(orch_module, "OPENCLAW_ROOT", str(tmp_path)),
        mock.patch.object(orch_module, "AUTODEV_PIPELINE_ROOT", str(tmp_path), create=True),
    ):
        # No exception expected
        orch._append_failure_history(str(failure_context_path))

    # Nothing should be written when there was no prior file
    assert not history_path.exists(), (
        "failure_history.jsonl should not be created when failure_context.json is absent."
    )


def test_failure_history_includes_phase_state_diagnostics(tmp_path, orch_module):
    """History entries must include last_error_code and escalation_trigger_reason from phase_state."""
    import unittest.mock as mock

    failure_context_path = tmp_path / "failure_context.json"
    history_path = tmp_path / "failure_history.jsonl"
    phase_state_path = tmp_path / "phase_state.json"

    failure_context_path.write_text(json.dumps({"phase_raw_id": "CORE-E1"}))
    phase_state_path.write_text(json.dumps({
        "last_error_code": "ERR_GATE_FAIL",
        "escalation_trigger_reason": "reviewer timeout",
    }))

    orch = _minimal_orchestrator(orch_module, tmp_path)

    with (
        mock.patch.object(orch_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
        mock.patch.object(orch_module, "PHASE_STATE_FILE", str(phase_state_path)),
        mock.patch.object(orch_module, "SYMLINK_TARGET", str(tmp_path)),
        mock.patch.object(orch_module, "OPENCLAW_ROOT", str(tmp_path)),
        mock.patch.object(orch_module, "AUTODEV_PIPELINE_ROOT", str(tmp_path), create=True),
    ):
        orch._append_failure_history(str(failure_context_path))

    entry = json.loads(history_path.read_text().strip())
    assert entry.get("last_error_code") == "ERR_GATE_FAIL", (
        "last_error_code not found in failure_history entry."
    )
    assert entry.get("escalation_trigger_reason") == "reviewer timeout", (
        "escalation_trigger_reason not found in failure_history entry."
    )
