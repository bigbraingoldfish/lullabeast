"""Tests for /api/metrics-summary cost + hold-time fields (Plan Phase 2).

Verifies:
- Per-phase cost/hold fields present in the response.
- Top-level totals (cost, hold, active) computed correctly.
- Hold derivation from pipeline_events.jsonl pairs trigger→resolve by phase.
- Unrelated escalation events for other projects are excluded.
- Falls back to summed phase durations when run_summary.json is absent.
- Prefers run_summary.json total_duration_seconds when present.
"""
import json
import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ui.server import app, _derive_hold_seconds_per_phase

client = TestClient(app)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def _write_metrics_jsonl(project_dir: str, rows: list[dict]) -> str:
    art = os.path.join(project_dir, ".autodev", "pipeline")
    os.makedirs(art, exist_ok=True)
    path = os.path.join(art, "metrics.jsonl")
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return path


def _write_events_jsonl(pipeline_root: str, events: list[dict]) -> str:
    os.makedirs(pipeline_root, exist_ok=True)
    path = os.path.join(pipeline_root, "pipeline_events.jsonl")
    with open(path, "w") as f:
        for evt in events:
            f.write(json.dumps(evt) + "\n")
    return path


def _make_phase_row(phase: str, *, duration: int, executor: int, escalations: int = 0,
                    planner_cost: float = 0.0, executor_cost: float = 0.0,
                    reviewer_cost: float = 0.0) -> dict:
    return {
        "ts": "2026-05-19T00:00:00Z",
        "phase": phase,
        "goal": f"phase {phase} goal",
        "executor_attempts": executor,
        "reviewer_passes": 1,
        "blame_fires": 0,
        "escalations": escalations,
        "skill_used": "core-logic",
        "planner_tokens": {"cost_total": planner_cost},
        "executor_tokens": {"cost_total": executor_cost},
        "reviewer_tokens": {"cost_total": reviewer_cost},
        "cost_total": round(planner_cost + executor_cost + reviewer_cost, 6),
        "duration_seconds": duration,
    }


def test_derive_hold_seconds_pairs_trigger_resolve(temp_dir):
    """Pairs trigger→resolve for the named project, ignores other projects."""
    events = [
        {"ts": "2026-05-19T00:00:00Z", "event": "escalation_trigger",
         "project": "proj-a", "phase": "CORE-E1"},
        {"ts": "2026-05-19T00:05:00Z", "event": "escalation_resolve",
         "project": "proj-a", "phase": "CORE-E1"},
        {"ts": "2026-05-19T00:10:00Z", "event": "escalation_trigger",
         "project": "proj-b", "phase": "CORE-E1"},  # different project, ignore
        {"ts": "2026-05-19T00:11:00Z", "event": "escalation_resolve",
         "project": "proj-b", "phase": "CORE-E1"},
    ]
    path = _write_events_jsonl(temp_dir, events)
    holds = _derive_hold_seconds_per_phase(path, "proj-a")
    assert holds == {"CORE-E1": 300}


def test_derive_hold_seconds_empty_when_no_events(temp_dir):
    """Returns {} when the events file is missing."""
    holds = _derive_hold_seconds_per_phase(
        os.path.join(temp_dir, "missing.jsonl"), "proj-a"
    )
    assert holds == {}


def test_derive_hold_seconds_unpaired_trigger_skipped(temp_dir):
    """Trigger without a resolve is skipped (warned)."""
    events = [
        {"ts": "2026-05-19T00:00:00Z", "event": "escalation_trigger",
         "project": "proj-a", "phase": "CORE-E1"},
        # no resolve
    ]
    path = _write_events_jsonl(temp_dir, events)
    holds = _derive_hold_seconds_per_phase(path, "proj-a")
    assert holds == {}


def test_derive_hold_seconds_multiple_phases(temp_dir):
    """Holds aggregate per phase across distinct pairs."""
    events = [
        {"ts": "2026-05-19T00:00:00Z", "event": "escalation_trigger",
         "project": "p", "phase": "CORE-E1"},
        {"ts": "2026-05-19T00:01:00Z", "event": "escalation_resolve",
         "project": "p", "phase": "CORE-E1"},
        {"ts": "2026-05-19T01:00:00Z", "event": "escalation_trigger",
         "project": "p", "phase": "REND-E1"},
        {"ts": "2026-05-19T08:00:00Z", "event": "escalation_resolve",
         "project": "p", "phase": "REND-E1"},
    ]
    path = _write_events_jsonl(temp_dir, events)
    holds = _derive_hold_seconds_per_phase(path, "p")
    assert holds == {"CORE-E1": 60, "REND-E1": 25200}


def test_metrics_summary_includes_cost_and_hold_fields(temp_dir):
    """Response includes per-phase cost/hold and top-level totals."""
    project_dir = os.path.join(temp_dir, "proj-a")
    os.makedirs(project_dir)
    _write_metrics_jsonl(project_dir, [
        _make_phase_row("CORE-E1", duration=60, executor=1,
                        planner_cost=0.01, executor_cost=0.02, reviewer_cost=0.01),
        _make_phase_row("REND-E1", duration=300, executor=2, escalations=1,
                        planner_cost=0.05, executor_cost=0.30, reviewer_cost=0.10),
    ])
    pipeline_root = os.path.join(temp_dir, "pipeline_root")
    _write_events_jsonl(pipeline_root, [
        {"ts": "2026-05-19T00:00:00Z", "event": "escalation_trigger",
         "project": "proj-a", "phase": "REND-E1"},
        {"ts": "2026-05-19T02:00:00Z", "event": "escalation_resolve",
         "project": "proj-a", "phase": "REND-E1"},
    ])

    fake_config = {
        "project_dir_path": project_dir,
        "events_path": os.path.join(pipeline_root, "pipeline_events.jsonl"),
    }
    with patch("ui.server.load_config", return_value=fake_config):
        resp = client.get("/api/metrics-summary")
    assert resp.status_code == 200
    body = resp.json()

    assert body["total_cost"] == pytest.approx(0.49, abs=1e-6)
    assert body["planner_cost_total"] == pytest.approx(0.06, abs=1e-6)
    assert body["executor_cost_total"] == pytest.approx(0.32, abs=1e-6)
    assert body["reviewer_cost_total"] == pytest.approx(0.11, abs=1e-6)
    assert body["total_hold_seconds"] == 7200
    # Without run_summary.json, total duration falls back to phase sum (60+300=360).
    assert body["total_duration_seconds"] == 360
    assert body["total_active_seconds"] == max(0, 360 - 7200)

    phase_map = {p["phase"]: p for p in body["phases"]}
    assert phase_map["CORE-E1"]["hold_seconds"] == 0
    assert phase_map["REND-E1"]["hold_seconds"] == 7200
    assert phase_map["CORE-E1"]["cost_total"] == pytest.approx(0.04, abs=1e-6)
    assert phase_map["REND-E1"]["executor_cost"] == pytest.approx(0.30, abs=1e-6)


def test_metrics_summary_zero_when_no_cost_or_escalation(temp_dir):
    """Clean run with no costs and no escalations: totals are zero, hold is zero."""
    project_dir = os.path.join(temp_dir, "clean")
    os.makedirs(project_dir)
    _write_metrics_jsonl(project_dir, [
        _make_phase_row("CORE-E1", duration=100, executor=1),
    ])
    pipeline_root = os.path.join(temp_dir, "pipeline_root")
    os.makedirs(pipeline_root)

    fake_config = {
        "project_dir_path": project_dir,
        "events_path": os.path.join(pipeline_root, "pipeline_events.jsonl"),
    }
    with patch("ui.server.load_config", return_value=fake_config):
        resp = client.get("/api/metrics-summary")
    assert resp.status_code == 200
    body = resp.json()

    assert body["total_cost"] == 0.0
    assert body["total_hold_seconds"] == 0
    assert body["total_active_seconds"] == 100
    for p in body["phases"]:
        assert p["hold_seconds"] == 0
        assert p["cost_total"] == 0.0


def test_metrics_summary_uses_max_of_run_summary_and_phase_sum(temp_dir):
    """total_duration_seconds = max(phase sum, run_summary wall-clock).

    run_summary.json is rewritten on every orchestrator boot — a "complete on
    startup" no-op shrinks total_duration_seconds to the boot window. Taking
    max() with the phase-row sum keeps the honest cumulative number visible.
    """
    project_dir = os.path.join(temp_dir, "proj-a")
    art = os.path.join(project_dir, ".autodev", "pipeline")
    os.makedirs(art)
    _write_metrics_jsonl(project_dir, [
        _make_phase_row("CORE-E1", duration=60, executor=1),
        _make_phase_row("REND-E1", duration=300, executor=2),
    ])
    pipeline_root = os.path.join(temp_dir, "pipeline_root")
    os.makedirs(pipeline_root)
    fake_config = {
        "project_dir_path": project_dir,
        "events_path": os.path.join(pipeline_root, "pipeline_events.jsonl"),
    }

    # Case A: run_summary.json larger → it wins.
    with open(os.path.join(art, "run_summary.json"), "w") as f:
        json.dump({"total_duration_seconds": 9999}, f)
    with patch("ui.server.load_config", return_value=fake_config):
        body = client.get("/api/metrics-summary").json()
    assert body["total_duration_seconds"] == 9999

    # Case B: run_summary.json shrunk by a noop startup → phase sum wins.
    with open(os.path.join(art, "run_summary.json"), "w") as f:
        json.dump({"total_duration_seconds": 106}, f)
    with patch("ui.server.load_config", return_value=fake_config):
        body = client.get("/api/metrics-summary").json()
    assert body["total_duration_seconds"] == 360  # phase sum 60+300
