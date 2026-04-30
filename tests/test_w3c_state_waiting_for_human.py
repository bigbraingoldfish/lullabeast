"""W3-C: waiting_for_human_at and waiting_for_human_resolved_at exposed via GET /api/state.

Tests verify:
- Key present in response when phase_state.json has it
- Key absent (not null) when phase_state.json does not have it
- waiting_for_human_resolved_at follows same rule
- Both absent when no phase_state.json exists
"""
import json
import os

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from ui.server import app

client = TestClient(app)


@pytest.fixture
def tmp_env(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    return {
        "pipeline_state_path": str(tmp_path / "pipeline_state.json"),
        "phase_state_path": str(tmp_path / "phase_state.json"),
        "lock_path": str(tmp_path / "pipeline.lock"),
        "events_path": str(tmp_path / "pipeline_events.jsonl"),
        "project_dir_path": str(proj),
    }


def _write_pipeline_state(cfg):
    path = cfg["pipeline_state_path"]
    with open(path, "w") as f:
        json.dump({"pipeline_status": "WAITING_FOR_HUMAN", "current_phase": 1}, f)


def test_waiting_for_human_at_returned_when_present(tmp_env):
    _write_pipeline_state(tmp_env)
    with open(tmp_env["phase_state_path"], "w") as f:
        json.dump({"waiting_for_human_at": "2026-04-30T10:00:00Z"}, f)

    with patch("ui.server.load_config", return_value=tmp_env):
        resp = client.get("/api/state")

    assert resp.status_code == 200
    assert resp.json()["waiting_for_human_at"] == "2026-04-30T10:00:00Z"


def test_waiting_for_human_at_absent_when_not_in_phase_state(tmp_env):
    _write_pipeline_state(tmp_env)
    with open(tmp_env["phase_state_path"], "w") as f:
        json.dump({"escalation_resets": 0}, f)  # no waiting_for_human_at key

    with patch("ui.server.load_config", return_value=tmp_env):
        resp = client.get("/api/state")

    assert resp.status_code == 200
    assert "waiting_for_human_at" not in resp.json()


def test_waiting_for_human_resolved_at_returned_when_present(tmp_env):
    _write_pipeline_state(tmp_env)
    with open(tmp_env["phase_state_path"], "w") as f:
        json.dump({"waiting_for_human_resolved_at": "2026-04-30T10:05:00Z"}, f)

    with patch("ui.server.load_config", return_value=tmp_env):
        resp = client.get("/api/state")

    assert resp.status_code == 200
    assert resp.json()["waiting_for_human_resolved_at"] == "2026-04-30T10:05:00Z"


def test_waiting_for_human_resolved_at_absent_when_not_in_phase_state(tmp_env):
    _write_pipeline_state(tmp_env)
    with open(tmp_env["phase_state_path"], "w") as f:
        json.dump({"escalation_resets": 1}, f)

    with patch("ui.server.load_config", return_value=tmp_env):
        resp = client.get("/api/state")

    assert resp.status_code == 200
    assert "waiting_for_human_resolved_at" not in resp.json()


def test_both_fields_absent_when_no_phase_state_file(tmp_env):
    _write_pipeline_state(tmp_env)
    # phase_state.json intentionally not created

    with patch("ui.server.load_config", return_value=tmp_env):
        resp = client.get("/api/state")

    assert resp.status_code == 200
    data = resp.json()
    assert "waiting_for_human_at" not in data
    assert "waiting_for_human_resolved_at" not in data


def test_both_fields_present_together(tmp_env):
    _write_pipeline_state(tmp_env)
    with open(tmp_env["phase_state_path"], "w") as f:
        json.dump({
            "waiting_for_human_at": "2026-04-30T10:00:00Z",
            "waiting_for_human_resolved_at": "2026-04-30T10:08:00Z",
        }, f)

    with patch("ui.server.load_config", return_value=tmp_env):
        resp = client.get("/api/state")

    assert resp.status_code == 200
    data = resp.json()
    assert data["waiting_for_human_at"] == "2026-04-30T10:00:00Z"
    assert data["waiting_for_human_resolved_at"] == "2026-04-30T10:08:00Z"


def test_waiting_for_human_via_symlinked_project_dir_matches_real_file(tmp_path):
    """W3-C: phase_state_path through symlink resolves to same file as under real project."""
    real = tmp_path / "real_workspace"
    real.mkdir(parents=True)
    (real / ".autodev" / "pipeline").mkdir(parents=True)
    real_phase = real / ".autodev" / "pipeline" / "phase_state.json"
    with open(real_phase, "w") as f:
        json.dump({"waiting_for_human_at": "2026-04-30T14:00:00Z"}, f)

    link = tmp_path / "pipeline-project"
    os.symlink(str(real), str(link))

    phase_via_link = str(link / ".autodev" / "pipeline" / "phase_state.json")
    assert os.path.samefile(phase_via_link, str(real_phase))

    cfg = {
        "pipeline_state_path": str(tmp_path / "pipeline_state.json"),
        "phase_state_path": phase_via_link,
        "lock_path": str(tmp_path / "pipeline.lock"),
        "events_path": str(tmp_path / "pipeline_events.jsonl"),
        "project_dir_path": str(link),
    }
    with open(cfg["pipeline_state_path"], "w") as f:
        json.dump({"pipeline_status": "WAITING_FOR_HUMAN", "current_phase": 1}, f)

    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/state")

    assert resp.status_code == 200
    assert resp.json()["waiting_for_human_at"] == "2026-04-30T14:00:00Z"
