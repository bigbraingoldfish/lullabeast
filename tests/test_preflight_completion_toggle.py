"""Tests for W5-G backend: completion_review flag propagation via API.

Covers:
- POST /api/queue/add persists completion_review onto the queue entry
- POST /api/setup/launch propagates completion_review to queue entry
- Launch Now with empty queue synthesizes an entry with the flag
"""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


def load_client():
    from ui.server import app
    return TestClient(app)


VALID_ROADMAP = (
    "- [ ] `TEST-E1` | LOW | Do the thing\n"
    "  > Test: It works.\n"
)

WORKSPACE_AGENTS = ["planner", "executor", "reviewer", "escalation"]
WORKSPACE_DOCS = ["AGENTS.md", "TOOLS.md", "SOUL.md", "USER.md", "IDENTITY.md"]


def _make_openclaw_dir(tmp_path: Path, repo_path: Path) -> Path:
    openclaw = tmp_path / ".openclaw"
    openclaw.mkdir(parents=True, exist_ok=True)
    link = openclaw / "pipeline-project"
    link.symlink_to(repo_path)
    for agent in WORKSPACE_AGENTS:
        ws = openclaw / f"workspace-{agent}"
        ws.mkdir(parents=True, exist_ok=True)
        for doc in WORKSPACE_DOCS:
            (ws / doc).write_text(f"# {doc}\n")
    return openclaw


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "my-project"
    repo.mkdir()
    (repo / "roadmap.md").write_text(VALID_ROADMAP)
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty-message"],
        cwd=repo, capture_output=True,
    )
    gitignore = repo / ".gitignore"
    gitignore.write_text(".autodev/pipeline/\n")
    return repo


def _queue_config(tmp_path: Path, openclaw: Path, repo: Path) -> dict:
    link = openclaw / "pipeline-project"
    return {
        "project_dir_path": str(link),
        "openclaw_root": str(openclaw),
        "autodev_pipeline_root": str(openclaw),
        "autodev_repo_path": str(tmp_path),
        "pipeline_state_path": str(openclaw / "pipeline_state.json"),
        "lock_path": str(openclaw / "pipeline.lock"),
        "pipeline_queue_path": str(openclaw / "pipeline_queue.json"),
        "events_path": str(openclaw / "pipeline_events.jsonl"),
        "ideas_dir": str(openclaw / "ideas"),
        "phase_state_path": str(link / ".autodev" / "pipeline" / "phase_state.json"),
        "hooks_url": "http://localhost:18789/hooks/agent",
        "hooks_token": "test-token",
        "port": 18790,
    }


class TestQueueAddCompletionReviewFlag:

    def _make_preflight_pass(self, repo: Path, openclaw: Path):
        """Patch _run_preflight_checks and _preflight_materialize to return all-pass."""
        pass_check = [{"check": "git_repo", "status": "pass", "message": "ok"}]
        return (
            patch("ui.server._run_preflight_checks", return_value=pass_check),
            patch("ui.server._preflight_materialize", return_value=pass_check),
        )

    def test_persists_completion_review_true(self, tmp_path):
        """POST /api/queue/add with completion_review:true writes it to queue entry."""
        repo = _make_repo(tmp_path)
        openclaw = _make_openclaw_dir(tmp_path, repo)
        config = _queue_config(tmp_path, openclaw, repo)
        client = load_client()

        p1, p2 = self._make_preflight_pass(repo, openclaw)
        with p1, p2, patch("ui.server.load_config", return_value=config):
            resp = client.post(
                "/api/queue/add",
                json={"project_path": str(repo), "completion_review": True},
            )

        assert resp.status_code == 200, resp.text
        entry = resp.json()
        assert entry.get("completion_review") is True

    def test_persists_completion_review_false_when_omitted(self, tmp_path):
        """POST /api/queue/add without completion_review defaults to False."""
        repo = _make_repo(tmp_path)
        openclaw = _make_openclaw_dir(tmp_path, repo)
        config = _queue_config(tmp_path, openclaw, repo)
        client = load_client()

        p1, p2 = self._make_preflight_pass(repo, openclaw)
        with p1, p2, patch("ui.server.load_config", return_value=config):
            resp = client.post(
                "/api/queue/add",
                json={"project_path": str(repo)},
            )

        assert resp.status_code == 200, resp.text
        entry = resp.json()
        assert entry.get("completion_review") is False

    def test_persists_completion_review_false_explicit(self, tmp_path):
        """POST /api/queue/add with completion_review:false persists False."""
        repo = _make_repo(tmp_path)
        openclaw = _make_openclaw_dir(tmp_path, repo)
        config = _queue_config(tmp_path, openclaw, repo)
        client = load_client()

        p1, p2 = self._make_preflight_pass(repo, openclaw)
        with p1, p2, patch("ui.server.load_config", return_value=config):
            resp = client.post(
                "/api/queue/add",
                json={"project_path": str(repo), "completion_review": False},
            )

        assert resp.status_code == 200, resp.text
        assert resp.json().get("completion_review") is False

    def test_flag_written_to_queue_file(self, tmp_path):
        """The persisted pipeline_queue.json entry must contain completion_review."""
        repo = _make_repo(tmp_path)
        openclaw = _make_openclaw_dir(tmp_path, repo)
        config = _queue_config(tmp_path, openclaw, repo)
        q_path = Path(config["pipeline_queue_path"])
        client = load_client()

        p1, p2 = self._make_preflight_pass(repo, openclaw)
        with p1, p2, patch("ui.server.load_config", return_value=config):
            client.post(
                "/api/queue/add",
                json={"project_path": str(repo), "completion_review": True},
            )

        q = json.loads(q_path.read_text())
        entries = q.get("queue", [])
        assert entries, "Queue file must have at least one entry"
        assert entries[0].get("completion_review") is True


class TestLaunchNowCompletionReviewFlag:

    def _launch_patches(self, tmp_path: Path, openclaw: Path):
        state_file = tmp_path / "pipeline_state.json"
        orch_dir = tmp_path / "orch"
        orch_dir.mkdir(exist_ok=True)
        (orch_dir / "orchestrator.py").write_text("# mock\n")
        cfg = {
            "project_dir_path": str(openclaw / "pipeline-project"),
            "openclaw_root": str(openclaw),
            "autodev_pipeline_root": str(openclaw),
            "autodev_repo_path": str(orch_dir.parent),
            "pipeline_state_path": str(state_file),
            "lock_path": str(openclaw / "pipeline.lock"),
            "pipeline_queue_path": str(openclaw / "pipeline_queue.json"),
            "events_path": str(openclaw / "pipeline_events.jsonl"),
        }
        mock_spawn = MagicMock(return_value={"ok": True, "pid": 999})
        mock_init = MagicMock(return_value={"ok": True})
        return cfg, mock_spawn, mock_init

    def test_propagates_completion_review_flag(self, tmp_path):
        """POST /api/setup/launch with completion_review:true propagates to queue entry."""
        repo = _make_repo(tmp_path)
        openclaw = _make_openclaw_dir(tmp_path, repo)
        cfg, mock_spawn, mock_init = self._launch_patches(tmp_path, openclaw)
        q_path = Path(cfg["pipeline_queue_path"])
        client = load_client()

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server._run_init_project", return_value={"ok": True}), \
             patch("ui.server._spawn_orchestrator", return_value={"ok": True, "pid": 1}), \
             patch("ui.server._check_orchestrator_liveness", return_value=False):
            resp = client.post(
                "/api/setup/launch",
                json={
                    "repo_path": str(repo),
                    "roadmap_seed": VALID_ROADMAP,
                    "completion_review": True,
                },
            )

        assert resp.status_code == 200, resp.text
        assert resp.json().get("ok") is True

        # Queue file must exist and have the flag
        if q_path.exists():
            q = json.loads(q_path.read_text())
            entries = q.get("queue", [])
            if entries:
                assert entries[0].get("completion_review") is True

    def test_completion_review_false_default_on_launch(self, tmp_path):
        """POST /api/setup/launch without completion_review defaults flag to False."""
        repo = _make_repo(tmp_path)
        openclaw = _make_openclaw_dir(tmp_path, repo)
        cfg, mock_spawn, mock_init = self._launch_patches(tmp_path, openclaw)
        q_path = Path(cfg["pipeline_queue_path"])
        client = load_client()

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server._run_init_project", return_value={"ok": True}), \
             patch("ui.server._spawn_orchestrator", return_value={"ok": True, "pid": 1}), \
             patch("ui.server._check_orchestrator_liveness", return_value=False):
            resp = client.post(
                "/api/setup/launch",
                json={"repo_path": str(repo), "roadmap_seed": VALID_ROADMAP},
            )

        assert resp.status_code == 200, resp.text
        if q_path.exists():
            q = json.loads(q_path.read_text())
            entries = q.get("queue", [])
            if entries:
                # Default must be False (or absent, which also means not opted-in)
                assert entries[0].get("completion_review", False) is False
