"""GET /api/state exposes sentinel_wait_started_at from pipeline_state.json."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ui.server import app

client = TestClient(app)


@pytest.fixture
def _cfg_and_files(tmp_path):
    project_root = tmp_path / "pipeline_project"
    project_root.mkdir()
    state_path = tmp_path / "pipeline_state.json"
    phase_path = tmp_path / "phase_state.json"
    events_path = tmp_path / "pipeline_events.jsonl"
    events_path.write_text("{}\n", encoding="utf-8")
    lock_path = tmp_path / "pipeline.lock"
    cfg = {
        "pipeline_state_path": str(state_path),
        "phase_state_path": str(phase_path),
        "lock_path": str(lock_path),
        "events_path": str(events_path),
        "project_dir_path": str(project_root),
    }
    return cfg, state_path, phase_path


def test_api_state_returns_sentinel_wait_started_at(_cfg_and_files):
    cfg, state_path, phase_path = _cfg_and_files
    ts = "2026-04-23T15:30:00+00:00"
    state_path.write_text(
        json.dumps(
            {
                "pipeline_status": "WAITING_FOR_SENTINEL",
                "current_phase": 2,
                "current_phase_raw_id": "INT-E1",
                "current_agent": "planner",
                "project_path": "/tmp/proj",
                "sentinel_wait_started_at": ts,
                "planner_retries": 0,
                "executor_retries": 0,
                "reviewer_retries": 0,
            }
        ),
        encoding="utf-8",
    )
    phase_path.write_text("{}", encoding="utf-8")

    with patch("ui.server.load_config", return_value=cfg):
        r = client.get("/api/state")
    assert r.status_code == 200
    assert r.json().get("sentinel_wait_started_at") == ts


def test_api_state_sentinel_field_null_when_absent(_cfg_and_files):
    cfg, state_path, phase_path = _cfg_and_files
    state_path.write_text(
        json.dumps(
            {
                "pipeline_status": "RUNNING",
                "current_phase": 1,
                "current_phase_raw_id": "CORE-1",
                "current_agent": "executor",
                "project_path": "/tmp/proj",
                "planner_retries": 0,
                "executor_retries": 0,
                "reviewer_retries": 0,
            }
        ),
        encoding="utf-8",
    )
    phase_path.write_text("{}", encoding="utf-8")

    with patch("ui.server.load_config", return_value=cfg):
        r = client.get("/api/state")
    assert r.status_code == 200
    assert r.json().get("sentinel_wait_started_at") is None
