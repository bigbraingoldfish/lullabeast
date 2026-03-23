"""Tests for POST /api/ideas create endpoint."""
import json
import os
import pytest
from fastapi.testclient import TestClient

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ui.server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Test client with temporary ideas_dir."""
    ideas_dir = tmp_path / "ideas"
    ideas_dir.mkdir()

    def mock_load_config(self_config=None):
        return {"ideas_dir": str(ideas_dir), "port": 18790}

    monkeypatch.setattr("ui.server.load_config", mock_load_config)
    client = TestClient(app)
    return client, ideas_dir


class TestPostIdeas:
    def test_creates_directory_and_session_json(self, client, monkeypatch):
        """POST /api/ideas creates the idea directory and session.json."""
        client_obj, ideas_dir = client

        response = client_obj.post("/api/ideas")
        assert response.status_code == 200
        data = response.json()
        assert "id" in data

        idea_id = data["id"]
        idea_path = ideas_dir / idea_id
        assert idea_path.is_dir()
        assert (idea_path / "session.json").exists()

    def test_returns_uuid_format(self, client, monkeypatch):
        """POST /api/ideas returns a valid UUID string."""
        client_obj, ideas_dir = client

        response = client_obj.post("/api/ideas")
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        idea_id = data["id"]
        # UUID format: 8-4-4-4-12 hex
        parts = idea_id.split("-")
        assert len(parts) == 5
        assert len(parts[0]) == 8
        assert len(parts[4]) == 12

    def test_session_json_has_correct_schema(self, client, monkeypatch):
        """session.json has messages=[], prd_content='', created, updated."""
        client_obj, ideas_dir = client

        response = client_obj.post("/api/ideas")
        idea_id = response.json()["id"]
        idea_path = ideas_dir / idea_id

        with open(idea_path / "session.json") as f:
            session = json.load(f)

        assert session["messages"] == []
        assert session["prd_content"] == ""
        assert session.get("name") == "New Idea"
        assert "created" in session
        assert "updated" in session

    def test_subsequent_get_includes_new_idea(self, client, monkeypatch):
        """After POST, GET /api/ideas does not list the idea until first turn completes."""
        client_obj, ideas_dir = client

        post_response = client_obj.post("/api/ideas")
        idea_id = post_response.json()["id"]

        get_response = client_obj.get("/api/ideas")
        assert get_response.status_code == 200
        data = get_response.json()
        assert not any(idea["id"] == idea_id for idea in data)
