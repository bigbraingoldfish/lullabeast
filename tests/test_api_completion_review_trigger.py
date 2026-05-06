"""Tests for W5-E: POST /api/completion-review/{project} on-demand trigger endpoint.

Key invariants:
- 409 if pipeline.lock held (orchestrator running)
- 409 if pipeline status is not PIPELINE_COMPLETE
- 200 with triggered:true on success
- Missing completion_report.md after trigger does not cause a 5xx
- Uses sync write_sentinel (not async) per test-authoring rule
"""

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient


def load_client():
    from ui.server import app
    return TestClient(app)


def _base_config(tmp_path: Path, pipeline_status: str = "PIPELINE_COMPLETE") -> dict:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    lock_path = tmp_path / "pipeline.lock"
    ps_path = tmp_path / "pipeline_state.json"
    ps_path.write_text(
        json.dumps({"pipeline_status": pipeline_status, "status": pipeline_status}),
        encoding="utf-8",
    )
    return {
        "project_dir_path": str(project_dir),
        "openclaw_root": str(tmp_path),
        "autodev_pipeline_root": str(tmp_path),
        "pipeline_state_path": str(ps_path),
        "lock_path": str(lock_path),
        "pipeline_queue_path": str(tmp_path / "pipeline_queue.json"),
        "events_path": str(tmp_path / "pipeline_events.jsonl"),
        "hooks_url": "http://localhost:18789/hooks/agent",
        "hooks_token": "test-token",
    }


def write_sentinel(path):
    """Sync sentinel writer — satisfies test-authoring rule (no async def)."""
    Path(path).touch()


class TestPostCompletionReviewTrigger:

    def test_returns_404_when_endpoint_not_implemented(self, tmp_path):
        """Endpoint must exist — 404 means W5-E is not yet implemented."""
        config = _base_config(tmp_path)
        client = load_client()
        with patch("ui.server.load_config", return_value=config), \
             patch("ui.server._check_orchestrator_liveness", return_value=False):
            resp = client.post("/api/completion-review/my-project")
        # Any status other than 404 means the endpoint exists (even if it errors differently)
        assert resp.status_code != 404, "W5-E endpoint not found — not yet implemented"

    def test_returns_409_when_lock_held(self, tmp_path):
        """If orchestrator lock is held, return 409 with error='queue_active'."""
        config = _base_config(tmp_path)
        client = load_client()

        with patch("ui.server.load_config", return_value=config), \
             patch("ui.server._check_orchestrator_liveness", return_value=True):
            resp = client.post("/api/completion-review/my-project")

        assert resp.status_code == 409
        data = resp.json()
        assert data.get("error") == "queue_active"

    def test_returns_409_when_not_pipeline_complete(self, tmp_path):
        """If pipeline status is not PIPELINE_COMPLETE, return 409 with error='not_complete'."""
        config = _base_config(tmp_path, pipeline_status="RUNNING")
        client = load_client()

        with patch("ui.server.load_config", return_value=config), \
             patch("ui.server._check_orchestrator_liveness", return_value=False):
            resp = client.post("/api/completion-review/my-project")

        assert resp.status_code == 409
        data = resp.json()
        assert data.get("error") == "not_complete"

    def _success_patches(self, config, sentinel_return=True):
        """Common patch context for successful completion review trigger."""
        return (
            patch("ui.server.load_config", return_value=config),
            patch("ui.server._check_orchestrator_liveness", return_value=False),
            patch("ui.server.SkillManager"),
            patch("ui.server.invoke_agent_webhook", return_value=None),
            patch("ui.server.cleanup_output_files", return_value=None),
            patch("ui.server.poll_for_sentinel", return_value=sentinel_return),
        )

    def test_returns_triggered_true_on_success(self, tmp_path):
        """Lock free + PIPELINE_COMPLETE + mocked skill/webhook/poll → triggered:true."""
        config = _base_config(tmp_path, pipeline_status="PIPELINE_COMPLETE")
        client = load_client()

        patches = self._success_patches(config)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/completion-review/my-project")

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("triggered") is True

    def test_session_key_in_response(self, tmp_path):
        """Response must include the session_key used for this invocation."""
        config = _base_config(tmp_path, pipeline_status="PIPELINE_COMPLETE")
        client = load_client()

        patches = self._success_patches(config)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/completion-review/my-project")

        data = resp.json()
        assert "session_key" in data
        assert "completion" in data["session_key"]
        assert "my-project" in data["session_key"]
        assert "reviewer" in data["session_key"]

    def test_missing_report_after_trigger_does_not_cause_5xx(self, tmp_path):
        """Sentinel found=False (report not written) must return 200, not 5xx."""
        config = _base_config(tmp_path, pipeline_status="PIPELINE_COMPLETE")
        client = load_client()

        patches = self._success_patches(config, sentinel_return=False)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/completion-review/my-project")

        assert resp.status_code == 200

    def test_409_detail_message_present(self, tmp_path):
        """409 responses must include a human-readable detail message."""
        config = _base_config(tmp_path)
        client = load_client()

        with patch("ui.server.load_config", return_value=config), \
             patch("ui.server._check_orchestrator_liveness", return_value=True):
            resp = client.post("/api/completion-review/my-project")

        data = resp.json()
        assert "detail" in data
        assert len(data["detail"]) > 10
