"""Tests for GET /api/ideas list endpoint."""
import json
import os
import shutil
import tempfile
import pytest
from fastapi.testclient import TestClient

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ui.server import app, load_config


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Test client with temporary ideas_dir."""
    ideas_dir = tmp_path / "ideas"
    ideas_dir.mkdir()

    # Override config to use temp ideas_dir
    def mock_load_config(self_config=None):
        config = {
            "ideas_dir": str(ideas_dir),
            "port": 18790,
        }
        return config

    monkeypatch.setattr("ui.server.load_config", mock_load_config)
    client = TestClient(app)
    return client, ideas_dir


class TestGetIdeasList:
    def test_returns_empty_array_when_dir_absent(self, tmp_path, monkeypatch):
        """When ideas_dir does not exist, GET /api/ideas returns []."""
        ideas_dir = tmp_path / "nonexistent_ideas"
        # Ensure it does NOT exist
        assert not ideas_dir.exists()

        def mock_load_config(self_config=None):
            return {"ideas_dir": str(ideas_dir), "port": 18790}

        monkeypatch.setattr("ui.server.load_config", mock_load_config)
        client = TestClient(app)

        response = client.get("/api/ideas")
        assert response.status_code == 200
        assert response.json() == []

    def test_returns_empty_array_when_dir_empty(self, client, monkeypatch):
        """When ideas_dir exists but is empty, GET /api/ideas returns []."""
        client_obj, ideas_dir = client
        response = client_obj.get("/api/ideas")
        assert response.status_code == 200
        assert response.json() == []

    def test_returns_list_with_id_name_summary_updated(self, client, monkeypatch):
        """When ideas exist, returns array of {id, name, summary, updated}."""
        client_obj, ideas_dir = client

        # Create an idea subdirectory (listed only after first turn completes)
        idea_id = "abc123"
        idea_path = ideas_dir / idea_id
        idea_path.mkdir()
        turns = idea_path / "turns"
        turns.mkdir()
        (turns / "1.done").write_text("done")

        # Write session.json with prd_content
        session = {
            "messages": [],
            "prd_content": "# My Great Idea\n\n## Problem Statement\nThis is a summary sentence.\n\n## Goals\nGoal one.",
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-02T00:00:00Z",
        }
        (idea_path / "session.json").write_text(json.dumps(session))

        response = client_obj.get("/api/ideas")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == idea_id
        assert data[0]["name"] == "My Great Idea"
        assert data[0]["summary"] == "This is a summary sentence"
        assert data[0]["updated"] == "2026-01-02T00:00:00Z"

    def test_name_falls_back_to_id_when_no_heading(self, client, monkeypatch):
        """When prd_content has no # heading, name falls back to idea id."""
        client_obj, ideas_dir = client

        idea_id = "fallback-test"
        idea_path = ideas_dir / idea_id
        idea_path.mkdir()
        turns = idea_path / "turns"
        turns.mkdir()
        (turns / "1.done").write_text("done")

        session = {
            "messages": [],
            "prd_content": "No heading here",
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }
        (idea_path / "session.json").write_text(json.dumps(session))

        response = client_obj.get("/api/ideas")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == idea_id

    def test_summary_is_blank_when_no_problem_statement(self, client, monkeypatch):
        """When prd_content has no ## Problem Statement, summary is blank."""
        client_obj, ideas_dir = client

        idea_id = "no-problem"
        idea_path = ideas_dir / idea_id
        idea_path.mkdir()
        turns = idea_path / "turns"
        turns.mkdir()
        (turns / "1.done").write_text("done")

        session = {
            "messages": [],
            "prd_content": "# Title\n\nSome text without a problem statement.",
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }
        (idea_path / "session.json").write_text(json.dumps(session))

        response = client_obj.get("/api/ideas")
        assert response.status_code == 200
        data = response.json()
        assert data[0]["summary"] == ""

    def test_multiple_ideas_sorted_by_updated(self, client, monkeypatch):
        """Multiple ideas are returned sorted by updated time (newest first)."""
        client_obj, ideas_dir = client

        for idea_id, updated in [("old", "2026-01-01T00:00:00Z"), ("new", "2026-03-01T00:00:00Z")]:
            idea_path = ideas_dir / idea_id
            idea_path.mkdir()
            turns = idea_path / "turns"
            turns.mkdir()
            (turns / "1.done").write_text("done")
            session = {
                "messages": [],
                "prd_content": "# Idea " + idea_id,
                "created": updated,
                "updated": updated,
            }
            (idea_path / "session.json").write_text(json.dumps(session))

        response = client_obj.get("/api/ideas")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["id"] == "new"
        assert data[1]["id"] == "old"


class TestDeleteIdeas:
    def test_delete_nonexistent_returns_404(self, client, monkeypatch):
        """DELETE /api/ideas/{id} returns 404 for non-existent idea."""
        client_obj, ideas_dir = client

        response = client_obj.delete("/api/ideas/nonexistent-id")
        assert response.status_code == 404

    def test_delete_existing_removes_directory(self, client, monkeypatch):
        """DELETE /api/ideas/{id} removes the idea directory and returns 200."""
        client_obj, ideas_dir = client

        idea_id = "to-delete"
        idea_path = ideas_dir / idea_id
        idea_path.mkdir()
        (idea_path / "session.json").write_text(json.dumps({}))

        response = client_obj.delete(f"/api/ideas/{idea_id}")
        assert response.status_code == 200
        assert not idea_path.exists()
