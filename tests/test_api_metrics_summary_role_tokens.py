"""METRICS-E2 — per-phase per-role token totals in /api/metrics-summary.

The Run Metrics phase expansion splits cost by role (``planner_cost`` /
``executor_cost`` / ``reviewer_cost``) but tokens only carried the phase total
and the class breakdown — the per-role token split existed on every metrics
row (``{role}_tokens.total_tokens``) and was read-and-dropped. These pin the
additive passthrough: per-phase ``planner_tokens`` / ``executor_tokens`` /
``reviewer_tokens`` integers via ``_role_token_total`` (missing/non-numeric →
0, same contract as the cost split and the run-level ``*_tokens_total``).

Fixture pattern mirrors ``test_api_metrics_tokens_breakdown.py``.
"""
import json
import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ui.server import app

client = TestClient(app)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def _write_metrics_jsonl(project_dir, rows):
    art = os.path.join(project_dir, ".autodev", "pipeline")
    os.makedirs(art, exist_ok=True)
    path = os.path.join(art, "metrics.jsonl")
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return path


def _role(total, cost=0.0):
    return {"total_tokens": total, "cost_total": cost}


def _row(phase, planner, executor, reviewer, duration=60):
    return {
        "ts": "2026-06-12T00:00:00Z",
        "phase": phase,
        "goal": f"phase {phase}",
        "executor_attempts": 1,
        "reviewer_passes": 1,
        "blame_fires": 0,
        "escalations": 0,
        "skill_used": "core-logic",
        "planner_tokens": planner,
        "executor_tokens": executor,
        "reviewer_tokens": reviewer,
        "cost_total": 0.0,
        "duration_seconds": duration,
    }


def _summary(temp_dir, rows):
    project_dir = os.path.join(temp_dir, "proj")
    os.makedirs(project_dir, exist_ok=True)
    _write_metrics_jsonl(project_dir, rows)
    fake_config = {
        "project_dir_path": project_dir,
        "events_path": os.path.join(temp_dir, "no-events.jsonl"),
    }
    with patch("ui.server.load_config", return_value=fake_config):
        resp = client.get("/api/metrics-summary")
    assert resp.status_code == 200
    return resp.json()


def test_per_phase_role_token_split_present_and_correct(temp_dir):
    rows = [
        _row("CORE-E1", _role(558075), _role(2326419), _role(794540)),
        _row("CORE-E2", _role(1000), _role(2000), _role(3000)),
    ]
    data = _summary(temp_dir, rows)
    p1, p2 = data["phases"]
    assert p1["planner_tokens"] == 558075
    assert p1["executor_tokens"] == 2326419
    assert p1["reviewer_tokens"] == 794540
    assert p2["planner_tokens"] == 1000
    assert p2["executor_tokens"] == 2000
    assert p2["reviewer_tokens"] == 3000
    # The split sums to the existing per-phase total (shared helper).
    assert p1["planner_tokens"] + p1["executor_tokens"] + p1["reviewer_tokens"] == p1["tokens_total"]


def test_pre_token_history_rows_default_to_zero(temp_dir):
    """Rows missing the role dicts (pre-W1-G history) read as 0, not None/NaN —
    the frontend's fmtTokenRoleSplit hides an all-zero split."""
    rows = [{
        "ts": "2026-06-12T00:00:00Z", "phase": "OLD-1", "goal": "old",
        "executor_attempts": 1, "reviewer_passes": 1, "escalations": 0,
        "duration_seconds": 10,
    }]
    data = _summary(temp_dir, rows)
    (phase,) = data["phases"]
    assert phase["planner_tokens"] == 0
    assert phase["executor_tokens"] == 0
    assert phase["reviewer_tokens"] == 0


def test_malformed_role_objects_default_to_zero(temp_dir):
    rows = [
        _row("BAD-1",
             None,
             "not-a-dict",
             {"total_tokens": "garbage", "cost_total": 0.0}),
    ]
    data = _summary(temp_dir, rows)
    (phase,) = data["phases"]
    assert phase["planner_tokens"] == 0
    assert phase["executor_tokens"] == 0
    assert phase["reviewer_tokens"] == 0


def test_existing_keys_unchanged(temp_dir):
    """Additive guard — tokens_total, tokens_breakdown, and the cost split keep
    their values alongside the new role-token keys."""
    rows = [_row("CORE-E1", _role(100, 0.01), _role(200, 0.02), _role(300, 0.03))]
    data = _summary(temp_dir, rows)
    (phase,) = data["phases"]
    assert phase["tokens_total"] == 600
    assert phase["planner_cost"] == 0.01
    assert phase["executor_cost"] == 0.02
    assert phase["reviewer_cost"] == 0.03
    assert data["total_tokens"] == 600
