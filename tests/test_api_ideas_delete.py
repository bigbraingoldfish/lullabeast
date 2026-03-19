"""Tests for DELETE /api/ideas/{id} endpoint."""
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


class TestDeleteIdeas:
    def test_delete_removes_directory_and_contents(self, client, monkeypatch):
        """DELETE removes the idea directory and all its contents."""
        client_obj, ideas_dir = client

        # Create an idea
        post_response = client_obj.post("/api/ideas")
        idea_id = post_response.json()["id"]
        idea_path = ideas_dir / idea_id
        assert idea_path.exists()

        # Create some extra files in it
        (idea_path / "prd_draft.md").write_text("# PRD")
        (idea_path / "turns").mkdir()
        (idea_path / "turns" / "turn_0.md").write_text("content")

        delete_response = client_obj.delete(f"/api/ideas/{idea_id}")
        assert delete_response.status_code == 200
        assert not idea_path.exists()

    def test_delete_nonexistent_returns_404(self, client, monkeypatch):
        """DELETE /api/ideas/{id} returns 404 for non-existent id."""
        client_obj, ideas_dir = client

        response = client_obj.delete("/api/ideas/does-not-exist-at-all")
        assert response.status_code == 404

    def test_deleted_idea_not_in_list(self, client, monkeypatch):
        """After DELETE, the idea no longer appears in GET /api/ideas."""
        client_obj, ideas_dir = client

        post_response = client_obj.post("/api/ideas")
        idea_id = post_response.json()["id"]

        # Confirm it's in the list
        get_response = client_obj.get("/api/ideas")
        assert any(idea["id"] == idea_id for idea in get_response.json())

        # Delete it
        client_obj.delete(f"/api/ideas/{idea_id}")

        # Confirm it's gone
        get_response = client_obj.get("/api/ideas")
        assert all(idea["id"] != idea_id for idea in get_response.json())
