"""Tests for POST /api/setup/switch-project and GET /api/setup/recent-projects."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ui.server import app

VALID_ROADMAP_SEED = (
    "- [ ] `TEST-E1` | LOW | Do the thing\n"
    "  > Test: It works.\n"
)

WORKSPACE_AGENTS = ["planner", "executor", "reviewer", "escalation"]
WORKSPACE_DOCS = ["AGENTS.md", "TOOLS.md", "SOUL.md", "USER.md", "IDENTITY.md"]


def _make_workspace(base_dir: Path, agent: str):
    ws = base_dir / f"workspace-{agent}"
    ws.mkdir(parents=True, exist_ok=True)
    for doc in WORKSPACE_DOCS:
        (ws / doc).write_text(f"# {doc}\n")
    return ws


def _make_openclaw_dir(tmp_path: Path, repo_path: Path):
    openclaw = tmp_path / ".openclaw"
    openclaw.mkdir(parents=True, exist_ok=True)
    link = openclaw / "pipeline-project"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(repo_path)
    for agent in WORKSPACE_AGENTS:
        _make_workspace(openclaw, agent)
    return openclaw


def _mock_subprocess_preflight_pass():
    def _inner(cmd, **kwargs):
        mock = MagicMock()
        mock.stderr = ""
        if not isinstance(cmd, list) or not cmd:
            mock.returncode = 0
            mock.stdout = ""
            return mock
        if cmd[0] == "git" and len(cmd) >= 2 and cmd[1] == "--version":
            mock.returncode = 0
            mock.stdout = "git version 2.40.0\n"
            return mock
        if "branch" in cmd and "--list" in cmd:
            mock.returncode = 0
            mock.stdout = "  main\n"
            return mock
        if "symbolic-ref" in cmd:
            mock.returncode = 0
            mock.stdout = "main\n"
            return mock
        mock.returncode = 0
        mock.stdout = ""
        return mock

    return _inner


def _stopped_state(path: Path):
    path.write_text(json.dumps({"pipeline_status": "STOPPED"}), encoding="utf-8")


@pytest.fixture
def client():
    return TestClient(app)


class TestRecentProjects:
    def test_get_recent_projects_returns_list(self, tmp_path, client):
        recent = tmp_path / "recent.json"
        recent.parent.mkdir(parents=True, exist_ok=True)
        recent.write_text(json.dumps([{"path": "/tmp/a", "last_used": "2020-01-01T00:00:00Z"}]))
        with patch("ui.server._ui_recent_projects_path", return_value=str(recent)):
            r = client.get("/api/setup/recent-projects")
        assert r.status_code == 200
        assert isinstance(r.json().get("projects"), list)


class TestSwitchProject:
    def test_switch_roadmap_ambiguous_two_files(self, tmp_path, client):
        repo_path = tmp_path / "proj"
        repo_path.mkdir()
        (repo_path / "roadmap.md").write_text(VALID_ROADMAP_SEED, encoding="utf-8")
        (repo_path / "extra_roadmap.md").write_text(VALID_ROADMAP_SEED, encoding="utf-8")
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        state = tmp_path / "pipeline_state.json"
        _stopped_state(state)
        cfg = {"pipeline_state_path": str(state)}

        with patch("ui.server.os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else p), \
             patch("ui.server.load_config", return_value=cfg), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            r = client.post("/api/setup/switch-project", json={"repo_path": str(repo_path)})

        assert r.status_code == 200
        data = r.json()
        assert data.get("roadmap_ambiguous") is True
        assert "roadmap.md" in (data.get("roadmap_files") or [])

    def test_switch_rejects_when_pipeline_running(self, tmp_path, client):
        repo_path = tmp_path / "proj"
        repo_path.mkdir()
        (repo_path / "roadmap.md").write_text(VALID_ROADMAP_SEED, encoding="utf-8")
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        state = tmp_path / "pipeline_state.json"
        state.write_text(json.dumps({"pipeline_status": "RUNNING"}), encoding="utf-8")
        cfg = {"pipeline_state_path": str(state)}

        with patch("ui.server.os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else p), \
             patch("ui.server.load_config", return_value=cfg):
            r = client.post("/api/setup/switch-project", json={"repo_path": str(repo_path)})

        assert r.status_code == 409

    def test_switch_allowed_when_waiting_human_and_project_dir_dangling(self, tmp_path, client):
        repo_path = tmp_path / "proj"
        repo_path.mkdir()
        (repo_path / "roadmap.md").write_text(VALID_ROADMAP_SEED, encoding="utf-8")
        (repo_path / ".git").mkdir()
        (repo_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        bad_link = tmp_path / "dangling_project_dir"
        bad_link.symlink_to("/tmp/nonexistent_autodev_switch_target")
        state = tmp_path / "pipeline_state.json"
        state.write_text(
            json.dumps({"pipeline_status": "WAITING_FOR_HUMAN"}),
            encoding="utf-8",
        )
        cfg = {
            "pipeline_state_path": str(state),
            "project_dir_path": str(bad_link),
        }

        with patch("ui.server.os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in str(p) else p), \
             patch("ui.server.load_config", return_value=cfg), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            r = client.post(
                "/api/setup/switch-project",
                json={"repo_path": str(repo_path), "start_orchestrator": False},
            )

        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_switch_rejects_when_waiting_human_and_project_dir_healthy(self, tmp_path, client):
        repo_path = tmp_path / "proj"
        repo_path.mkdir()
        (repo_path / "roadmap.md").write_text(VALID_ROADMAP_SEED, encoding="utf-8")
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        state = tmp_path / "pipeline_state.json"
        state.write_text(
            json.dumps({"pipeline_status": "WAITING_FOR_HUMAN"}),
            encoding="utf-8",
        )
        cfg = {
            "pipeline_state_path": str(state),
            "project_dir_path": str(repo_path),
        }

        with patch("ui.server.os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in str(p) else p), \
             patch("ui.server.load_config", return_value=cfg):
            r = client.post("/api/setup/switch-project", json={"repo_path": str(repo_path)})

        assert r.status_code == 409

    def test_switch_ready_after_single_roadmap(self, tmp_path, client):
        repo_path = tmp_path / "proj"
        repo_path.mkdir()
        (repo_path / "roadmap.md").write_text(VALID_ROADMAP_SEED, encoding="utf-8")
        (repo_path / ".git").mkdir()
        (repo_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        state = tmp_path / "pipeline_state.json"
        _stopped_state(state)
        cfg = {"pipeline_state_path": str(state)}

        with patch("ui.server.os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else p), \
             patch("ui.server.load_config", return_value=cfg), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            r = client.post(
                "/api/setup/switch-project",
                json={"repo_path": str(repo_path), "start_orchestrator": False},
            )

        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        assert data.get("ready_to_start") is True
        assert data.get("coherence", {}).get("ok") is True
