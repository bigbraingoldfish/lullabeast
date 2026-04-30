"""
W1-F: pipeline_events.jsonl event writer.

Tests verify:
- _write_pipeline_event module-level helper exists with correct schema
- The helper is append-only and non-blocking
- Five call sites are present in orchestrator (gate_fail, gate_pass,
  escalation_trigger, escalation_resolve, phase_complete)

Pattern: runtime tests for the helper, source-text for the call sites.
"""
import json
import os
import pathlib
import sys
import importlib
import unittest.mock as mock

import pytest

_ORCH_PATH = pathlib.Path(__file__).parent.parent / "pipeline" / "orchestrator.py"
_SRC = _ORCH_PATH.read_text()
_LINES = _SRC.splitlines()


# ---------------------------------------------------------------------------
# Structural — source text checks
# ---------------------------------------------------------------------------

def test_write_pipeline_event_helper_exists():
    """_write_pipeline_event must be defined in orchestrator source."""
    assert "def _write_pipeline_event(" in _SRC or "_write_pipeline_event" in _SRC, (
        "_write_pipeline_event not found in orchestrator.py. "
        "Add a module-level helper that appends one JSON line to pipeline_events.jsonl."
    )


def test_gate_fail_event_called_at_multiple_gate_sites():
    """_write_pipeline_event('gate_fail', ...) must appear at >= 3 gate fail sites."""
    gate_fail_calls = _SRC.count('"gate_fail"')
    gate_fail_calls += _SRC.count("'gate_fail'")
    assert gate_fail_calls >= 3, (
        f"'gate_fail' event appears only {gate_fail_calls} time(s) in orchestrator — "
        "expected >= 3 (planner gate, executor gate, reviewer gate fail paths). "
        "Add _write_pipeline_event('gate_fail', ...) calls at each gate failure."
    )


def test_gate_pass_event_at_pipeline_complete():
    """_write_pipeline_event('gate_pass', ...) must appear near PIPELINE_COMPLETE."""
    assert '"gate_pass"' in _SRC or "'gate_pass'" in _SRC, (
        "'gate_pass' event not found in orchestrator. "
        "Add _write_pipeline_event('gate_pass', ...) when reviewer gate passes."
    )

    # Verify gate_pass appears in the reviewer branch (after "current_agent == "reviewer"" check).
    lines = _LINES
    reviewer_agent_linenos = [
        i for i, ln in enumerate(lines, 1)
        if 'current_agent == "reviewer"' in ln or "current_agent == 'reviewer'" in ln
    ]
    gate_pass_linenos = [
        i for i, ln in enumerate(lines, 1)
        if '"gate_pass"' in ln or "'gate_pass'" in ln
    ]
    # At least one gate_pass must appear after the reviewer branch start
    reviewer_start = min(reviewer_agent_linenos) if reviewer_agent_linenos else 0
    in_reviewer_branch = [gp for gp in gate_pass_linenos if gp > reviewer_start]
    assert in_reviewer_branch, (
        "No 'gate_pass' event found after the reviewer agent branch. "
        "Emit it right after `if gate_result == 'PASS':`."
    )


def test_escalation_trigger_event_at_waiting_for_human_sites():
    """'escalation_trigger' event must appear >= 2 times (once per WAITING_FOR_HUMAN site)."""
    count = _SRC.count('"escalation_trigger"')
    count += _SRC.count("'escalation_trigger'")
    assert count >= 2, (
        f"'escalation_trigger' event appears only {count} time(s) — "
        "expected >= 2 (one per WAITING_FOR_HUMAN escalation site)."
    )


def test_escalation_resolve_event_at_resolution_site():
    """'escalation_resolve' event must appear in orchestrator source."""
    assert '"escalation_resolve"' in _SRC or "'escalation_resolve'" in _SRC, (
        "'escalation_resolve' event not found in orchestrator. "
        "Add _write_pipeline_event('escalation_resolve', ...) when the human command is read."
    )


def test_phase_complete_event_near_metrics_write():
    """'phase_complete' event must appear near the metrics.jsonl write block."""
    assert '"phase_complete"' in _SRC or "'phase_complete'" in _SRC, (
        "'phase_complete' event not found in orchestrator. "
        "Add _write_pipeline_event('phase_complete', ...) at the canonical metrics row write."
    )

    lines = _LINES
    phase_complete_linenos = [
        i for i, ln in enumerate(lines, 1)
        if '"phase_complete"' in ln or "'phase_complete'" in ln
    ]
    metrics_write_linenos = [
        i for i, ln in enumerate(lines, 1)
        if "_metrics_path = os.path.join(PROJECT_ARTIFACTS_DIR" in ln
    ]
    close_enough = any(
        abs(pc - mw) <= 110
        for pc in phase_complete_linenos
        for mw in metrics_write_linenos
    )
    assert close_enough, (
        "No 'phase_complete' event found within 110 lines of the _metrics_path assignment. "
        "Emit phase_complete near the canonical metrics.jsonl write."
    )


# ---------------------------------------------------------------------------
# Runtime tests — the helper must actually work
# ---------------------------------------------------------------------------

def _load_write_pipeline_event():
    """Extract and import the module-level _write_pipeline_event from orchestrator."""
    pipeline_dir = str(_ORCH_PATH.parent)
    if pipeline_dir not in sys.path:
        sys.path.insert(0, pipeline_dir)
    if "orchestrator" in sys.modules:
        del sys.modules["orchestrator"]
    orch_mod = importlib.import_module("orchestrator")
    fn = getattr(orch_mod, "_write_pipeline_event", None)
    return orch_mod, fn


def test_write_pipeline_event_schema(tmp_path):
    """_write_pipeline_event must write a JSON line with the correct schema keys."""
    orch_mod, fn = _load_write_pipeline_event()
    assert fn is not None, "_write_pipeline_event not exported from orchestrator module."

    with mock.patch.object(orch_mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path)):
        fn("gate_fail", "CORE-E1", "executor", {"exit_code": 1})

    events_file = tmp_path / "pipeline_events.jsonl"
    assert events_file.exists(), "pipeline_events.jsonl was not created."

    lines = [l for l in events_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert set(entry.keys()) >= {"ts", "event", "project", "phase", "agent", "detail"}, (
        f"Missing required keys. Got: {set(entry.keys())}"
    )
    assert entry["event"] == "gate_fail"
    assert entry["phase"] == "CORE-E1"
    assert entry["agent"] == "executor"
    assert entry["detail"]["exit_code"] == 1


def test_write_pipeline_event_appends_multiple(tmp_path):
    """_write_pipeline_event must append — each call adds one line."""
    orch_mod, fn = _load_write_pipeline_event()
    assert fn is not None

    with mock.patch.object(orch_mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path)):
        fn("gate_fail", "CORE-E1", "executor", {})
        fn("gate_pass", "CORE-E1", "reviewer", {})
        fn("phase_complete", "CORE-E1", "reviewer", {"executor_attempts": 2})

    events_file = tmp_path / "pipeline_events.jsonl"
    lines = [l for l in events_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 3, f"Expected 3 events, got {len(lines)}."
    events = [json.loads(l)["event"] for l in lines]
    assert events == ["gate_fail", "gate_pass", "phase_complete"]


def test_write_pipeline_event_swallows_oserror(tmp_path):
    """_write_pipeline_event must not raise even when the write fails."""
    orch_mod, fn = _load_write_pipeline_event()
    assert fn is not None

    # Point at an unwritable location (a file, not a dir)
    blocker = tmp_path / "pipeline_events.jsonl"
    blocker.mkdir()  # make it a directory so open() fails

    with mock.patch.object(orch_mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path)):
        # Should not raise
        fn("gate_fail", "CORE-E1", "executor", {})


def test_write_pipeline_event_detail_defaults_to_empty_dict(tmp_path):
    """_write_pipeline_event should handle None detail gracefully."""
    orch_mod, fn = _load_write_pipeline_event()
    assert fn is not None

    with mock.patch.object(orch_mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path)):
        fn("phase_complete", "UI-E1", "reviewer", None)

    events_file = tmp_path / "pipeline_events.jsonl"
    entry = json.loads(events_file.read_text().strip())
    assert isinstance(entry["detail"], dict), "detail should be {} when None is passed."
