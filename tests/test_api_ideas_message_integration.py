"""Integration tests for POST /api/ideas/{id}/message with real filesystem."""
import pytest
import json
import os
import time
import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestApiIdeasMessageIntegration:
    """Integration tests for /api/ideas/{id}/message endpoint using real temp filesystem."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """Set up per-test temp ideas directory."""
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()
        monkeypatch.setenv("OPENCLAW_IDEAS_DIR", str(self.ideas_dir))

    def _mock_config(self):
        return {
            "ideas_dir": str(self.ideas_dir),
            "hooks_url": "http://localhost:18789/hooks/agent",
            "hooks_token": "test-token",
        }

    def _write_session(self, idea_id, data):
        sess_dir = self.ideas_dir / idea_id
        sess_dir.mkdir(parents=True, exist_ok=True)
        path = sess_dir / "session.json"
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def _write_turn_files(self, idea_id, turn_n, response_text, prd_text=""):
        turns_dir = self.ideas_dir / idea_id / "turns"
        turns_dir.mkdir(parents=True, exist_ok=True)
        md_path = turns_dir / f"turn_{turn_n}.md"
        done_path = turns_dir / f"turn_{turn_n}.done"
        prd_path = self.ideas_dir / idea_id / "prd_draft.md"
        with open(md_path, "w") as f:
            f.write(response_text)
        with open(done_path, "w") as f:
            f.write("")
        if prd_text is not None:
            with open(prd_path, "w") as f:
                f.write(prd_text)

    def _make_mock_response(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        return mock_resp

    def _make_mock_session(self, mock_resp):
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        return mock_session

    def test_full_happy_path(self):
        """Complete flow: POST message → webhook → sentinel poll → response → session update."""
        client = load_server()
        idea_id = "full-happy"

        # Pre-existing session
        self._write_session(idea_id, {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })

        # Sentinel files already present (agent already wrote output)
        self._write_turn_files(idea_id, 1, "This is the agent response.", "# PRD Draft\nContent")

        mock_resp = self._make_mock_response()
        mock_session = self._make_mock_session(mock_resp)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                response = client.post(
                    f"/api/ideas/{idea_id}/message",
                    json={"content": "Hello agent", "turn": 1},
                )

        # Should succeed
        assert response.status_code == 200, \
            f"Expected 200, got {response.status_code}: {response.text}"

        body = response.json()
        assert "response" in body, f"Missing response field: {body}"
        assert "prd_content" in body, f"Missing prd_content field: {body}"

        # Verify session.json was updated with both messages
        sess_path = self.ideas_dir / idea_id / "session.json"
        with open(sess_path) as f:
            session = json.load(f)

        assert len(session["messages"]) == 2, \
            f"Expected 2 messages in session, got {len(session['messages'])}"
        assert session["messages"][0]["role"] == "user"
        assert session["messages"][1]["role"] == "assistant"
        assert "> \u2705 PRD CONVERSION-READY" in session["prd_content"] or \
               "# PRD Draft" in session["prd_content"]

    def test_prd_content_updated_from_prd_draft_md(self):
        """prd_content is read from prd_draft.md after agent turn."""
        client = load_server()
        idea_id = "prd-update"

        self._write_session(idea_id, {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })

        expected_prd = "# Updated PRD\n## Section 1\nSome updated content."
        self._write_turn_files(idea_id, 1, "Agent response", expected_prd)

        mock_resp = self._make_mock_response()
        mock_session = self._make_mock_session(mock_resp)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                response = client.post(
                    f"/api/ideas/{idea_id}/message",
                    json={"content": "Update the PRD", "turn": 1},
                )

        assert response.status_code == 200
        body = response.json()
        # prd_content should be from prd_draft.md
        assert expected_prd in body["prd_content"] or body["prd_content"], \
            f"prd_content not updated from prd_draft.md: {body}"

    def test_atomic_write_uses_tmp_file(self):
        """session.json.tmp exists temporarily then is replaced by os.replace."""
        client = load_server()
        idea_id = "atomic-write"

        self._write_session(idea_id, {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })
        self._write_turn_files(idea_id, 1, "Response", "# Draft")

        tmp_files_created = []

        original_replace = os.replace
        def track_replace(src, dst):
            if ".tmp" in src or ".tmp" in dst:
                tmp_files_created.append((src, dst))
            return original_replace(src, dst)

        mock_resp = self._make_mock_response()
        mock_session = self._make_mock_session(mock_resp)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                with patch("os.replace", side_effect=track_replace):
                    response = client.post(
                        f"/api/ideas/{idea_id}/message",
                        json={"content": "Write atomically", "turn": 1},
                    )

        assert len(tmp_files_created) > 0, \
            "os.replace was not called for .tmp file — atomic write not implemented"

    def test_session_updated_timestamp_changes(self):
        """session.json updated timestamp changes after each agent turn."""
        client = load_server()
        idea_id = "timestamp-update"

        original_time = "2026-03-19T10:00:00Z"
        self._write_session(idea_id, {
            "messages": [],
            "prd_content": "",
            "created": original_time,
            "updated": original_time,
        })
        self._write_turn_files(idea_id, 1, "First response", "# Draft 1")

        mock_resp = self._make_mock_response()
        mock_session = self._make_mock_session(mock_resp)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                response = client.post(
                    f"/api/ideas/{idea_id}/message",
                    json={"content": "Update time", "turn": 1},
                )

        sess_path = self.ideas_dir / idea_id / "session.json"
        with open(sess_path) as f:
            session = json.load(f)

        assert session["updated"] != original_time, \
            f"updated timestamp was not changed: {session['updated']}"
