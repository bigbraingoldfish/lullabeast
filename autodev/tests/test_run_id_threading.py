"""P1-A — ``run_id`` minting and threading into the durable telemetry surfaces.

A run_id is a uuid minted when a NEW run begins (it travels with ``run_started_at``)
and preserved across phase advance / queue revival. It is threaded into every
``pipeline_events.jsonl`` line, ``run_summary.json`` (+ ``runs_index.jsonl``), and
the canonical metrics row, so failures/escalations/cost can be grouped per run
(``jq 'group_by(.run_id)'``) without fragile ts-joins.

``run_id``'s durable home is ``pipeline_state.json``; the two module-level writers
(``_write_pipeline_event``, ``_write_run_summary``) read it from there via
``_current_run_id()``; the metrics-row writer (a method) reads ``self.state``.

These pin the helper + the three threading points. The mint-vs-preserve placement
at the call sites is guaranteed by co-location with ``run_started_at`` (run_id is
added on the same lines), exercised end-to-end by the run_started_at lifecycle
tests.
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


def test_new_run_id_is_unique_nonempty():
    """``_new_run_id`` mints a fresh, non-empty id each call — two runs never
    collide, which is what makes per-run grouping meaningful."""
    a = orch_mod._new_run_id()
    b = orch_mod._new_run_id()
    assert a and b
    assert a != b


def test_write_pipeline_event_includes_run_id_from_state(tmp_path, monkeypatch):
    """An emitted event line carries the current run_id read from pipeline_state.json."""
    pr = tmp_path / "pipeline_root"
    pr.mkdir()
    art = tmp_path / "art"
    art.mkdir()
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(pr))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(pr / "pipeline_state.json"))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(art))
    (pr / "pipeline_state.json").write_text(json.dumps({"run_id": "RID-abc"}))

    orch_mod._write_pipeline_event("gate_pass", "CORE-1", "executor", {"k": "v"})

    lines = [l for l in (pr / "pipeline_events.jsonl").read_text().splitlines() if l.strip()]
    row = json.loads(lines[-1])
    assert row["run_id"] == "RID-abc"
    assert row["event"] == "gate_pass"  # schema unchanged otherwise


def test_write_pipeline_event_run_id_null_when_state_absent(tmp_path, monkeypatch):
    """No state file → run_id is None (key always present, never a crash)."""
    pr = tmp_path / "pipeline_root"
    pr.mkdir()
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(pr))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(pr / "does_not_exist.json"))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path / "nolink"))

    orch_mod._write_pipeline_event("status_changed", "", "system", {})

    lines = [l for l in (pr / "pipeline_events.jsonl").read_text().splitlines() if l.strip()]
    row = json.loads(lines[-1])
    assert "run_id" in row
    assert row["run_id"] is None


def _drive_metrics_row(tmp_path, monkeypatch, state_extra):
    """Minimal driver for ``_write_canonical_metrics_row`` (mirrors the
    ``test_phase3_metrics_row_pain_signals`` idiom, kept self-contained)."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pipeline_root = tmp_path / "pipeline_root"
    pipeline_root.mkdir()
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(artifacts))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(pipeline_root))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(artifacts / "phase_state.json"))
    (artifacts / "phase_state.json").write_text(json.dumps({
        "executor_self_failure_retries": 0,
        "executor_reviewer_rejection_retries": 0,
        "planner_tokens_acc": {}, "executor_tokens_acc": {}, "reviewer_tokens_acc": {},
    }))
    (artifacts / "current_phase.json").write_text(json.dumps({"raw_id": "CORE-E1", "detail": "g"}))

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {"current_phase": 1, "current_phase_raw_id": "CORE-E1", "reviewer_retries": 0}
    orch.state.update(state_extra)
    orch.openclaw_config = {}
    orch.lock_fd = None
    orch._write_canonical_metrics_row()

    lines = [l for l in (artifacts / "metrics.jsonl").read_text().splitlines() if l.strip()]
    return json.loads(lines[-1])


def test_metrics_row_includes_run_id(tmp_path, monkeypatch):
    """The canonical metrics row carries run_id from ``self.state`` so completed-phase
    history is groupable per run."""
    row = _drive_metrics_row(tmp_path, monkeypatch, {"run_id": "RID-metrics"})
    assert row.get("run_id") == "RID-metrics"


def test_run_summary_includes_run_id(tmp_path, monkeypatch):
    """``run_summary.json`` and the ``runs_index.jsonl`` line both carry run_id
    (read from pipeline_state.json at terminal-exit time)."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pipeline_root = tmp_path / "pipeline_root"
    pipeline_root.mkdir()
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(pipeline_root))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(pipeline_root / "pipeline_state.json"))
    (pipeline_root / "pipeline_state.json").write_text(
        json.dumps({"project_path": "/p/proj", "run_id": "RID-run"})
    )

    orch_mod._write_run_summary("STOPPED", "operator stop")

    summary = json.loads((artifacts / "run_summary.json").read_text())
    assert summary.get("run_id") == "RID-run"
    idx_lines = [l for l in (pipeline_root / "runs_index.jsonl").read_text().splitlines() if l.strip()]
    assert json.loads(idx_lines[-1]).get("run_id") == "RID-run"
