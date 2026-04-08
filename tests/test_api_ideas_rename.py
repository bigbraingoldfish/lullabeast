"""Tests for PATCH /api/ideas/{id} rename endpoint."""
import json
import os
import re
import sys

import pytest
from fastapi.testclient import TestClient

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
    return TestClient(app), ideas_dir


class TestRenameIdeas:
    def test_patch_rename_updates_session_name_and_timestamp(self, client):
        """PATCH persists trimmed name and updates session timestamp."""
        client_obj, ideas_dir = client

        post_response = client_obj.post("/api/ideas")
        idea_id = post_response.json()["id"]
        session_path = ideas_dir / idea_id / "session.json"
        before_session = json.loads(session_path.read_text())
        before_updated = before_session["updated"]

        response = client_obj.patch(f"/api/ideas/{idea_id}", json={"name": "  Better Idea Name  "})

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["id"] == idea_id
        assert body["name"] == "Better Idea Name"

        after_session = json.loads(session_path.read_text())
        assert after_session["name"] == "Better Idea Name"
        assert after_session["updated"] != before_updated
        assert re.match(r".+Z$", after_session["updated"])

    def test_patch_rename_returns_404_for_missing_idea(self, client):
        """PATCH returns 404 for a missing idea directory."""
        client_obj, _ideas_dir = client

        response = client_obj.patch("/api/ideas/does-not-exist", json={"name": "Renamed"})

        assert response.status_code == 404

    def test_patch_rename_rejects_whitespace_name(self, client):
        """PATCH rejects whitespace-only names after trimming."""
        client_obj, ideas_dir = client

        post_response = client_obj.post("/api/ideas")
        idea_id = post_response.json()["id"]
        session_path = ideas_dir / idea_id / "session.json"
        original_session = json.loads(session_path.read_text())
        original_name = original_session["name"]

        response = client_obj.patch(f"/api/ideas/{idea_id}", json={"name": "   "})

        assert response.status_code == 400
        # Ensure failed request does not mutate persisted name.
        after_session = json.loads(session_path.read_text())
        assert after_session["name"] == original_name
