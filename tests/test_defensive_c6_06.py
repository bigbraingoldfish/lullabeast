"""C6-06: _queue_run_trigger_next_logic must distinguish an empty queue from
a queue where all projects are blocked/dependency-hold.

Without the fix, both cases return the same "all projects blocked or in
dependency hold" message.  With the fix, an empty queue returns a distinct
{"ok": False, "reason": "queue_empty"} response.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from ui.server import app, _queue_run_trigger_next_logic

client = TestClient(app)


def _base_config(queue_path: Path) -> dict:
    return {
        "pipeline_queue_path": str(queue_path),
        "pipeline_state_path": "",
        "pipeline_lock_path": "",
    }


class TestC606EmptyQueue:
    def test_empty_queue_returns_queue_empty_reason(self, tmp_path):
        """With no entries in the queue, _queue_run_trigger_next_logic must return
        {'ok': False, 'reason': 'queue_empty'} not a generic halted message."""
        queue_path = tmp_path / "pipeline_queue.json"
        queue_path.write_text(json.dumps({"queue": [], "queue_mode": "auto", "last_updated": ""}))
        config = _base_config(queue_path)

        result = _queue_run_trigger_next_logic(config)

        assert result.get("ok") is False, f"Expected ok=False for empty queue, got: {result}"
        assert result.get("reason") == "queue_empty", (
            f"Expected reason='queue_empty' for empty queue, got: {result.get('reason')!r}. "
            "Empty queue and blocked queue are indistinguishable without this fix (C6-06 unfixed)."
        )

    def test_blocked_queue_does_not_return_queue_empty_reason(self, tmp_path):
        """A non-empty queue where all entries are BLOCKED should NOT return 'queue_empty'."""
        queue_path = tmp_path / "pipeline_queue.json"
        queue_path.write_text(json.dumps({
            "queue": [
                {
                    "id": "abc",
                    "name": "blocked-project",
                    "project_path": "/nonexistent/path",
                    "state": "BLOCKED",
                    "position": 0,
                },
            ],
            "queue_mode": "auto",
            "last_updated": "",
        }))
        config = _base_config(queue_path)

        result = _queue_run_trigger_next_logic(config)

        # blocked queue returns {"queue_halted": True, ...} — just assert it's not queue_empty
        assert result.get("reason") != "queue_empty", (
            "BLOCKED queue should not return 'queue_empty' reason"
        )

    def test_post_trigger_next_empty_queue_returns_400_or_200_with_queue_empty(self, tmp_path):
        """POST /api/queue/trigger-next with empty queue should surface a distinct message."""
        queue_path = tmp_path / "pipeline_queue.json"
        queue_path.write_text(json.dumps({"queue": [], "queue_mode": "auto", "last_updated": ""}))

        with patch("ui.server.load_config", return_value=_base_config(queue_path)):
            resp = client.post("/api/queue/trigger-next")

        # The endpoint either returns 200 with queue_empty reason, or 409/similar
        # The key requirement: 'queue_empty' is surfaced somehow (not the generic halted message)
        data = resp.json()
        assert "queue_empty" in str(data), (
            f"Expected 'queue_empty' to appear in trigger-next response for empty queue, got: {data}"
        )
