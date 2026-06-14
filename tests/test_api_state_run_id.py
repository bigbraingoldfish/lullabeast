"""P1-A (server half) — ``run_id`` on ``/api/state`` and the launch reset template.

The orchestrator threads ``run_id`` through events / run_summary / the metrics row;
the server must (a) surface it on ``/api/state`` (an explicit whitelist — fields do
not pass through automatically) so the UI can label the live run, and (b) mint it in
the shared launch/switch reset template ``_clean_pipeline_state_for_project`` so a
Setup-launched run has an id from its first state write.

Fixture pattern mirrors ``test_api_phase4_run_started_at.py``.
"""
import json
import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ui.server import app, _clean_pipeline_state_for_project

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


def _write_state(cfg, state):
    with open(cfg["pipeline_state_path"], "w") as f:
        json.dump(state, f)


def test_state_exposes_run_id(temp_dir):
    """A ``run_id`` in pipeline_state.json is returned verbatim by ``/api/state``."""
    cfg = _config(temp_dir)
    _write_state(cfg, {
        "pipeline_status": "RUNNING",
        "current_phase": 1,
        "run_id": "11111111-2222-3333-4444-555555555555",
    })
    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/state")
    assert resp.status_code == 200
    assert resp.json()["run_id"] == "11111111-2222-3333-4444-555555555555"


def test_state_run_id_null_when_absent(temp_dir):
    """When the state file lacks run_id the response still carries the key as None
    (stable shape) rather than dropping it."""
    cfg = _config(temp_dir)
    _write_state(cfg, {"pipeline_status": "RUNNING", "current_phase": 1})
    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/state")
    assert resp.status_code == 200
    body = resp.json()
    assert "run_id" in body
    assert body["run_id"] is None


def test_clean_pipeline_state_stamps_run_id():
    """The shared launch/switch reset template mints a run_id so a Setup-launched
    run is groupable from its first state write. Distinct calls mint distinct ids."""
    a = _clean_pipeline_state_for_project("/some/project")
    b = _clean_pipeline_state_for_project("/some/project")
    assert a.get("run_id")
    assert b.get("run_id")
    assert a["run_id"] != b["run_id"]
