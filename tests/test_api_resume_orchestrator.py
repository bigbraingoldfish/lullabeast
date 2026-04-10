"""Tests for POST /api/resume-orchestrator symlink vs pipeline_state project_path."""

import json
import os
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ui.server import app


def test_resume_uses_symlink_realpath_when_state_path_differs(tmp_path: Path):
    real = tmp_path / "actual_project"
    real.mkdir()
    link = tmp_path / "pipeline_link"
    os.symlink(real, link, target_is_directory=True)

    stale = tmp_path / "other_project"
    stale.mkdir()
    state_path = tmp_path / "pipeline_state.json"
    state_path.write_text(
        json.dumps({"project_path": str(stale), "pipeline_status": "STOPPED"}),
        encoding="utf-8",
    )

    orch = tmp_path / "oc"
    orch.mkdir()
    (orch / "orchestrator.py").write_text("# mock\n", encoding="utf-8")
    cfg = {
        "pipeline_state_path": str(state_path),
        "project_dir_path": str(link),
        "autodev_repo_path": str(orch),
    }

    with patch("ui.server.load_config", return_value=cfg), patch("ui.server._spawn_orchestrator") as m:
        r = TestClient(app).post("/api/resume-orchestrator")

    assert r.status_code == 200
    m.assert_called_once()
    arg = m.call_args[0][0]
    assert os.path.samefile(arg, str(real))


def test_resume_orchestrator_409_when_lock_held(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    state_path = tmp_path / "pipeline_state.json"
    state_path.write_text(
        json.dumps({"project_path": str(project), "pipeline_status": "RUNNING"}),
        encoding="utf-8",
    )
    lock_file = tmp_path / "pipeline.lock"
    lock_file.write_text("", encoding="utf-8")
    orch = tmp_path / "oc"
    orch.mkdir()
    (orch / "orchestrator.py").write_text("# mock\n", encoding="utf-8")
    cfg = {
        "pipeline_state_path": str(state_path),
        "autodev_repo_path": str(orch),
        "lock_path": str(lock_file),
    }
    with (
        patch("ui.server.load_config", return_value=cfg),
        patch("ui.server._check_orchestrator_liveness", return_value=True),
        patch("ui.server._spawn_orchestrator") as m,
    ):
        r = TestClient(app).post("/api/resume-orchestrator")
    assert r.status_code == 409
    assert r.json()["detail"] == "Orchestrator is already running"
    m.assert_not_called()


def test_resume_orchestrator_200_when_lock_configured_but_free(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    state_path = tmp_path / "pipeline_state.json"
    state_path.write_text(
        json.dumps({"project_path": str(project), "pipeline_status": "RUNNING"}),
        encoding="utf-8",
    )
    lock_file = tmp_path / "pipeline.lock"
    lock_file.write_text("", encoding="utf-8")
    orch = tmp_path / "oc"
    orch.mkdir()
    (orch / "orchestrator.py").write_text("# mock\n", encoding="utf-8")
    cfg = {
        "pipeline_state_path": str(state_path),
        "autodev_repo_path": str(orch),
        "lock_path": str(lock_file),
    }
    with (
        patch("ui.server.load_config", return_value=cfg),
        patch("ui.server._check_orchestrator_liveness", return_value=False),
        patch("ui.server._spawn_orchestrator") as m,
    ):
        r = TestClient(app).post("/api/resume-orchestrator")
    assert r.status_code == 200
    m.assert_called_once()
    assert m.call_args[0][0] == str(project)
