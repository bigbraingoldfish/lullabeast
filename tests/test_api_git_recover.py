"""Tests for POST /api/pipeline/git-recover."""

import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ui.server import app


def _mk_proc(returncode=0, stdout="", stderr=""):
    class _P:
        pass

    p = _P()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


def test_git_recover_success_updates_pipeline_state(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    state_path = tmp_path / "pipeline_state.json"
    state_path.write_text(
        json.dumps(
            {
                "pipeline_status": "RUNNING",
                "status": "RUNNING",
                "current_agent": "escalation",
            }
        ),
        encoding="utf-8",
    )

    cfg = {
        "project_dir_path": str(project_dir),
        "pipeline_state_path": str(state_path),
        "base_branch": "",
    }

    def fake_run(cmd, **kwargs):
        if cmd[:4] == ["git", "show-ref", "--verify", "--quiet"] and cmd[4].endswith("/main"):
            return _mk_proc(returncode=0)
        if cmd == ["git", "checkout", "main"]:
            return _mk_proc(returncode=0)
        if cmd[:3] == ["git", "stash", "push"]:
            return _mk_proc(returncode=0, stdout="Saved working directory")
        if cmd == ["git", "symbolic-ref", "refs/remotes/origin/HEAD"]:
            return _mk_proc(returncode=1)
        if cmd == ["git", "config", "--get", "init.defaultBranch"]:
            return _mk_proc(returncode=1)
        return _mk_proc(returncode=0)

    client = TestClient(app)
    with patch("ui.server.load_config", return_value=cfg), patch("ui.server.subprocess.run", side_effect=fake_run):
        res = client.post("/api/pipeline/git-recover", json={})

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["base_branch"] == "main"

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["pipeline_status"] == "RUNNING"
    assert state["status"] == "RUNNING"
    assert state["current_agent"] == "planner"


def test_git_recover_returns_503_when_project_missing(tmp_path: Path):
    cfg = {
        "project_dir_path": str(tmp_path / "missing_project"),
        "pipeline_state_path": str(tmp_path / "pipeline_state.json"),
        "base_branch": "",
    }
    client = TestClient(app)
    with patch("ui.server.load_config", return_value=cfg):
        res = client.post("/api/pipeline/git-recover", json={})

    assert res.status_code == 503
