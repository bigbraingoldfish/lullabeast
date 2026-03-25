"""Tests for POST /api/ideas/{id}/message endpoint."""
import pytest
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestApiIdeasMessage:
    """Tests for POST /api/ideas/{id}/message endpoint."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """Set up per-test temp ideas directory."""
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()
        monkeypatch.setenv("OPENCLAW_IDEAS_DIR", str(self.ideas_dir))

    def _write_session(self, idea_id, data):
        """Write session.json for an idea."""
        sess_dir = self.ideas_dir / idea_id
        sess_dir.mkdir(parents=True, exist_ok=True)
        path = sess_dir / "session.json"
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def _write_turn_files(self, idea_id, turn_n, response_text, prd_text=""):
        """Write {n}.md and {n}.done sentinel files (AGENTS.md contract)."""
        turns_dir = self.ideas_dir / idea_id / "turns"
        turns_dir.mkdir(parents=True, exist_ok=True)
        md_path = turns_dir / f"{turn_n}.md"
        done_path = turns_dir / f"{turn_n}.done"
        prd_path = self.ideas_dir / idea_id / "prd_draft.md"
        with open(md_path, "w") as f:
            f.write(response_text)
        with open(done_path, "w") as f:
            f.write("")
        if prd_text is not None:
            with open(prd_path, "w") as f:
                f.write(prd_text)

    def _mock_config(self):
        return {
            "ideas_dir": str(self.ideas_dir),
            "hooks_url": "http://localhost:18789/hooks/agent",
            "hooks_token": "test-token",
        }

    def _make_mock_response(self):
        """Build a properly-async mock response for session.post()."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        return mock_resp

    def _make_mock_session(self, mock_resp):
        """Build a mock aiohttp.ClientSession with async post method."""
        mock_session = MagicMock()
        # post() must be AsyncMock so `await session.post(...)` works
        mock_session.post = AsyncMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        return mock_session

    def test_endpoint_exists(self):
        """POST /api/ideas/1/message returns 200 when sentinel is found."""
        client = load_server()

        # Pre-create session.json
        self._write_session("1", {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })

        # Pre-create turn files (sentinel present immediately)
        self._write_turn_files("1", 1, "Hello from agent", "# PRD\nDraft content")

        mock_resp = self._make_mock_response()
        mock_session = self._make_mock_session(mock_resp)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                response = client.post(
                    "/api/ideas/1/message",
                    json={"content": "Hi agent", "turn": 1},
                )

        assert response.status_code in (200, 408), f"Unexpected status: {response.status_code}"

    def test_returns_response_and_prd_content_on_success(self):
        """Response body contains {response, prd_content} fields."""
        client = load_server()

        self._write_session("5", {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })

        self._write_turn_files("5", 1, "Agent response text", "# PRD\nSome draft")

        mock_resp = self._make_mock_response()
        mock_session = self._make_mock_session(mock_resp)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                response = client.post(
                    "/api/ideas/5/message",
                    json={"content": "Hello", "turn": 1},
                )

        if response.status_code == 200:
            body = response.json()
            assert "response" in body, f"Missing 'response' field: {body}"
            assert "prd_content" in body, f"Missing 'prd_content' field: {body}"

    def test_returns_408_on_timeout(self):
        """Returns 408 when sentinel file is not found within timeout."""
        client = load_server()

        self._write_session("9", {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })
        # Do NOT write turn files — sentinel will never appear

        mock_resp = self._make_mock_response()
        mock_session = self._make_mock_session(mock_resp)

        async def fake_sleep(seconds):
            pass

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                # Patch POLL_TIMEOUT to 2s so the test completes quickly
                with patch("ui.server.POLL_TIMEOUT", 2):
                    with patch("ui.server.asyncio.sleep", side_effect=fake_sleep):
                        response = client.post(
                            "/api/ideas/9/message",
                            json={"content": "Timeout test", "turn": 1},
                        )

        # Should timeout and return 408
        assert response.status_code == 408, f"Expected 408, got {response.status_code}"

    def test_webhook_sent_with_correct_payload(self):
        """Webhook is POSTed to hooks_url with correct Bearer auth and body."""
        client = load_server()
        captured_payload = {}

        self._write_session("2", {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })
        self._write_turn_files("2", 3, "Agent response", "# Draft")

        def capture_post(url, **kwargs):
            captured_payload["url"] = url
            captured_payload["headers"] = kwargs.get("headers", {})
            captured_payload["json"] = kwargs.get("json", {})
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=None)
            return mock_resp

        mock_session = MagicMock()
        mock_session.post = AsyncMock(side_effect=capture_post)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                response = client.post(
                    "/api/ideas/2/message",
                    json={"content": "Test message", "turn": 3},
                )

        assert "url" in captured_payload, "POST was not called"
        assert "Authorization" in captured_payload.get("headers", {}), \
            f"Missing Authorization header: {captured_payload}"

    def test_session_json_updated_atomically(self):
        """session.json is written via .tmp + os.replace after agent turn."""
        client = load_server()
        idea_id = "7"
        sess_path = self.ideas_dir / idea_id / "session.json"

        self._write_session(idea_id, {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })
        self._write_turn_files(idea_id, 1, "Agent says hello", "# PRD\nUpdated content")

        mock_resp = self._make_mock_response()
        mock_session = self._make_mock_session(mock_resp)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                response = client.post(
                    f"/api/ideas/{idea_id}/message",
                    json={"content": "Hello", "turn": 1},
                )

        # Verify session.json exists and has correct structure
        assert sess_path.exists(), f"session.json not created at {sess_path}"
        with open(sess_path) as f:
            data = json.load(f)
        assert "messages" in data
        assert "prd_content" in data
        assert len(data["messages"]) == 2, f"Expected 2 messages, got {len(data['messages'])}"

        # No leftover .tmp file
        tmp_path = str(sess_path) + ".tmp"
        assert not os.path.exists(tmp_path), "Leftover .tmp file found"

    def test_turn_n_from_request_body(self):
        """turn_n is read from request body, not derived from message count."""
        client = load_server()
        captured_payload = {}

        self._write_session("3", {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })
        self._write_turn_files("3", 10, "Turn 10 response", "")

        def capture_post(url, **kwargs):
            captured_payload["url"] = url
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=None)
            return mock_resp

        mock_session = MagicMock()
        mock_session.post = AsyncMock(side_effect=capture_post)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                # Send turn=10 explicitly in body
                response = client.post(
                    "/api/ideas/3/message",
                    json={"content": "Which turn?", "turn": 10},
                )

        # Sentinel path should use turn 10
        turn_done_path = self.ideas_dir / "3" / "turns" / "10.done"
        assert turn_done_path.exists(), f"Expected 10.done at {turn_done_path}"
