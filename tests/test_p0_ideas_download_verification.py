"""P0 Stage B11: GET /api/ideas/{id}/download-verification.

Mirrors GET /api/ideas/{id}/download-roadmap. Returns the verification_content
from session.json as a markdown attachment, with the filename derived from the
first ``# heading`` in prd_content + ``-verification.md`` suffix.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestDownloadVerification:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()

    def _mock_config(self):
        return {"ideas_dir": str(self.ideas_dir)}

    def _write_session(self, idea_id, verification_content=None, prd_content=""):
        idea_dir = self.ideas_dir / idea_id
        idea_dir.mkdir(parents=True, exist_ok=True)
        session = {
            "messages": [],
            "prd_content": prd_content,
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }
        if verification_content is not None:
            session["verification_content"] = verification_content
        (idea_dir / "session.json").write_text(json.dumps(session))

    def test_returns_404_when_idea_not_found(self):
        client = load_server()
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/missing/download-verification")
        assert r.status_code == 404

    def test_returns_404_when_no_verification_content(self):
        client = load_server()
        self._write_session("1", verification_content=None)
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/1/download-verification")
        assert r.status_code == 404

    def test_returns_404_when_verification_content_empty(self):
        client = load_server()
        self._write_session("2", verification_content="")
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/2/download-verification")
        assert r.status_code == 404

    def test_returns_200_with_verification_content(self):
        client = load_server()
        verification = "# Verification\n\n## Project type\nweb-app\n"
        self._write_session("3", verification_content=verification)
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/3/download-verification")
        assert r.status_code == 200
        assert verification in r.text

    def test_content_type_is_markdown(self):
        client = load_server()
        self._write_session("4", verification_content="# Verification\n\nx")
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/4/download-verification")
        assert "text/markdown" in r.headers.get("content-type", "")

    def test_content_disposition_attachment(self):
        client = load_server()
        self._write_session("5", verification_content="# Verification")
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/5/download-verification")
        disposition = r.headers.get("content-disposition", "")
        assert "attachment" in disposition
        assert ".md" in disposition

    def test_filename_derived_from_prd_heading(self):
        client = load_server()
        self._write_session(
            "6",
            verification_content="# Verification",
            prd_content="# My Project Idea\n\nSome content.",
        )
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/6/download-verification")
        disposition = r.headers.get("content-disposition", "")
        assert "My-Project-Idea-verification.md" in disposition, (
            "Filename must derive from the first PRD heading + "
            "-verification.md suffix (mirroring download-roadmap)."
        )

    def test_filename_falls_back_to_idea_id(self):
        client = load_server()
        self._write_session("7", verification_content="# V", prd_content="No heading.")
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/7/download-verification")
        disposition = r.headers.get("content-disposition", "")
        assert "7-verification.md" in disposition
