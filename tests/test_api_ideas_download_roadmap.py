"""Tests for GET /api/ideas/{id}/download-roadmap endpoint."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestApiIdeasDownloadRoadmap:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()

    def _mock_config(self):
        return {"ideas_dir": str(self.ideas_dir)}

    def _write_session(self, idea_id, roadmap_content=None, prd_content=""):
        idea_dir = self.ideas_dir / idea_id
        idea_dir.mkdir(parents=True, exist_ok=True)
        session = {
            "messages": [],
            "prd_content": prd_content,
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }
        if roadmap_content is not None:
            session["roadmap_content"] = roadmap_content
        (idea_dir / "session.json").write_text(json.dumps(session))

    def test_returns_404_when_idea_not_found(self):
        client = load_server()
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/nonexistent/download-roadmap")
        assert r.status_code == 404

    def test_returns_404_when_no_roadmap_content(self):
        client = load_server()
        self._write_session("1", roadmap_content=None)
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/1/download-roadmap")
        assert r.status_code == 404

    def test_returns_404_when_roadmap_content_is_empty(self):
        client = load_server()
        self._write_session("2", roadmap_content="")
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/2/download-roadmap")
        assert r.status_code == 404

    def test_returns_200_with_roadmap_content(self):
        client = load_server()
        roadmap = "# Project Roadmap\n\n- [ ] `phase-1` | LOW | First phase"
        self._write_session("3", roadmap_content=roadmap)
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/3/download-roadmap")
        assert r.status_code == 200
        assert roadmap in r.text

    def test_content_type_is_markdown(self):
        client = load_server()
        self._write_session("4", roadmap_content="# Roadmap\n\nContent here.")
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/4/download-roadmap")
        assert "text/markdown" in r.headers.get("content-type", "")

    def test_content_disposition_attachment(self):
        client = load_server()
        self._write_session("5", roadmap_content="# Roadmap\n\nContent.")
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/5/download-roadmap")
        disposition = r.headers.get("content-disposition", "")
        assert "attachment" in disposition
        assert ".md" in disposition

    def test_filename_derived_from_prd_heading(self):
        """Filename uses first # heading from prd_content."""
        client = load_server()
        self._write_session(
            "6",
            roadmap_content="# Roadmap",
            prd_content="# My Project Idea\n\nSome content."
        )
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/6/download-roadmap")
        disposition = r.headers.get("content-disposition", "")
        assert "My-Project-Idea-roadmap.md" in disposition

    def test_filename_falls_back_to_idea_id(self):
        """Filename falls back to idea_id when no # heading in prd_content."""
        client = load_server()
        self._write_session("7", roadmap_content="# Roadmap", prd_content="No heading here.")
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/7/download-roadmap")
        disposition = r.headers.get("content-disposition", "")
        assert "7-roadmap.md" in disposition
