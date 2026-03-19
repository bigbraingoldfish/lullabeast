"""Tests for POST /api/ideas/{idea_id}/upload"""
import pytest
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

# Ensure the ui module is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "ui"))

from fastapi.testclient import TestClient
from ui.server import app

client = TestClient(app)


@pytest.fixture
def ideas_dir(tmp_path):
    with patch("ui.server.load_config") as mock_config:
        mock_config.return_value = {"ideas_dir": str(tmp_path)}
        yield tmp_path


@pytest.fixture
def existing_idea(ideas_dir):
    idea_id = "test-idea-123"
    idea_path = ideas_dir / idea_id
    idea_path.mkdir(parents=True, exist_ok=True)
    session = {"messages": [], "prd_content": "", "created": "2024-01-01T00:00:00Z", "updated": "2024-01-01T00:00:00Z"}
    (idea_path / "session.json").write_text(json.dumps(session))
    return idea_id


class TestUploadMdFileWithAllHeaders:
    """pass_criteria: POST /api/ideas/{id}/upload with a .md file containing
    all 3 required headers returns 200 and stores content in session.json['prd_content']"""

    def test_upload_md_all_headers_returns_200(self, ideas_dir, existing_idea):
        content = (
            "# My Idea\n\n"
            "## Problem Statement\n"
            "There is a gap in the market.\n\n"
            "## Goals & Success Metrics\n"
            "Goal: fill the gap. Metric: revenue.\n\n"
            "## Functional Requirements\n"
            "1. Feature A\n"
            "2. Feature B\n"
        )
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {"ideas_dir": str(ideas_dir)}
            response = client.post(
                f"/api/ideas/{existing_idea}/upload",
                files={"file": ("myidea.md", content.encode(), "text/markdown")},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "format_ok"
        assert data["trigger_clarity_check"] is True

    def test_upload_stores_prd_content_in_session(self, ideas_dir, existing_idea):
        content = (
            "# My Idea\n\n"
            "## Problem Statement\n"
            "There is a gap.\n\n"
            "## Goals & Success Metrics\n"
            "Goal: fill it.\n\n"
            "## Functional Requirements\n"
            "1. Feature A\n"
        )
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {"ideas_dir": str(ideas_dir)}
            response = client.post(
                f"/api/ideas/{existing_idea}/upload",
                files={"file": ("myidea.md", content.encode(), "text/markdown")},
            )
        assert response.status_code == 200
        session_path = ideas_dir / existing_idea / "session.json"
        session = json.loads(session_path.read_text())
        assert session["prd_content"] == content


class TestUploadMdFileMissingHeaders:
    """pass_criteria: POST /api/ideas/{id}/upload with a .md file missing any
    of the 3 required headers returns 422 with the exact list of missing headers."""

    def test_missing_one_header_returns_422(self, ideas_dir, existing_idea):
        content = (
            "# My Idea\n\n"
            "## Problem Statement\n"
            "There is a gap.\n\n"
            "## Goals & Success Metrics\n"
            "Goal: fill it.\n\n"
            # Missing ## Functional Requirements
        )
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {"ideas_dir": str(ideas_dir)}
            response = client.post(
                f"/api/ideas/{existing_idea}/upload",
                files={"file": ("incomplete.md", content.encode(), "text/markdown")},
            )
        assert response.status_code == 422
        data = response.json()
        assert "Missing required headers" in data["detail"]
        # Check X-Missing-Headers header
        missing = response.headers.get("X-Missing-Headers", "").split(",")
        assert "## Functional Requirements" in missing

    def test_missing_two_headers_returns_422(self, ideas_dir, existing_idea):
        content = "# My Idea\n\n## Problem Statement\nThere is a gap.\n\n"
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {"ideas_dir": str(ideas_dir)}
            response = client.post(
                f"/api/ideas/{existing_idea}/upload",
                files={"file": ("incomplete.md", content.encode(), "text/markdown")},
            )
        assert response.status_code == 422
        missing = response.headers.get("X-Missing-Headers", "").split(",")
        assert "## Goals & Success Metrics" in missing
        assert "## Functional Requirements" in missing

    def test_empty_file_missing_all_headers_returns_422(self, ideas_dir, existing_idea):
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {"ideas_dir": str(ideas_dir)}
            response = client.post(
                f"/api/ideas/{existing_idea}/upload",
                files={"file": ("empty.md", b"", "text/markdown")},
            )
        assert response.status_code == 422
        missing = response.headers.get("X-Missing-Headers", "").split(",")
        assert "## Problem Statement" in missing
        assert "## Goals & Success Metrics" in missing
        assert "## Functional Requirements" in missing


class TestUploadNonMdFile:
    """pass_criteria: POST /api/ideas/{id}/upload with a non-.md file returns
    400/422 without reaching the header-check logic."""

    def test_upload_txt_file_returns_400(self, ideas_dir, existing_idea):
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {"ideas_dir": str(ideas_dir)}
            response = client.post(
                f"/api/ideas/{existing_idea}/upload",
                files={"file": ("readme.txt", b"# Readme", "text/plain")},
            )
        assert response.status_code == 400
        assert "Only .md files" in response.json()["detail"]

    def test_upload_no_extension_returns_400(self, ideas_dir, existing_idea):
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {"ideas_dir": str(ideas_dir)}
            response = client.post(
                f"/api/ideas/{existing_idea}/upload",
                files={"file": ("readme", b"# Readme", "text/plain")},
            )
        assert response.status_code == 400

    def test_upload_uppercase_md_extension_returns_200(self, ideas_dir, existing_idea):
        content = (
            "# My Idea\n\n"
            "## Problem Statement\n"
            "Gap.\n\n"
            "## Goals & Success Metrics\n"
            "Goal.\n\n"
            "## Functional Requirements\n"
            "1. A\n"
        )
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {"ideas_dir": str(ideas_dir)}
            response = client.post(
                f"/api/ideas/{existing_idea}/upload",
                files={"file": ("myidea.MD", content.encode(), "text/markdown")},
            )
        assert response.status_code == 200


class TestUploadIdeaNotFound:
    def test_upload_nonexistent_idea_returns_404(self, ideas_dir):
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {"ideas_dir": str(ideas_dir)}
            response = client.post(
                "/api/ideas/nonexistent-id/upload",
                files={"file": ("myidea.md", b"# Hi", "text/markdown")},
            )
        assert response.status_code == 404
