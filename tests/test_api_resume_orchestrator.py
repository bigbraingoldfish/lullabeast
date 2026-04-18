"""Tests for POST /api/resume-orchestrator symlink vs pipeline_state project_path."""

import json
import os
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ui.server import app


def test_resume_reconciles_symlink_when_state_differs(tmp_path: Path):
    actual = tmp_path / "actual_project"
    actual.mkdir()
    stale = tmp_path / "stale_project"
    stale.mkdir()
    link = tmp_path / "pipeline_link"
    os.symlink(stale, link, target_is_directory=True)

    state_path = tmp_path / "pipeline_state.json"
    state_path.write_text(
        json.dumps({"project_path": str(actual), "pipeline_status": "STOPPED"}),
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
    body = r.json()
    assert body["ok"] is True
    assert body["reconciled"] is True
    assert body["reconcile_action"] == "symlink_to_state"
    assert body["previous_symlink_real"] == os.path.realpath(str(stale))
    assert body["canonical_project_real"] == os.path.realpath(str(actual))

    m.assert_called_once()
    assert m.call_args[0][0] == str(actual)
    assert os.path.realpath(str(link)) == os.path.realpath(str(actual))


def test_resume_reconcile_refuses_when_link_is_real_directory(tmp_path: Path):
    actual = tmp_path / "actual_project"
    actual.mkdir()
    link_dir = tmp_path / "pipeline_not_a_symlink"
    link_dir.mkdir()

    state_path = tmp_path / "pipeline_state.json"
    state_path.write_text(
        json.dumps({"project_path": str(actual), "pipeline_status": "STOPPED"}),
        encoding="utf-8",
    )

    orch = tmp_path / "oc"
    orch.mkdir()
    (orch / "orchestrator.py").write_text("# mock\n", encoding="utf-8")
    cfg = {
        "pipeline_state_path": str(state_path),
        "project_dir_path": str(link_dir),
        "autodev_repo_path": str(orch),
    }

    with patch("ui.server.load_config", return_value=cfg), patch("ui.server._spawn_orchestrator") as m:
        r = TestClient(app).post("/api/resume-orchestrator")

    assert r.status_code == 422
    detail = r.json().get("detail", "")
    assert "real directory" in detail.lower() or "refusing" in detail.lower()
    m.assert_not_called()


def test_resume_reconcile_then_spawn_fails_returns_503_with_reconciled_true(tmp_path: Path):
    actual = tmp_path / "actual_project"
    actual.mkdir()
    stale = tmp_path / "stale_project"
    stale.mkdir()
    link = tmp_path / "pipeline_link"
    os.symlink(stale, link, target_is_directory=True)

    state_path = tmp_path / "pipeline_state.json"
    state_path.write_text(
        json.dumps({"project_path": str(actual), "pipeline_status": "STOPPED"}),
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

    with patch("ui.server.load_config", return_value=cfg), patch(
        "ui.server._spawn_orchestrator",
        return_value={"ok": False, "error": "boom"},
    ):
        r = TestClient(app).post("/api/resume-orchestrator")

    assert r.status_code == 503
    body = r.json()
    assert body.get("ok") is False
    assert body.get("reconciled") is True
    assert body.get("reconcile_action") == "symlink_to_state"
    assert body.get("error") == "boom"
    assert body.get("canonical_project_real") == os.path.realpath(str(actual))
    assert os.path.realpath(str(link)) == os.path.realpath(str(actual))


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
    body = r.json()
    assert body["ok"] is True
    assert body.get("reconciled") is False
    m.assert_called_once()
    assert m.call_args[0][0] == str(project)
