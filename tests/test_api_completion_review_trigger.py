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

    def _success_patches(self, config):
        """Common patch context for successful completion review trigger."""
        return (
            patch("ui.server.load_config", return_value=config),
            patch("ui.server._check_orchestrator_liveness", return_value=False),
            patch("ui.server.SkillManager"),
            patch("ui.server.invoke_agent_webhook", return_value=None),
            patch("ui.server.cleanup_output_files", return_value=None),
        )

    def test_returns_triggered_true_on_success(self, tmp_path):
        """Lock free + PIPELINE_COMPLETE + mocked skill/webhook → triggered:true."""
        config = _base_config(tmp_path, pipeline_status="PIPELINE_COMPLETE")
        client = load_client()

        patches = self._success_patches(config)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            resp = client.post("/api/completion-review/my-project")

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("triggered") is True

    def test_session_key_in_response(self, tmp_path):
        """Response must include the session_key used for this invocation."""
        config = _base_config(tmp_path, pipeline_status="PIPELINE_COMPLETE")
        client = load_client()

        patches = self._success_patches(config)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            resp = client.post("/api/completion-review/my-project")

        data = resp.json()
        assert "session_key" in data
        assert "completion" in data["session_key"]
        assert "my-project" in data["session_key"]
        assert "reviewer" in data["session_key"]

    def test_returns_200_even_if_report_not_written_yet(self, tmp_path):
        """Fire-and-forget: endpoint returns 200 immediately, report appears later."""
        config = _base_config(tmp_path, pipeline_status="PIPELINE_COMPLETE")
        client = load_client()

        patches = self._success_patches(config)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
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

    def test_webhook_receives_completion_message(self, tmp_path):
        """invoke_agent_webhook must receive a message= kwarg with completion-specific instructions."""
        config = _base_config(tmp_path, pipeline_status="PIPELINE_COMPLETE")
        client = load_client()

        with patch("ui.server.load_config", return_value=config), \
             patch("ui.server._check_orchestrator_liveness", return_value=False), \
             patch("ui.server.SkillManager"), \
             patch("ui.server.invoke_agent_webhook", return_value=None) as mock_webhook, \
             patch("ui.server.cleanup_output_files", return_value=None):
            resp = client.post("/api/completion-review/my-project")

        assert resp.status_code == 200
        mock_webhook.assert_called_once()
        call_kwargs = mock_webhook.call_args
        # Must pass a custom message kwarg — not rely on webhook_client default
        msg = call_kwargs.kwargs.get("message") or (call_kwargs.args[5] if len(call_kwargs.args) > 5 else None)
        assert msg is not None, (
            "invoke_agent_webhook must receive an explicit message= kwarg — "
            "without it, the default reviewer message tells the agent to do code review"
        )
        assert "completion" in msg.lower(), (
            "Webhook message must mention 'completion' — the agent needs to know "
            "this is a documentation pass, not a code review"
        )

    def test_webhook_message_mentions_completion_report(self, tmp_path):
        """The custom message must instruct the agent to write completion_report.md."""
        config = _base_config(tmp_path, pipeline_status="PIPELINE_COMPLETE")
        client = load_client()

        with patch("ui.server.load_config", return_value=config), \
             patch("ui.server._check_orchestrator_liveness", return_value=False), \
             patch("ui.server.SkillManager"), \
             patch("ui.server.invoke_agent_webhook", return_value=None) as mock_webhook, \
             patch("ui.server.cleanup_output_files", return_value=None):
            resp = client.post("/api/completion-review/my-project")

        assert resp.status_code == 200
        call_kwargs = mock_webhook.call_args
        all_args = call_kwargs.args + tuple(call_kwargs.kwargs.values())
        msg_str = " ".join(str(a) for a in all_args)
        assert "completion_report" in msg_str, (
            "Webhook message must mention completion_report.md so the agent knows what to produce"
        )

    def test_endpoint_does_not_block_on_sentinel(self, tmp_path):
        """Endpoint must not call poll_for_sentinel synchronously (blocks the event loop).

        The fix is fire-and-forget: trigger webhook and return immediately.
        The UI polls GET /api/completion-report to detect when the report appears.
        """
        import inspect
        from ui.server import post_completion_review_trigger
        source = inspect.getsource(post_completion_review_trigger)
        assert "poll_for_sentinel" not in source, (
            "post_completion_review_trigger must not call poll_for_sentinel — "
            "it blocks the async event loop for up to 300s. Use fire-and-forget instead."
        )

    def test_returns_immediately_without_waiting(self, tmp_path):
        """Endpoint should return quickly (fire-and-forget), not block for sentinel."""
        import time as _time
        config = _base_config(tmp_path, pipeline_status="PIPELINE_COMPLETE")
        client = load_client()

        with patch("ui.server.load_config", return_value=config), \
             patch("ui.server._check_orchestrator_liveness", return_value=False), \
             patch("ui.server.SkillManager"), \
             patch("ui.server.invoke_agent_webhook", return_value=None), \
             patch("ui.server.cleanup_output_files", return_value=None):
            t0 = _time.monotonic()
            resp = client.post("/api/completion-review/my-project")
            elapsed = _time.monotonic() - t0

        assert resp.status_code == 200
        assert elapsed < 5.0, (
            f"Endpoint took {elapsed:.1f}s — must return immediately after triggering webhook, "
            "not block waiting for sentinel"
        )


class TestCompletionMessageWalkthroughStructure:
    """The on-demand UI trigger and the auto-end-of-pipeline trigger must produce
    identically-structured completion_report.md walkthroughs. See plan §2.

    These assertions mirror autodev/tests/test_orchestrator_completion_review.py's
    TestCompletionMessageContent so the two code paths stay in sync.
    """

    def _capture_message(self, tmp_path):
        config = _base_config(tmp_path, pipeline_status="PIPELINE_COMPLETE")
        client = load_client()
        with patch("ui.server.load_config", return_value=config), \
             patch("ui.server._check_orchestrator_liveness", return_value=False), \
             patch("ui.server.SkillManager"), \
             patch("ui.server.invoke_agent_webhook", return_value=None) as mock_webhook, \
             patch("ui.server.cleanup_output_files", return_value=None):
            resp = client.post("/api/completion-review/my-project")
        assert resp.status_code == 200
        kwargs = mock_webhook.call_args.kwargs
        return kwargs.get("message", ""), config["project_dir_path"]

    def test_message_includes_open_terminal_step(self, tmp_path):
        msg, _ = self._capture_message(tmp_path)
        assert "Open Terminal" in msg or "Open a terminal" in msg, (
            "UI trigger prompt must begin the run instructions with 'Open Terminal'"
        )

    def test_message_includes_cd_to_project_path(self, tmp_path):
        msg, project_dir = self._capture_message(tmp_path)
        assert "cd " in msg, "UI trigger prompt must include a `cd ` step"
        assert project_dir in msg, (
            f"UI trigger prompt must interpolate the absolute project path "
            f"({project_dir!r}) — so the agent writes `cd /actual/path`, not `cd <your-path>`"
        )

    def test_message_uses_fresh_terminal_framing(self, tmp_path):
        msg, _ = self._capture_message(tmp_path)
        framing = msg.lower()
        assert "fresh terminal" in framing or "no prior context" in framing, (
            "UI trigger prompt must frame the audience as a fresh-terminal reader"
        )

    def test_message_requires_per_command_fenced_blocks(self, tmp_path):
        msg, _ = self._capture_message(tmp_path)
        lower = msg.lower()
        assert "fenced" in lower or "```" in msg, (
            "UI trigger prompt must require fenced code blocks"
        )
        assert "each" in lower or "one fenced" in lower or "own" in lower, (
            "UI trigger prompt must require ONE fenced block per command"
        )

    def test_message_contains_no_placeholder_literals(self, tmp_path):
        msg, _ = self._capture_message(tmp_path)
        assert "<your-path>" not in msg, "prompt must not contain a placeholder literal"
        assert "<project>" not in msg, "prompt must not contain a placeholder literal"
