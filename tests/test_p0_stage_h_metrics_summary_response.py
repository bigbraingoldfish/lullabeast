"""P0 Stage H — /api/metrics-summary propagates the new retry counters.

The orchestrator's canonical metrics row now carries two new top-level
fields (``executor_self_failures`` and ``executor_reviewer_rejections``).
The UI server's ``/api/metrics-summary`` endpoint must surface them in
the per-phase response so the dashboard can render the breakdown.

Backward compatibility: rows written before Stage H rolled out won't
have the new fields. The endpoint must default missing values to ``0``
via ``.get(..., 0)`` rather than omitting the key — the frontend's
``formatExecAttemptsBreakdown`` helper expects the keys to be present.

Pattern: drive the endpoint with a TestClient against a seeded
metrics.jsonl on a temporary project dir. Mirrors the existing
``test_w3d_metrics_global.py`` pattern.
"""

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import ui.server as server_mod  # noqa: E402


@pytest.fixture
def seeded_project(tmp_path, monkeypatch):
    """Set up a temp project directory with a .autodev/pipeline/metrics.jsonl
    that the endpoint reads. Returns (project_dir, write_rows fn)."""
    project_dir = tmp_path / "stage-h-project"
    pipeline_dir = project_dir / ".autodev" / "pipeline"
    pipeline_dir.mkdir(parents=True, exist_ok=True)

    def write_rows(rows):
        with open(pipeline_dir / "metrics.jsonl", "w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")

    # Stub load_config so the endpoint sees our temp project as the active one.
    original = server_mod.load_config

    def fake_load_config():
        cfg = original()
        cfg["project_dir_path"] = str(project_dir)
        return cfg

    monkeypatch.setattr(server_mod, "load_config", fake_load_config)
    return project_dir, write_rows


def _get_metrics(client):
    resp = client.get("/api/metrics-summary")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_phases_include_self_failure_counter(seeded_project):
    """The phase entry must surface ``executor_self_failures`` from the
    underlying metrics row."""
    project_dir, write_rows = seeded_project
    write_rows([{
        "ts": "2026-05-22T00:00:00Z",
        "phase": "CORE-E1",
        "goal": "x",
        "executor_attempts": 4,
        "reviewer_passes": 1,
        "blame_fires": 0,
        "escalations": 0,
        "skill_used": "core-logic",
        "blame_verdict": None,
        "executor_self_failures": 2,
        "executor_reviewer_rejections": 1,
        "duration_seconds": 60,
    }])

    client = TestClient(server_mod.app)
    data = _get_metrics(client)
    phase = next(p for p in data["phases"] if p["phase"] == "CORE-E1")
    assert phase.get("executor_self_failures") == 2, (
        "/api/metrics-summary per-phase entry must surface "
        "executor_self_failures so the dashboard's phase dropdown can "
        "render the retry breakdown without re-reading metrics.jsonl."
    )


def test_phases_include_rejection_counter(seeded_project):
    """Symmetric: the phase entry must surface
    ``executor_reviewer_rejections``."""
    project_dir, write_rows = seeded_project
    write_rows([{
        "ts": "2026-05-22T00:00:00Z",
        "phase": "CORE-E1",
        "executor_attempts": 3,
        "reviewer_passes": 2,
        "executor_self_failures": 0,
        "executor_reviewer_rejections": 2,
    }])

    client = TestClient(server_mod.app)
    data = _get_metrics(client)
    phase = next(p for p in data["phases"] if p["phase"] == "CORE-E1")
    assert phase.get("executor_reviewer_rejections") == 2


def test_phases_default_new_counters_to_zero_when_missing(seeded_project):
    """Pre-Stage-H history rows lack the new fields. The endpoint must
    default to 0 rather than omit the key so the frontend's
    ``formatExecAttemptsBreakdown`` always sees numeric values."""
    project_dir, write_rows = seeded_project
    # A row written before Stage H — no new counter fields at all.
    write_rows([{
        "ts": "2025-12-01T00:00:00Z",
        "phase": "LEGACY-E1",
        "executor_attempts": 2,
        "reviewer_passes": 1,
        # NO executor_self_failures, NO executor_reviewer_rejections
    }])

    client = TestClient(server_mod.app)
    data = _get_metrics(client)
    phase = next(p for p in data["phases"] if p["phase"] == "LEGACY-E1")
    assert "executor_self_failures" in phase, (
        "/api/metrics-summary must include the field even when the "
        "underlying row lacks it — default to 0. Without this guard, "
        "the frontend's helper sees `undefined` and shows 'NaN' in the "
        "phase dropdown."
    )
    assert phase["executor_self_failures"] == 0
    assert "executor_reviewer_rejections" in phase
    assert phase["executor_reviewer_rejections"] == 0
