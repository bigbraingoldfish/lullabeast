"""Phase 3 (gate-feedback methodology) — orchestrator drains gate_warnings.json.

``_emit_gate_warnings(raw_id)`` runs on the executor-PASS path. It:

* emits exactly one summarising ``gate_warning`` event (codes + count) when the
  executor gate recorded warnings;
* stashes a compact ``last_gate_warnings`` summary onto phase_state so the
  canonical metrics row can persist it;
* **does NOT remove gate_warnings.json** — the reviewer reads it next (the one
  deliberate divergence from ``_emit_reachability_advisory``, which removes its
  file);
* clears a stale stash on a clean pass (no file) so the metrics row is accurate.

The metrics row then surfaces the stash under ``gate_warnings``.

Idiom mirrors test_orchestrator_reachability_events.py / test_phase3_reachability_stash.py
/ test_phase3_metrics_row_pain_signals.py.
"""

import json
import os
import re
import sys

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


def _make_orchestrator():
    return orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)


def _write_warnings(tmp_path, payload):
    path = os.path.join(str(tmp_path), "gate_warnings.json")
    with open(path, "w") as f:
        json.dump(payload, f)
    return path


def _read_ps(tmp_path):
    p = tmp_path / "phase_state.json"
    return json.loads(p.read_text()) if p.exists() else {}


@pytest.fixture
def gw_env(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json"))
    captured = []
    monkeypatch.setattr(
        orch_mod, "_write_pipeline_event",
        lambda et, ph, ag, det: captured.append((et, ph, ag, det)),
    )
    return tmp_path, captured


_SAMPLE = {
    "phase_raw_id": "CORE-E1",
    "warnings": [
        {"code": "ERR_MANIFEST_FILE_MISSING", "detail": "x", "files": ["src/ghost.py"]},
        {"code": "ERR_TDD_COVERAGE_MISMATCH", "detail": "y", "missing_tests": ["tests/test_b.py"]},
    ],
}


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


def test_one_gate_warning_event_with_codes_and_count(gw_env):
    """Two warnings → exactly ONE summarising gate_warning event (not N), carrying
    the sorted code set and total count for the activity feed."""
    tmp_path, captured = gw_env
    _write_warnings(tmp_path, _SAMPLE)
    _make_orchestrator()._emit_gate_warnings("CORE-E1")

    assert len(captured) == 1, f"expected one gate_warning event, got {captured!r}"
    et, ph, ag, det = captured[0]
    assert et == "gate_warning"
    assert ph == "CORE-E1"
    assert ag == "executor"
    assert det.get("count") == 2
    assert det.get("codes") == ["ERR_MANIFEST_FILE_MISSING", "ERR_TDD_COVERAGE_MISMATCH"]
    assert "src/ghost.py" in det.get("files", [])
    assert "tests/test_b.py" in det.get("files", [])


def test_gate_warnings_file_preserved_for_reviewer(gw_env):
    """LOAD-BEARING divergence from reachability: the file MUST remain on disk
    after draining — the reviewer reads it to adjudicate."""
    tmp_path, _ = gw_env
    _write_warnings(tmp_path, _SAMPLE)
    _make_orchestrator()._emit_gate_warnings("CORE-E1")
    assert os.path.exists(os.path.join(str(tmp_path), "gate_warnings.json")), (
        "gate_warnings.json must NOT be removed — the reviewer still needs it"
    )


def test_no_file_emits_nothing(gw_env):
    """Clean pass (no warnings file) → no event."""
    tmp_path, captured = gw_env
    _make_orchestrator()._emit_gate_warnings("CORE-E1")
    assert captured == []


def test_empty_warnings_list_emits_nothing(gw_env):
    """A file with an empty warnings list is defensively treated as no-warnings."""
    tmp_path, captured = gw_env
    _write_warnings(tmp_path, {"phase_raw_id": "CORE-E1", "warnings": []})
    _make_orchestrator()._emit_gate_warnings("CORE-E1")
    assert captured == []


# ---------------------------------------------------------------------------
# phase_state stash
# ---------------------------------------------------------------------------


def test_stash_compact_summary(gw_env):
    """Stash is compact (count + codes only) — per-file detail rode out on the
    event, the row stays small."""
    tmp_path, _ = gw_env
    _write_warnings(tmp_path, _SAMPLE)
    _make_orchestrator()._emit_gate_warnings("CORE-E1")
    assert _read_ps(tmp_path).get("last_gate_warnings") == {
        "count": 2,
        "codes": ["ERR_MANIFEST_FILE_MISSING", "ERR_TDD_COVERAGE_MISMATCH"],
    }


def test_stash_preserves_existing_phase_state_keys(gw_env):
    """Read-modify-write — sibling keys survive the stash."""
    tmp_path, _ = gw_env
    (tmp_path / "phase_state.json").write_text(json.dumps({"executor_retries": 3}))
    _write_warnings(tmp_path, _SAMPLE)
    _make_orchestrator()._emit_gate_warnings("CORE-E1")
    ps = _read_ps(tmp_path)
    assert ps.get("executor_retries") == 3
    assert ps.get("last_gate_warnings") is not None


def test_clean_pass_clears_stale_stash(gw_env):
    """No warnings file, but a stale last_gate_warnings from a prior attempt is
    present → it must be cleared so the metrics row doesn't report stale data."""
    tmp_path, _ = gw_env
    (tmp_path / "phase_state.json").write_text(
        json.dumps({"executor_retries": 1, "last_gate_warnings": {"count": 9, "codes": ["X"]}})
    )
    _make_orchestrator()._emit_gate_warnings("CORE-E1")
    ps = _read_ps(tmp_path)
    assert "last_gate_warnings" not in ps, "stale stash must be cleared on a clean pass"
    assert ps.get("executor_retries") == 1, "clearing must not wipe sibling keys"


# ---------------------------------------------------------------------------
# Structural — call site on the PASS path + file in the artifact-lifecycle lists
# ---------------------------------------------------------------------------


def test_emit_called_on_executor_pass_path():
    """_emit_gate_warnings must be invoked on the executor PASS path so warnings
    reach the feed/stash before the orchestrator moves on to the reviewer."""
    assert re.search(r"self\._emit_gate_warnings\(", _ORCH_SRC), (
        "orchestrator must invoke _emit_gate_warnings on the executor PASS path"
    )


def test_gate_warnings_in_artifact_lifecycle_lists():
    """gate_warnings.json must appear in the same per-phase artifact-lifecycle
    sites as its siblings (reset_phase, reset_execution, write_failure_context's
    _pipeline_meta exclude-set, and the phase-complete cleanup) so a fresh
    phase/attempt never inherits a prior phase's warnings. Four sites → >= 4."""
    occurrences = len(re.findall(r'"gate_warnings\.json"', _ORCH_SRC))
    assert occurrences >= 4, (
        "gate_warnings.json must be enumerated in all four per-phase artifact "
        f"sites; found only {occurrences}."
    )


# ---------------------------------------------------------------------------
# Metrics row surfaces the stash
# ---------------------------------------------------------------------------


def _drive_writer(tmp_path, monkeypatch, phase_state_extra=None):
    """Seed phase_state, drive _write_canonical_metrics_row, return the parsed
    row. Copied from test_phase3_metrics_row_pain_signals.py's idiom (test files
    stay self-contained)."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pipeline_root = tmp_path / "pipeline_root"
    pipeline_root.mkdir()
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(artifacts))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(pipeline_root))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(artifacts / "phase_state.json"))

    phase_state = {
        "executor_retries": 0, "executor_self_failure_retries": 0,
        "executor_reviewer_rejection_retries": 0, "reviewer_retries": 0,
        "planner_tokens_acc": {}, "executor_tokens_acc": {}, "reviewer_tokens_acc": {},
        "blame_fires": 0, "escalations": 0, "skill_injected": "core-logic",
    }
    if phase_state_extra:
        phase_state.update(phase_state_extra)
    (artifacts / "phase_state.json").write_text(json.dumps(phase_state))
    (artifacts / "current_phase.json").write_text(json.dumps(
        {"raw_id": "CORE-E1", "detail": "Phase CORE-E1"}))

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {
        "current_phase": 1, "current_phase_raw_id": "CORE-E1",
        "executor_retries": 0, "reviewer_retries": 0,
        "executor_self_failure_retries": 0, "executor_reviewer_rejection_retries": 0,
        "phase_start_time": "2026-05-22T00:00:00+00:00",
    }
    orch.openclaw_config = {}
    orch.lock_fd = None
    orch._write_canonical_metrics_row()

    lines = [l for l in (artifacts / "metrics.jsonl").read_text().splitlines() if l.strip()]
    assert lines
    return json.loads(lines[-1])


def test_row_includes_gate_warnings_when_stashed(tmp_path, monkeypatch):
    stash = {"count": 2, "codes": ["ERR_MANIFEST_FILE_MISSING", "ERR_TDD_COVERAGE_MISMATCH"]}
    row = _drive_writer(tmp_path, monkeypatch, {"last_gate_warnings": stash})
    assert row.get("gate_warnings") == stash


def test_row_gate_warnings_null_when_absent(tmp_path, monkeypatch):
    """A phase that raised no warnings carries the key as null (present, null) —
    additive, backward-compatible."""
    row = _drive_writer(tmp_path, monkeypatch)
    assert "gate_warnings" in row
    assert row["gate_warnings"] is None
