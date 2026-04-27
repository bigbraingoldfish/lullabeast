"""Tests for POST /api/stop (pipeline_stop_requested and WAITING_FOR_HUMAN escalation STOP)."""

import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_lifespan():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_context(app):
        yield

    return mock_context


@pytest.fixture
def test_client(mock_lifespan):
    from ui.server import app

    app.router.lifespan_context = mock_lifespan
    with TestClient(app) as client:
        yield client


class TestPostApiStop:
    def test_stop_running_creates_pipeline_stop_requested(self, test_client, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        ps = tmp_path / "pipeline_state.json"
        ps.write_text(
            json.dumps({"pipeline_status": "RUNNING"}),
            encoding="utf-8",
        )
        cfg = {
            "pipeline_state_path": str(ps),
            "project_dir_path": str(proj),
            "lock_path": str(tmp_path / "noop.lock"),
        }
        with patch("ui.server.load_config", return_value=cfg), patch(
            "ui.server._orchestrator_alive_from_config", return_value=True
        ):
            r = test_client.post("/api/stop")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "orchestrator_alive" in data
        assert (proj / ".autodev" / "pipeline" / "pipeline_stop_requested").exists()

    def test_stop_waiting_for_sentinel_creates_stop_file(self, test_client, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        ps = tmp_path / "pipeline_state.json"
        ps.write_text(
            json.dumps({"pipeline_status": "WAITING_FOR_SENTINEL"}),
            encoding="utf-8",
        )
        cfg = {
            "pipeline_state_path": str(ps),
            "project_dir_path": str(proj),
            "lock_path": str(tmp_path / "noop.lock"),
        }
        with patch("ui.server.load_config", return_value=cfg), patch(
            "ui.server._orchestrator_alive_from_config", return_value=False
        ):
            r = test_client.post("/api/stop")
        assert r.status_code == 200
        assert (proj / ".autodev" / "pipeline" / "pipeline_stop_requested").exists()

    def test_stop_waiting_for_human_writes_escalation_files(self, test_client, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        ps = tmp_path / "pipeline_state.json"
        ph = tmp_path / "phase_state.json"
        ps.write_text(
            json.dumps({"pipeline_status": "WAITING_FOR_HUMAN"}),
            encoding="utf-8",
        )
        ph.write_text(json.dumps({"escalation_resets": 0}), encoding="utf-8")
        cfg = {
            "pipeline_state_path": str(ps),
            "phase_state_path": str(ph),
            "project_dir_path": str(proj),
            "lock_path": str(tmp_path / "noop.lock"),
        }
        with patch("ui.server.load_config", return_value=cfg), patch(
            "ui.server._orchestrator_alive_from_config", return_value=True
        ):
            r = test_client.post("/api/stop")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["message"] == "Stop command queued for orchestrator"
        assert data["orchestrator_alive"] is True
        assert "hint" not in data
        art = proj / ".autodev" / "pipeline"
        ej = art / "escalation_output.json"
        ed = art / "escalation_output.done"
        assert ej.exists() and ed.exists()
        written = json.loads(ej.read_text(encoding="utf-8"))
        assert written["command"] == "STOP"
        assert written["source"] == "ui"

    def test_stop_waiting_for_human_hint_when_orchestrator_down(self, test_client, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        ps = tmp_path / "pipeline_state.json"
        ph = tmp_path / "phase_state.json"
        ps.write_text(
            json.dumps({"pipeline_status": "WAITING_FOR_HUMAN"}),
            encoding="utf-8",
        )
        ph.write_text(json.dumps({"escalation_resets": 0}), encoding="utf-8")
        cfg = {
            "pipeline_state_path": str(ps),
            "phase_state_path": str(ph),
            "project_dir_path": str(proj),
            "lock_path": str(tmp_path / "noop.lock"),
        }
        with patch("ui.server.load_config", return_value=cfg), patch(
            "ui.server._orchestrator_alive_from_config", return_value=False
        ):
            r = test_client.post("/api/stop")
        assert r.status_code == 200
        data = r.json()
        assert data["orchestrator_alive"] is False
        assert "hint" in data and len(data["hint"]) > 0

    def test_stop_waiting_for_human_writes_via_symlink_realpath(self, test_client, tmp_path):
        real = tmp_path / "real_proj"
        real.mkdir()
        link = tmp_path / "plink"
        os.symlink(real, link, target_is_directory=True)
        ps = tmp_path / "pipeline_state.json"
        ph = tmp_path / "phase_state.json"
        ps.write_text(
            json.dumps({"pipeline_status": "WAITING_FOR_HUMAN"}),
            encoding="utf-8",
        )
        ph.write_text(json.dumps({"escalation_resets": 0}), encoding="utf-8")
        cfg = {
            "pipeline_state_path": str(ps),
            "phase_state_path": str(ph),
            "project_dir_path": str(link),
            "lock_path": str(tmp_path / "noop.lock"),
        }
        with patch("ui.server.load_config", return_value=cfg), patch(
            "ui.server._orchestrator_alive_from_config", return_value=True
        ):
            r = test_client.post("/api/stop")
        assert r.status_code == 200
        art = real / ".autodev" / "pipeline"
        assert (art / "escalation_output.json").exists()
        assert (art / "escalation_output.done").exists()

    def test_stop_rejects_stopped_state(self, test_client, tmp_path):
        ps = tmp_path / "pipeline_state.json"
        ps.write_text(json.dumps({"pipeline_status": "STOPPED"}), encoding="utf-8")
        cfg = {
            "pipeline_state_path": str(ps),
            "project_dir_path": str(tmp_path / "proj"),
            "lock_path": str(tmp_path / "noop.lock"),
        }
        with patch("ui.server.load_config", return_value=cfg):
            r = test_client.post("/api/stop")
        assert r.status_code == 409
