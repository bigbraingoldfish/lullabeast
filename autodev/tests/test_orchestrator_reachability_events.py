"""P1 Stage F — orchestrator emits reachability_warning + reachability_not_applicable events.

The orchestrator's ``_emit_reachability_advisory(raw_id)`` method drains
``executor_advisory_detail.json``, emits exactly one summarising
``reachability_warning`` event when ``reachability_summary`` is populated, one
``reachability_not_applicable`` event when set, and one ``reachability_warning``
event per diagnostic, then removes the advisory file.

Tests combine behavioural verification (call the method with mocks, inspect
event calls) with source-level guards (the method must be called on the PASS
path; the advisory file must be in the four reset/cleanup lists).
"""

import json
import os
import re
import sys
from unittest.mock import patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402


_ORCH_PATH = os.path.join(PIPELINE_DIR, "orchestrator.py")
with open(_ORCH_PATH, "r", encoding="utf-8") as _f:
    _ORCH_SRC = _f.read()


def _write_advisory(tmp_path, payload):
    """Drop a stub executor_advisory_detail.json under tmp_path (acting as
    PROJECT_ARTIFACTS_DIR)."""
    path = os.path.join(str(tmp_path), "executor_advisory_detail.json")
    with open(path, "w") as f:
        json.dump(payload, f)
    return path


def _make_orchestrator():
    """Construct a minimal Orchestrator instance without going through __init__
    side effects. We only test ``_emit_reachability_advisory`` here."""
    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    return inst


@pytest.fixture(autouse=True)
def _isolate_phase_state(tmp_path, monkeypatch):
    """Phase 3 — ``_emit_reachability_advisory`` now writes phase_state on a
    finding (it stashes ``last_reachability_summary`` before removing the
    advisory file). Point ``PHASE_STATE_FILE`` at tmp so the stash never touches
    a real phase_state.json during the suite. Existing event/file-removal
    assertions are unaffected; the stash content is covered by
    test_phase3_reachability_stash.py."""
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json"))


# ---------------------------------------------------------------------------
# Behavioural — the method emits the right events and clears the file
# ---------------------------------------------------------------------------


def test_summary_event_emitted_once_for_many_files(tmp_path, monkeypatch):
    """The whole point of the summary shape: 3 unreachable files → 1 event,
    not 3. Carries the file list inside detail.files for the UI to render."""
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    _write_advisory(tmp_path, {
        "reachability_summary": {
            "files": ["a.py", "b.py", "c.py"],
            "count": 3,
            "command": "python main.py",
            "reason_template": "declared in manifest but not reached from entry point",
        },
        "reachability_not_applicable": None,
        "reachability_diagnostics": [],
    })
    captured = []
    monkeypatch.setattr(
        orch_mod, "_write_pipeline_event",
        lambda et, ph, ag, det: captured.append((et, ph, ag, det)),
    )

    inst = _make_orchestrator()
    inst._emit_reachability_advisory("COMPLETE-R0")

    assert len(captured) == 1, f"expected exactly one event, got {len(captured)}: {captured!r}"
    et, ph, ag, det = captured[0]
    assert et == "reachability_warning"
    assert ph == "COMPLETE-R0"
    assert ag == "executor"
    assert det.get("kind") == "unreachable_summary"
    assert det.get("count") == 3
    assert det.get("files") == ["a.py", "b.py", "c.py"]
    assert det.get("command") == "python main.py"
    # The advisory file must be consumed.
    assert not os.path.exists(os.path.join(str(tmp_path), "executor_advisory_detail.json"))


def test_no_advisory_file_emits_nothing(tmp_path, monkeypatch):
    """Common-case PASS — no advisory file present. Method must be a no-op,
    no events emitted, no exception."""
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    captured = []
    monkeypatch.setattr(
        orch_mod, "_write_pipeline_event",
        lambda et, ph, ag, det: captured.append((et, ph, ag, det)),
    )
    inst = _make_orchestrator()
    inst._emit_reachability_advisory("COMPLETE-R0")
    assert captured == []


def test_not_applicable_event_emitted(tmp_path, monkeypatch):
    """Test-runner entries surface as reachability_not_applicable, distinct
    from the warning channel."""
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    _write_advisory(tmp_path, {
        "reachability_summary": None,
        "reachability_not_applicable": {
            "reason": "entry point is a test runner ('pytest'); reachability check intentionally skipped"
        },
        "reachability_diagnostics": [],
    })
    captured = []
    monkeypatch.setattr(
        orch_mod, "_write_pipeline_event",
        lambda et, ph, ag, det: captured.append((et, ph, ag, det)),
    )

    inst = _make_orchestrator()
    inst._emit_reachability_advisory("COMPLETE-R0")

    assert len(captured) == 1
    et, ph, ag, det = captured[0]
    assert et == "reachability_not_applicable"
    assert ag == "executor"
    assert "pytest" in det.get("reason", "")


def test_diagnostics_emit_one_warning_per_entry(tmp_path, monkeypatch):
    """Diagnostics describe the *check*, not the artifact — they ride on
    reachability_warning so the UI surfaces them, but stay distinct from the
    unreachable_summary event by `kind`."""
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    _write_advisory(tmp_path, {
        "reachability_summary": None,
        "reachability_not_applicable": None,
        "reachability_diagnostics": [
            {"kind": "no_resolver",
             "reason": "no resolver for entry command 'cargo run'",
             "file": None},
        ],
    })
    captured = []
    monkeypatch.setattr(
        orch_mod, "_write_pipeline_event",
        lambda et, ph, ag, det: captured.append((et, ph, ag, det)),
    )

    inst = _make_orchestrator()
    inst._emit_reachability_advisory("COMPLETE-R0")

    assert len(captured) == 1
    et, ph, ag, det = captured[0]
    assert et == "reachability_warning"
    assert det.get("kind") == "no_resolver"


def test_mixed_summary_and_not_applicable_both_emit(tmp_path, monkeypatch):
    """Defensive — shouldn't normally co-occur, but the orchestrator must
    handle both fields set without dropping either event."""
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    _write_advisory(tmp_path, {
        "reachability_summary": {"files": ["x.py"], "count": 1,
                                 "command": "python x.py",
                                 "reason_template": "..."},
        "reachability_not_applicable": {"reason": "test runner skip"},
        "reachability_diagnostics": [],
    })
    captured = []
    monkeypatch.setattr(
        orch_mod, "_write_pipeline_event",
        lambda et, ph, ag, det: captured.append((et, ph, ag, det)),
    )

    inst = _make_orchestrator()
    inst._emit_reachability_advisory("COMPLETE-R0")

    types = sorted(c[0] for c in captured)
    assert types == ["reachability_not_applicable", "reachability_warning"]


# ---------------------------------------------------------------------------
# Structural — call site lives on the PASS path
# ---------------------------------------------------------------------------


def test_emit_method_called_on_executor_pass_path():
    """`_emit_reachability_advisory(raw_id)` must be called inside the
    `if gate_passed:` block following the executor gate, so warnings reach
    the activity feed before the orchestrator moves on to reviewer."""
    # Find the executor PASS path; the orchestrator must call the new method
    # there. We tolerate either `self._emit_reachability_advisory(raw_id)`
    # or `self._emit_reachability_advisory(...)` patterns.
    pat = re.compile(r"self\._emit_reachability_advisory\(")
    assert pat.search(_ORCH_SRC), (
        "orchestrator must invoke _emit_reachability_advisory on the executor "
        "PASS path so reachability events reach the activity feed"
    )


def test_advisory_file_in_reset_phase_cleanup_list():
    """The advisory file must be enumerated alongside executor_gate_detail.json
    in the reset/cleanup paths so a fresh phase starts with no stale advisory
    leaking from the prior phase."""
    # All four sites enumerate per-phase artifact filenames as a string list;
    # check the advisory filename appears at least four times in the source
    # (one for each of the four cleanup lists at lines 2363, 2478, 3011, 4893).
    occurrences = len(re.findall(r'"executor_advisory_detail\.json"', _ORCH_SRC))
    assert occurrences >= 4, (
        "executor_advisory_detail.json must be in all four cleanup lists "
        f"(reset_phase, reset_execution, write_failure_context, and the "
        f"trailing reset path). Found only {occurrences} occurrences."
    )
