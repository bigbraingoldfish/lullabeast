"""MON-1 — per-phase ``models_used`` passthrough in /api/metrics-summary.

The orchestrator stamps {role: model} into each canonical metrics row
(``models_used``, null for pre-deploy rows); the endpoint passes it through on
the per-phase rows so the Run Metrics header can render a model badge next to
the skill badge. Malformed values (non-dict) read as null — same defensive
contract as the other row fields.

Fixture pattern mirrors ``test_api_metrics_summary_role_tokens.py``.
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


def _row(phase, models_used="__absent__"):
    row = {
        "ts": "2026-06-12T00:00:00Z",
        "phase": phase,
        "goal": f"phase {phase}",
        "executor_attempts": 1,
        "reviewer_passes": 1,
        "escalations": 0,
        "skill_used": "core-logic",
        "duration_seconds": 60,
        "cost_total": 0.0,
    }
    if models_used != "__absent__":
        row["models_used"] = models_used
    return row


def _summary(temp_dir, rows):
    project_dir = os.path.join(temp_dir, "proj")
    art = os.path.join(project_dir, ".autodev", "pipeline")
    os.makedirs(art, exist_ok=True)
    with open(os.path.join(art, "metrics.jsonl"), "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    fake_config = {
        "project_dir_path": project_dir,
        "events_path": os.path.join(temp_dir, "no-events.jsonl"),
    }
    with patch("ui.server.load_config", return_value=fake_config):
        resp = client.get("/api/metrics-summary")
    assert resp.status_code == 200
    return resp.json()


def test_models_used_passed_through(temp_dir):
    models = {"planner": "m-a", "executor": "m-b", "reviewer": "m-b"}
    data = _summary(temp_dir, [_row("CORE-E1", models_used=models)])
    (phase,) = data["phases"]
    assert phase["models_used"] == models


def test_pre_deploy_rows_read_null(temp_dir):
    data = _summary(temp_dir, [_row("OLD-1")])
    (phase,) = data["phases"]
    assert phase["models_used"] is None


def test_malformed_models_used_reads_null(temp_dir):
    data = _summary(temp_dir, [_row("BAD-1", models_used="not-a-dict")])
    (phase,) = data["phases"]
    assert phase["models_used"] is None
