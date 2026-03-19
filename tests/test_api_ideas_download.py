"""Tests for GET /api/ideas/{id}/download endpoint."""
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


class TestDownloadIdeas:
    def test_returns_content_disposition_header(self, client, monkeypatch):
        """Response includes Content-Disposition header for file download."""
        client_obj, ideas_dir = client

        # Create an idea
        post_response = client_obj.post("/api/ideas")
        idea_id = post_response.json()["id"]
        idea_path = ideas_dir / idea_id

        # Write session.json with prd_content
        session = {
            "messages": [],
            "prd_content": "# My Project\n\n## Problem Statement\nSomething here.",
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }
        (idea_path / "session.json").write_text(json.dumps(session))

        response = client_obj.get(f"/api/ideas/{idea_id}/download")
        assert response.status_code == 200
        content_disposition = response.headers.get("content-disposition", "")
        assert "attachment" in content_disposition
        assert "filename=" in content_disposition

    def test_filename_from_first_heading(self, client, monkeypatch):
        """Filename uses first # heading from prd_content."""
        client_obj, ideas_dir = client

        post_response = client_obj.post("/api/ideas")
        idea_id = post_response.json()["id"]
        idea_path = ideas_dir / idea_id

        session = {
            "messages": [],
            "prd_content": "# My Awesome Project\n\n## Problem Statement\nContent here.",
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }
        (idea_path / "session.json").write_text(json.dumps(session))

        response = client_obj.get(f"/api/ideas/{idea_id}/download")
        content_disposition = response.headers.get("content-disposition", "")
        assert "My-Awesome-Project-prd.md" in content_disposition

    def test_filename_falls_back_to_id(self, client, monkeypatch):
        """When prd_content has no # heading, filename uses idea id."""
        client_obj, ideas_dir = client

        post_response = client_obj.post("/api/ideas")
        idea_id = post_response.json()["id"]
        idea_path = ideas_dir / idea_id

        session = {
            "messages": [],
            "prd_content": "No heading in this file.",
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }
        (idea_path / "session.json").write_text(json.dumps(session))

        response = client_obj.get(f"/api/ideas/{idea_id}/download")
        content_disposition = response.headers.get("content-disposition", "")
        assert idea_id in content_disposition
        assert "-prd.md" in content_disposition

    def test_returns_prd_content(self, client, monkeypatch):
        """Response body is the prd_content from session.json."""
        client_obj, ideas_dir = client

        post_response = client_obj.post("/api/ideas")
        idea_id = post_response.json()["id"]
        idea_path = ideas_dir / idea_id

        prd = "# Test Project\n\n## Problem Statement\nThe main problem.\n\n## Goals\nGoal one."
        session = {
            "messages": [],
            "prd_content": prd,
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }
        (idea_path / "session.json").write_text(json.dumps(session))

        response = client_obj.get(f"/api/ideas/{idea_id}/download")
        assert response.status_code == 200
        assert response.text == prd

    def test_nonexistent_idea_returns_404(self, client, monkeypatch):
        """GET /api/ideas/{id}/download returns 404 for non-existent idea."""
        client_obj, ideas_dir = client

        response = client_obj.get("/api/ideas/nonexistent-id/download")
        assert response.status_code == 404
