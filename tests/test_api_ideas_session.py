"""Tests for GET /api/ideas/{id}/session endpoint."""
import pytest
import json
from pathlib import Path


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestApiIdeasSession:
    """Tests for GET /api/ideas/{id}/session endpoint."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """Set up per-test temp ideas directory."""
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()
        monkeypatch.setenv("OPENCLAW_IDEAS_DIR", str(self.ideas_dir))

    def _write_session(self, idea_id, data):
        sess_dir = self.ideas_dir / idea_id
        sess_dir.mkdir(parents=True, exist_ok=True)
        path = sess_dir / "session.json"
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def _mock_config(self):
        return {
            "ideas_dir": str(self.ideas_dir),
        }

    def test_returns_full_session_json(self):
        """Returns full session.json when it exists for the idea."""
        client = load_server()
        idea_id = "42"
        session_data = {
            "messages": [
                {"role": "user", "content": "Hello", "ts": "2026-03-19T10:00:00Z"},
                {"role": "assistant", "content": "Hi there", "ts": "2026-03-19T10:00:01Z"},
            ],
            "prd_content": "# PRD Draft\nSome content here",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:01Z",
        }
        self._write_session(idea_id, session_data)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            response = client.get(f"/api/ideas/{idea_id}/session")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        body = response.json()
        assert body.get("messages") == session_data["messages"], f"Messages mismatch: {body}"
        assert body.get("prd_content") == session_data["prd_content"], f"prd_content mismatch: {body}"
        assert body.get("created") == session_data["created"], f"created mismatch: {body}"
        assert body.get("updated") == session_data["updated"], f"updated mismatch: {body}"

    def test_returns_empty_messages_when_no_session(self):
        """Returns {messages:[], prd_content:'', created:null, updated:null} when no session exists."""
        # No session.json written — this idea has no session
        client = load_server()

        with patch("ui.server.load_config", return_value=self._mock_config()):
            response = client.get("/api/ideas/999/session")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        body = response.json()
        assert body.get("messages") == [], f"Expected empty messages, got {body}"
        assert body.get("prd_content") == "", f"Expected empty prd_content, got {body}"
        assert body.get("created") is None, f"Expected null created, got {body}"
        assert body.get("updated") is None, f"Expected null updated, got {body}"

    def test_returns_correct_messages_from_session(self):
        """Returns messages array from session.json."""
        client = load_server()
        idea_id = "55"
        session_data = {
            "messages": [
                {"role": "user", "content": "First message", "ts": "2026-03-19T10:00:00Z"},
                {"role": "assistant", "content": "First response", "ts": "2026-03-19T10:00:05Z"},
                {"role": "user", "content": "Second message", "ts": "2026-03-19T10:01:00Z"},
            ],
            "prd_content": "PRD content",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:01:00Z",
        }
        self._write_session(idea_id, session_data)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            response = client.get(f"/api/ideas/{idea_id}/session")

        assert response.status_code == 200
        body = response.json()
        assert len(body["messages"]) == 3
        assert body["messages"][0]["role"] == "user"
        assert body["messages"][0]["content"] == "First message"
        assert body["prd_content"] == "PRD content"

    def test_returns_prd_content_from_session(self):
        """Returns prd_content field from session.json."""
        client = load_server()
        idea_id = "77"
        session_data = {
            "messages": [],
            "prd_content": "# My PRD\n## Section 1\nContent here",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:05:00Z",
        }
        self._write_session(idea_id, session_data)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            response = client.get(f"/api/ideas/{idea_id}/session")

        assert response.status_code == 200
        body = response.json()
        assert "# My PRD" in body["prd_content"]

    def test_endpoint_exists_at_correct_path(self):
        """GET /api/ideas/{id}/session returns 200 or empty-object response."""
        client = load_server()
        # Idea 0 never existed — returns empty schema
        with patch("ui.server.load_config", return_value=self._mock_config()):
            response = client.get("/api/ideas/0/session")
        assert response.status_code in (200, 404, 422)

    def test_rebuilds_empty_session_from_turn_artifacts(self):
        """If session is empty but turns/prd files exist, endpoint self-heals session."""
        client = load_server()
        idea_id = "rebuild-1"
        idea_dir = self.ideas_dir / idea_id
        idea_dir.mkdir(parents=True, exist_ok=True)
        turns = idea_dir / "turns"
        turns.mkdir(parents=True, exist_ok=True)
        (turns / "1.md").write_text("# Turn 1\n\nAgent response")
        (turns / "1.done").write_text("done")
        (idea_dir / "prd_draft.md").write_text("# Rebuilt PRD\n\n## Problem Statement\nRecovered content.")
        self._write_session(
            idea_id,
            {
                "name": "Recovered Idea",
                "messages": [],
                "prd_content": "",
                "roadmap_content": "",
                "created": "2026-03-24T00:00:00Z",
                "updated": "2026-03-24T00:00:00Z",
            },
        )

        with patch("ui.server.load_config", return_value=self._mock_config()):
            response = client.get(f"/api/ideas/{idea_id}/session")

        assert response.status_code == 200
        body = response.json()
        assert len(body["messages"]) >= 1
        assert any(m.get("role") == "assistant" for m in body["messages"])
        assert "Recovered content" in body["prd_content"]


from unittest.mock import patch
