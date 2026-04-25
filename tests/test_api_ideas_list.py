"""Tests for GET /api/ideas list endpoint."""
import json
import os
from pathlib import Path
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

    def test_name_falls_back_to_untitled_when_no_heading_or_messages(self, client, monkeypatch):
        """When prd_content has no # heading and no user messages, name is Untitled Idea."""
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
        assert data[0]["name"] == "Untitled Idea"
        # Resolved name persisted
        saved = json.loads((idea_path / "session.json").read_text())
        assert saved["name"] == "Untitled Idea"

    def test_uuid_stored_name_resolves_from_prd_heading(self, client, monkeypatch):
        """Session name equal to UUID is replaced with prd_draft / prd heading."""
        client_obj, ideas_dir = client
        uid = "67fbade1-7150-46e3-814b-3029a489d0a5"
        idea_path = ideas_dir / uid
        idea_path.mkdir()
        turns = idea_path / "turns"
        turns.mkdir()
        (turns / "1.done").write_text("done")
        session = {
            "name": uid,
            "messages": [],
            "prd_content": "# Resolved Title\n\n## Problem Statement\nx.",
            "updated": "2026-01-01T00:00:00Z",
        }
        (idea_path / "session.json").write_text(json.dumps(session))

        response = client_obj.get("/api/ideas")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Resolved Title"
        assert uid not in data[0]["name"]

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


class TestGetIdeasListReadinessScore:
    """UI-6: GET /api/ideas includes readiness_score from readiness.json."""

    def _create_listed_idea(self, ideas_dir, idea_id: str, session: dict) -> Path:
        idea_path = ideas_dir / idea_id
        idea_path.mkdir()
        turns = idea_path / "turns"
        turns.mkdir()
        (turns / "1.done").write_text("done")
        (idea_path / "session.json").write_text(json.dumps(session))
        return idea_path

    def test_readiness_score_returned_when_json_exists(self, client, monkeypatch):
        """Idea with readiness.json {"score": 7} returns readiness_score 7."""
        client_obj, ideas_dir = client
        idea_path = self._create_listed_idea(
            ideas_dir,
            "ready-idea",
            {
                "messages": [],
                "prd_content": "# T\n\n## Problem Statement\nx.",
                "updated": "2026-01-02T00:00:00Z",
            },
        )
        (idea_path / "readiness.json").write_text(json.dumps({"score": 7}))

        response = client_obj.get("/api/ideas")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["readiness_score"] == 7

    def test_readiness_score_null_when_file_absent(self, client, monkeypatch):
        """Idea with no readiness.json returns readiness_score null."""
        client_obj, ideas_dir = client
        self._create_listed_idea(
            ideas_dir,
            "no-readiness",
            {
                "messages": [],
                "prd_content": "# T\n\n## Problem Statement\nx.",
                "updated": "2026-01-02T00:00:00Z",
            },
        )

        response = client_obj.get("/api/ideas")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["readiness_score"] is None

    def test_readiness_score_null_when_file_malformed(self, client, monkeypatch):
        """Malformed readiness.json returns readiness_score null, no 500."""
        client_obj, ideas_dir = client
        idea_path = self._create_listed_idea(
            ideas_dir,
            "bad-json",
            {
                "messages": [],
                "prd_content": "# T\n\n## Problem Statement\nx.",
                "updated": "2026-01-02T00:00:00Z",
            },
        )
        (idea_path / "readiness.json").write_text("{ not valid json")

        response = client_obj.get("/api/ideas")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["readiness_score"] is None


class TestGetIdeasListDocFlags:
    """UI-7: GET /api/ideas includes has_prd and has_roadmap from session.json."""

    def _create_listed_idea(self, ideas_dir, idea_id: str, session: dict) -> Path:
        idea_path = ideas_dir / idea_id
        idea_path.mkdir()
        turns = idea_path / "turns"
        turns.mkdir()
        (turns / "1.done").write_text("done")
        (idea_path / "session.json").write_text(json.dumps(session))
        return idea_path

    def test_has_prd_true_when_prd_content_present(self, client, monkeypatch):
        client_obj, ideas_dir = client
        self._create_listed_idea(
            ideas_dir,
            "prd-yes",
            {
                "messages": [],
                "prd_content": "# Title\n\nBody.",
                "updated": "2026-01-02T00:00:00Z",
            },
        )
        response = client_obj.get("/api/ideas")
        assert response.status_code == 200
        assert response.json()[0]["has_prd"] is True

    def test_has_prd_false_when_prd_content_empty(self, client, monkeypatch):
        client_obj, ideas_dir = client
        self._create_listed_idea(
            ideas_dir,
            "prd-no",
            {
                "messages": [],
                "prd_content": "   ",
                "updated": "2026-01-02T00:00:00Z",
            },
        )
        response = client_obj.get("/api/ideas")
        assert response.status_code == 200
        assert response.json()[0]["has_prd"] is False

    def test_has_roadmap_true_when_roadmap_content_present(self, client, monkeypatch):
        client_obj, ideas_dir = client
        self._create_listed_idea(
            ideas_dir,
            "rm-yes",
            {
                "messages": [],
                "prd_content": "# T\n\n## Problem Statement\nx.",
                "roadmap_content": "# Roadmap\n\nPhase 1.",
                "updated": "2026-01-02T00:00:00Z",
            },
        )
        response = client_obj.get("/api/ideas")
        assert response.status_code == 200
        assert response.json()[0]["has_roadmap"] is True

    def test_has_roadmap_false_when_roadmap_content_empty(self, client, monkeypatch):
        client_obj, ideas_dir = client
        self._create_listed_idea(
            ideas_dir,
            "rm-no",
            {
                "messages": [],
                "prd_content": "# T\n\n## Problem Statement\nx.",
                "updated": "2026-01-02T00:00:00Z",
            },
        )
        response = client_obj.get("/api/ideas")
        assert response.status_code == 200
        assert response.json()[0]["has_roadmap"] is False


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
