"""Tests for POST /api/ideas/{idea_id}/clarity-check"""
import json
import pytest
import sys
import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

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
def idea_with_prd(ideas_dir):
    idea_id = "test-idea-456"
    idea_path = ideas_dir / idea_id
    idea_path.mkdir(parents=True, exist_ok=True)
    session = {
        "messages": [],
        "prd_content": (
            "# My Idea\n\n## Problem Statement\nGap.\n\n"
            "## Goals & Success Metrics\nGoal.\n\n"
            "## Functional Requirements\n1. A\n"
        ),
        "created": "2024-01-01T00:00:00Z",
        "updated": "2024-01-01T00:00:00Z",
    }
    (idea_path / "session.json").write_text(json.dumps(session))
    return idea_id


class TestClarityCheckSuccess:
    """pass_criteria: POST /api/ideas/{id}/clarity-check sends a webhook POST
    to hooks_url and polls for clarity_result.done; returns the JSON content
    of clarity_result.json on success."""

    def test_returns_clarity_result_json_on_success(self, ideas_dir, idea_with_prd):
        result_json = {
            "pass": True,
            "missing_sections": [],
            "issues": [],
        }
        result_path = ideas_dir / idea_with_prd / "clarity_result.json"
        done_path = ideas_dir / idea_with_prd / "clarity_result.done"

        async def mock_post(*args, **kwargs):
            result_path.write_text(json.dumps(result_json))
            done_path.touch()
            return MagicMock(status=200, json=AsyncMock(return_value={}))

        with patch("ui.server.load_config") as mock_config, \
             patch("ui.server.asyncio.sleep", new_callable=AsyncMock):

            mock_config.return_value = {
                "ideas_dir": str(ideas_dir),
                "hooks_url": "http://localhost:18789/hooks/agent",
                "hooks_token": "secret",
            }

            with patch("aiohttp.ClientSession.post", new=mock_post):
                response = client.post(f"/api/ideas/{idea_with_prd}/clarity-check")

        assert response.status_code == 200
        data = response.json()
        assert data["pass"] is True
        assert data["missing_sections"] == []
        assert data["issues"] == []

    def test_returns_pass_false_with_missing_sections(self, ideas_dir, idea_with_prd):
        result_json = {
            "pass": False,
            "missing_sections": ["## Non-Functional Requirements"],
            "issues": ["Problem statement is vague"],
        }
        result_path = ideas_dir / idea_with_prd / "clarity_result.json"
        done_path = ideas_dir / idea_with_prd / "clarity_result.done"

        async def mock_post(*args, **kwargs):
            result_path.write_text(json.dumps(result_json))
            done_path.touch()
            return MagicMock(status=200)

        with patch("ui.server.load_config") as mock_config, \
             patch("ui.server.asyncio.sleep", new_callable=AsyncMock):

            mock_config.return_value = {
                "ideas_dir": str(ideas_dir),
                "hooks_url": "http://localhost:18789/hooks/agent",
                "hooks_token": "secret",
            }

            with patch("aiohttp.ClientSession.post", new=mock_post):
                response = client.post(f"/api/ideas/{idea_with_prd}/clarity-check")

        assert response.status_code == 200
        data = response.json()
        assert data["pass"] is False
        assert "## Non-Functional Requirements" in data["missing_sections"]
        assert "Problem statement is vague" in data["issues"]


class TestClarityCheckTimeout:
    """pass_criteria: POST /api/ideas/{id}/clarity-check returns 504 when
    clarity_result.done is not created within 60 seconds."""

    def test_returns_504_on_timeout(self, ideas_dir, idea_with_prd):
        """Returns 504 when clarity_result.done is not created within 60 seconds.

        Patches done_path.exists() to always return False via a targeted mock so
        other Path.exists() calls (session_path, etc.) are unaffected.
        """
        async def mock_post(*args, **kwargs):
            return MagicMock(status=200)

        async def fast_sleep(duration):
            # No-op: returns immediately so the deadline-driven while loop
            # runs without real delay and triggers the 60s timeout path.
            return

        done_path_real = ideas_dir / idea_with_prd / "clarity_result.done"
        done_path_mock = MagicMock(spec=Path)
        done_path_mock.exists = lambda: False
        done_path_mock.__truediv__ = done_path_real.__truediv__

        # Session path is real — must exist for the endpoint to read prd_content
        session_path_real = ideas_dir / idea_with_prd / "session.json"

        with patch("ui.server.load_config") as mock_config, \
             patch("ui.server.asyncio.sleep", new=fast_sleep):

            mock_config.return_value = {
                "ideas_dir": str(ideas_dir),
                "hooks_url": "http://localhost:18789/hooks/agent",
                "hooks_token": "secret",
            }

            # Intercept Path() calls — return done_path_mock only for the done_path,
            # real paths for everything else
            original_path_new = Path.__new__

            def fake_path_new(cls, *args, **kwargs):
                if args and "clarity_result.done" in str(args[0]):
                    return done_path_mock
                return original_path_new(cls, *args, **kwargs)

            with patch.object(Path, "__new__", new=fake_path_new):
                with patch("aiohttp.ClientSession.post", new=mock_post):
                    response = client.post(f"/api/ideas/{idea_with_prd}/clarity-check")

        assert response.status_code == 504
        assert "timed out" in response.json()["detail"]


class TestClarityCheckNoPrdContent:
    def test_returns_422_when_no_prd_content(self, ideas_dir):
        idea_id = "empty-idea"
        idea_path = ideas_dir / idea_id
        idea_path.mkdir(parents=True, exist_ok=True)
        session = {"messages": [], "prd_content": "", "created": "2024-01-01T00:00:00Z", "updated": "2024-01-01T00:00:00Z"}
        (idea_path / "session.json").write_text(json.dumps(session))

        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {"ideas_dir": str(ideas_dir)}
            response = client.post(f"/api/ideas/{idea_id}/clarity-check")

        assert response.status_code == 422
        assert "No prd_content" in response.json()["detail"]

    def test_returns_404_when_session_not_found(self, ideas_dir):
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {"ideas_dir": str(ideas_dir)}
            response = client.post("/api/ideas/nonexistent/clarity-check")
        assert response.status_code == 404
