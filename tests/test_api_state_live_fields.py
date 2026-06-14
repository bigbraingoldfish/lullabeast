"""P2-A — live phase-outcome + token fields on ``GET /api/state``.

The orchestrator already writes these to ``phase_state.json`` (Section 6.4 outcome
fields + the per-role live token accumulators) but they were read by nothing in
the server — integrity finding 6 ("phase_state outcome fields unexposed"). P2-A
surfaces them so the Pipeline screen can show, live: the last poll verdict
(``last_poll_reason``), the dense last-attempt summary (``last_attempt_summary``),
the terminal outcome (``last_phase_outcome``), demoted gate warnings
(``last_gate_warnings``), and the in-progress phase's spend (``current_phase_tokens``
= the three ``{role}_tokens_acc`` dicts + a summed ``{total_tokens, cost_total}``).

Contract (mirrors ``_compute_escalation_view``): **present-only** — a field is
included only when present in phase_state, so the endpoint keeps its "absent when
unset" shape and a minimal state never 500s. ``agent_activity_age_seconds`` is the
exception (always present, null when unset) and is pinned in
``test_api_state_activity_stamp_age.py``.

Fixture pattern mirrors ``test_api_phase4_run_started_at.py``.
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
        "pipeline_state_path": os.path.join(temp_dir, "pipeline_state.json"),
        "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
        "lock_path": os.path.join(temp_dir, "pipeline.lock"),
        "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
        "project_dir_path": project_root,
    }


def _write(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


def test_state_exposes_outcome_fields(temp_dir):
    """The Section-6.4 phase_state outcome fields surface verbatim so the Pipeline
    screen can render the last poll verdict / attempt summary / terminal outcome /
    demoted warnings without scraping logs (integrity finding 6)."""
    cfg = _config(temp_dir)
    _write(cfg["pipeline_state_path"], {
        "pipeline_status": "WAITING_FOR_SENTINEL", "current_phase": 2, "current_agent": "executor",
    })
    _write(cfg["phase_state_path"], {
        "last_poll_reason": "succeeded",
        "last_attempt_summary": "executor attempt 1 succeeded in 312s",
        "last_phase_outcome": "completed",
        "last_gate_warnings": {"count": 2, "codes": ["ERR_MANIFEST_FILE_MISSING", "ERR_TDD_COVERAGE_MISMATCH"]},
    })
    with patch("ui.server.load_config", return_value=cfg):
        body = client.get("/api/state").json()
    assert body["last_poll_reason"] == "succeeded"
    assert body["last_attempt_summary"].startswith("executor attempt 1")
    assert body["last_phase_outcome"] == "completed"
    assert body["last_gate_warnings"]["count"] == 2
    assert "ERR_MANIFEST_FILE_MISSING" in body["last_gate_warnings"]["codes"]


def test_state_exposes_current_phase_tokens_summed(temp_dir):
    """``current_phase_tokens`` carries the three live per-role accumulators plus a
    summed ``{total_tokens, cost_total}`` so the Monitor strip can add the in-progress
    phase to the completed-phase totals."""
    cfg = _config(temp_dir)
    _write(cfg["pipeline_state_path"], {"pipeline_status": "RUNNING", "current_agent": "executor"})
    _write(cfg["phase_state_path"], {
        "planner_tokens_acc": {"total_tokens": 1000, "cost_total": 0.01, "input": 100, "output": 50},
        "executor_tokens_acc": {"total_tokens": 2000, "cost_total": 0.02},
        "reviewer_tokens_acc": {"total_tokens": 500, "cost_total": 0.005},
    })
    with patch("ui.server.load_config", return_value=cfg):
        body = client.get("/api/state").json()
    cpt = body["current_phase_tokens"]
    assert cpt["total_tokens"] == 3500
    assert round(cpt["cost_total"], 6) == 0.035
    assert cpt["executor"]["total_tokens"] == 2000
    assert cpt["planner"]["input"] == 100  # per-role dict passed through verbatim


def test_state_live_fields_absent_when_unset(temp_dir):
    """Present-only contract: a minimal phase_state omits the live keys entirely
    (no KeyError, no 500) — the endpoint keeps its "absent when unset" shape."""
    cfg = _config(temp_dir)
    _write(cfg["pipeline_state_path"], {"pipeline_status": "RUNNING"})
    _write(cfg["phase_state_path"], {})
    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/state")
    assert resp.status_code == 200
    body = resp.json()
    for k in ("last_poll_reason", "last_attempt_summary", "last_phase_outcome",
              "last_gate_warnings", "current_phase_tokens"):
        assert k not in body, f"{k} should be absent when unset (present-only contract)"
