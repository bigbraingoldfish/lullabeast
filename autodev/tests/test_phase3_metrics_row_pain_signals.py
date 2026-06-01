"""Phase 3 — durable per-phase pain signals in the canonical metrics row.

The observability remediation (Phase 3) persists per-phase "pain signals"
into the canonical metrics row (and its mirror in
``metrics_history/<project>.jsonl``) so the completed-phase history can
answer "how painful was this phase, and how did it end." These fields are
read from the fresh on-disk ``phase_state`` at row-write time:

* ``escalation_resets`` / ``nuclear_resets`` / ``reviewer_unverified_retries``
  — per-phase counters that previously lived only in ``phase_state.json``
  (wiped on phase advance).
* ``reset_log`` — the operator-reset audit trail, snapshotted into the row
  before the phase-advance re-init drops it. The row writer runs on the
  reviewer-PASS path *before* ``phase_state.json`` is deleted, so the log is
  still present in ``ps_m`` at row-write time.
* ``reachability_summary`` — a compact copy of the executor reachability
  advisory, stashed onto ``phase_state`` by ``_emit_reachability_advisory``
  (Change #3) under the key ``last_reachability_summary`` and surfaced here.

All additive: readers tolerate unknown fields, defaults are safe, no
migration. A regression that drops a field or changes its default/source is
exactly what these tests catch.

Pattern mirrors ``test_p0_stage_h_metrics_row_breakdown.py``: seed
``phase_state.json``, drive ``_write_canonical_metrics_row()``, inspect the
written JSONL row.
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402


def _drive_writer(tmp_path, monkeypatch, phase_state_extra=None):
    """Set up a bare orchestrator, seed ``phase_state.json`` (with optional
    extra keys merged in), run ``_write_canonical_metrics_row``, and return
    ``(parsed_row, artifacts_dir, pipeline_root)``.

    Copied (not imported) from ``test_p0_stage_h_metrics_row_breakdown.py``'s
    idiom — test files stay self-contained.
    """
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pipeline_root = tmp_path / "pipeline_root"
    pipeline_root.mkdir()

    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(artifacts))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(pipeline_root))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(artifacts / "phase_state.json"))

    phase_state = {
        "executor_retries": 0,
        "executor_self_failure_retries": 0,
        "executor_reviewer_rejection_retries": 0,
        "reviewer_retries": 0,
        "planner_tokens_acc": {},
        "executor_tokens_acc": {},
        "reviewer_tokens_acc": {},
        "blame_fires": 0,
        "escalations": 0,
        "skill_injected": "core-logic",
    }
    if phase_state_extra:
        phase_state.update(phase_state_extra)
    (artifacts / "phase_state.json").write_text(json.dumps(phase_state))
    (artifacts / "current_phase.json").write_text(json.dumps({
        "raw_id": "CORE-E1",
        "detail": "Phase CORE-E1: bring up tasks view",
    }))

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {
        "current_phase": 1,
        "current_phase_raw_id": "CORE-E1",
        "executor_retries": 0,
        "reviewer_retries": 0,
        "executor_self_failure_retries": 0,
        "executor_reviewer_rejection_retries": 0,
        "phase_start_time": "2026-05-22T00:00:00+00:00",
    }
    orch.openclaw_config = {}
    orch.lock_fd = None

    orch._write_canonical_metrics_row()

    metrics_path = artifacts / "metrics.jsonl"
    assert metrics_path.exists(), "metrics.jsonl must be written"
    lines = [l for l in metrics_path.read_text().splitlines() if l.strip()]
    assert lines, "metrics.jsonl must contain at least one row"
    return json.loads(lines[-1]), artifacts, pipeline_root


# ---------------------------------------------------------------------------
# Change #1 — pain-signal counters in the row
# ---------------------------------------------------------------------------


def test_row_includes_escalation_resets(tmp_path, monkeypatch):
    """``escalation_resets`` from phase_state must surface in the row so the
    durable history records how many operator recover cycles a phase burned."""
    row, _, _ = _drive_writer(tmp_path, monkeypatch, {"escalation_resets": 2})
    assert row.get("escalation_resets") == 2


def test_row_includes_nuclear_resets(tmp_path, monkeypatch):
    """``nuclear_resets`` (the destructive escape-hatch cap counter) must be
    in the durable row."""
    row, _, _ = _drive_writer(tmp_path, monkeypatch, {"nuclear_resets": 1})
    assert row.get("nuclear_resets") == 1


def test_row_includes_reviewer_unverified_retries(tmp_path, monkeypatch):
    """The pooled contract-shape retry counter must be in the durable row."""
    row, _, _ = _drive_writer(tmp_path, monkeypatch, {"reviewer_unverified_retries": 2})
    assert row.get("reviewer_unverified_retries") == 2


def test_row_pain_signals_default_zero_when_absent(tmp_path, monkeypatch):
    """A phase that never escalated/nuclear-reset/contract-retried must still
    carry the keys (present, value 0) — additive schema, safe defaults. Catches
    a writer that only conditionally adds the keys."""
    row, _, _ = _drive_writer(tmp_path, monkeypatch)
    for key in ("escalation_resets", "nuclear_resets", "reviewer_unverified_retries"):
        assert key in row, f"{key} must always be present in the row"
        assert row[key] == 0, f"{key} must default to 0 when absent from phase_state"


# ---------------------------------------------------------------------------
# Change #2 — reset_log snapshot in the row (durable before the advance wipe)
# ---------------------------------------------------------------------------


def test_row_includes_reset_log_snapshot(tmp_path, monkeypatch):
    """The operator-reset audit trail must be snapshotted verbatim into the
    row, because the live ``reset_log`` in phase_state is wiped on phase
    advance and would otherwise be lost from the historical record."""
    reset_log = [
        {"reset_number": 1, "command": "RESET_EXECUTION", "reason": "x",
         "timestamp": "2026-06-01T00:00:00+00:00"},
        {"reset_number": 1, "command": "NUCLEAR_RESET", "reason": "y",
         "timestamp": "2026-06-01T01:00:00+00:00"},
    ]
    row, _, _ = _drive_writer(tmp_path, monkeypatch, {"reset_log": reset_log})
    assert row.get("reset_log") == reset_log


def test_row_reset_log_empty_when_absent(tmp_path, monkeypatch):
    """A phase with no resets must carry an empty list (present, not null)."""
    row, _, _ = _drive_writer(tmp_path, monkeypatch)
    assert "reset_log" in row
    assert row["reset_log"] == []


# ---------------------------------------------------------------------------
# Change #3 (read side) — reachability_summary surfaced from the stash
# ---------------------------------------------------------------------------


def test_row_includes_reachability_summary_when_stashed(tmp_path, monkeypatch):
    """When ``_emit_reachability_advisory`` has stashed
    ``last_reachability_summary`` onto phase_state, the row must surface it
    under ``reachability_summary``."""
    stash = {"kind": "unreachable_summary", "count": 3,
             "files": ["a.py", "b.py", "c.py"], "command": "python main.py"}
    row, _, _ = _drive_writer(tmp_path, monkeypatch, {"last_reachability_summary": stash})
    assert row.get("reachability_summary") == stash


def test_row_reachability_summary_null_when_absent(tmp_path, monkeypatch):
    """A phase with no reachability findings must carry the key as ``None``
    (present, null) — additive and backward-compatible."""
    row, _, _ = _drive_writer(tmp_path, monkeypatch)
    assert "reachability_summary" in row
    assert row["reachability_summary"] is None


# ---------------------------------------------------------------------------
# Durability — the orchestrator-private history mirror carries the new fields
# ---------------------------------------------------------------------------


def test_row_pain_signals_mirrored_in_history_file(tmp_path, monkeypatch):
    """All new fields must also land in ``metrics_history/<project>.jsonl``
    (the orchestrator-private append-only durable copy), not just the
    project-visible ``metrics.jsonl`` the executor can overwrite."""
    stash = {"kind": "unreachable_summary", "count": 1,
             "files": ["x.py"], "command": "python x.py"}
    reset_log = [{"reset_number": 1, "command": "RESET_PHASE", "reason": "z",
                  "timestamp": "2026-06-01T00:00:00+00:00"}]
    _drive_writer(tmp_path, monkeypatch, {
        "escalation_resets": 1,
        "nuclear_resets": 2,
        "reviewer_unverified_retries": 1,
        "reset_log": reset_log,
        "last_reachability_summary": stash,
    })

    artifacts = tmp_path / "artifacts"
    pipeline_root = tmp_path / "pipeline_root"
    project_name = os.path.basename(os.path.realpath(str(artifacts)))
    history_path = pipeline_root / "metrics_history" / f"{project_name}.jsonl"
    assert history_path.exists(), (
        f"Orchestrator-private history file must be written at {history_path}"
    )
    hist_lines = [l for l in history_path.read_text().splitlines() if l.strip()]
    hist_row = json.loads(hist_lines[-1])
    assert hist_row.get("escalation_resets") == 1
    assert hist_row.get("nuclear_resets") == 2
    assert hist_row.get("reviewer_unverified_retries") == 1
    assert hist_row.get("reset_log") == reset_log
    assert hist_row.get("reachability_summary") == stash
