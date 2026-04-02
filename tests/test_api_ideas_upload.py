"""Tests for POST /api/ideas/{idea_id}/upload"""
import json
import os
import sys
import pytest
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
    session = {
        "name": "New Idea",
        "messages": [],
        "prd_content": "",
        "created": "2024-01-01T00:00:00Z",
        "updated": "2024-01-01T00:00:00Z",
    }
    (idea_path / "session.json").write_text(json.dumps(session))
    return idea_id


class TestUploadMdFileSynthesis:
    """POST /api/ideas/{id}/upload writes seed, triggers agent, polls sentinel."""

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

        async def sleep_then_sentinel(delay):
            turns = ideas_dir / existing_idea / "turns"
            turns.mkdir(parents=True, exist_ok=True)
            for n in range(1, 6):
                (turns / f"{n}.done").write_text("done")
            (ideas_dir / existing_idea / "prd_draft.md").write_text("# Synth\n\n## Problem Statement\nOK.")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "ideas_dir": str(ideas_dir),
                "hooks_url": "http://x/hooks",
                "hooks_token": "t",
            }
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                with patch("ui.server.asyncio.sleep", AsyncMock(side_effect=sleep_then_sentinel)):
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

        async def sleep_then_sentinel(delay):
            turns = ideas_dir / existing_idea / "turns"
            turns.mkdir(parents=True, exist_ok=True)
            for n in range(1, 6):
                (turns / f"{n}.done").write_text("done")
            (ideas_dir / existing_idea / "prd_draft.md").write_text(content)

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "ideas_dir": str(ideas_dir),
                "hooks_url": "http://x/hooks",
                "hooks_token": "t",
            }
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                with patch("ui.server.asyncio.sleep", AsyncMock(side_effect=sleep_then_sentinel)):
                    response = client.post(
                        f"/api/ideas/{existing_idea}/upload",
                        files={"file": ("myidea.md", content.encode(), "text/markdown")},
                    )
        assert response.status_code == 200
        session_path = ideas_dir / existing_idea / "session.json"
        session = json.loads(session_path.read_text())
        assert session["prd_content"] == content
        assert (ideas_dir / existing_idea / "uploaded_seed.md").exists()


class TestUploadArbitraryMarkdown:
    """Any non-empty .md is accepted — agent synthesizes; no header gate."""

    def test_missing_headers_still_returns_200_with_mock_agent(self, ideas_dir, existing_idea):
        content = "# My Idea\n\nSome freeform text without template sections.\n"

        async def sleep_then_sentinel(delay):
            turns = ideas_dir / existing_idea / "turns"
            turns.mkdir(parents=True, exist_ok=True)
            for n in range(1, 6):
                (turns / f"{n}.done").write_text("done")
            (ideas_dir / existing_idea / "prd_draft.md").write_text("# Out\n")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "ideas_dir": str(ideas_dir),
                "hooks_url": "http://x/hooks",
                "hooks_token": "t",
            }
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                with patch("ui.server.POLL_INTERVAL", 0.05), patch(
                    "ui.server.asyncio.sleep", AsyncMock(side_effect=sleep_then_sentinel)
                ):
                    response = client.post(
                        f"/api/ideas/{existing_idea}/upload",
                        files={"file": ("incomplete.md", content.encode(), "text/markdown")},
                    )
        assert response.status_code == 200

    def test_empty_file_returns_400(self, ideas_dir, existing_idea):
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {"ideas_dir": str(ideas_dir)}
            response = client.post(
                f"/api/ideas/{existing_idea}/upload",
                files={"file": ("empty.md", b"", "text/markdown")},
            )
        assert response.status_code == 400


class TestUploadNonMdFile:
    """Non-.md files rejected client-side / server 400."""

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

        async def sleep_then_sentinel(delay):
            turns = ideas_dir / existing_idea / "turns"
            turns.mkdir(parents=True, exist_ok=True)
            for n in range(1, 6):
                (turns / f"{n}.done").write_text("done")
            (ideas_dir / existing_idea / "prd_draft.md").write_text(content)

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "ideas_dir": str(ideas_dir),
                "hooks_url": "http://x/hooks",
                "hooks_token": "t",
            }
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                with patch("ui.server.asyncio.sleep", AsyncMock(side_effect=sleep_then_sentinel)):
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
