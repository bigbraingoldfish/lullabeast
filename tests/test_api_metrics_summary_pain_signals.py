"""P2-A — ``/api/metrics-summary`` per-phase rows pass through the pain signals.

The orchestrator's canonical metrics row persists per-phase "pain signals"
expressly for analysis — ``escalation_resets``, ``nuclear_resets``,
``reviewer_unverified_retries`` (counters), ``gate_warnings`` and
``reachability_summary`` (compact dicts) — but ``_build_project_metrics_summary``
dropped them from its per-phase rows (integrity finding 3: "metrics-summary drops
persisted pain-signals the writer added for analysis"). These pin the additive
passthrough so the Pipeline screen can render per-phase ⚠/↻/⛔ chips (P2-B).

Additive only — existing keys unchanged. Fixture mirrors
``test_api_metrics_tokens_breakdown.py``.
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


def _config(temp_dir):
    project_root = os.path.join(temp_dir, "pipeline_project")
    os.makedirs(project_root, exist_ok=True)
    return {
        "project_dir_path": project_root,
        "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
    }


def _write_metrics_jsonl(project_dir, rows):
    art = os.path.join(project_dir, ".autodev", "pipeline")
    os.makedirs(art, exist_ok=True)
    with open(os.path.join(art, "metrics.jsonl"), "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _base_row(phase):
    """A minimal canonical row (the token/cost keys the aggregator needs)."""
    empty = {"total_tokens": 0, "cost_total": 0.0}
    return {
        "ts": "2026-06-13T00:00:00Z", "phase": phase, "goal": f"phase {phase}",
        "executor_attempts": 1, "reviewer_passes": 1, "escalations": 0,
        "planner_tokens": dict(empty), "executor_tokens": dict(empty), "reviewer_tokens": dict(empty),
    }


def test_per_phase_rows_pass_through_pain_signals(temp_dir):
    """A row carrying the five pain signals surfaces all five on its per-phase row."""
    cfg = _config(temp_dir)
    row = _base_row("CORE-1")
    row.update({
        "escalation_resets": 1,
        "nuclear_resets": 0,
        "reviewer_unverified_retries": 2,
        "gate_warnings": {"count": 3, "codes": ["ERR_MANIFEST_FILE_MISSING"]},
        "reachability_summary": {"kind": "unreachable_summary", "count": 2},
    })
    _write_metrics_jsonl(cfg["project_dir_path"], [row])
    with patch("ui.server.load_config", return_value=cfg):
        body = client.get("/api/metrics-summary").json()
    ph = body["phases"][0]
    assert ph["escalation_resets"] == 1
    assert ph["nuclear_resets"] == 0
    assert ph["reviewer_unverified_retries"] == 2
    assert ph["gate_warnings"]["count"] == 3
    assert ph["reachability_summary"]["kind"] == "unreachable_summary"


def test_per_phase_pain_signals_default_when_absent(temp_dir):
    """A pre-deploy row without the fields: counters default to 0, compact dicts to
    null — so the frontend chip logic always sees stable types."""
    cfg = _config(temp_dir)
    _write_metrics_jsonl(cfg["project_dir_path"], [_base_row("CORE-1")])
    with patch("ui.server.load_config", return_value=cfg):
        body = client.get("/api/metrics-summary").json()
    ph = body["phases"][0]
    assert ph["escalation_resets"] == 0
    assert ph["nuclear_resets"] == 0
    assert ph["reviewer_unverified_retries"] == 0
    assert ph["gate_warnings"] is None
    assert ph["reachability_summary"] is None
