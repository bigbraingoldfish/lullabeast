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
    """pass_criteria: POST /api/ideas/{id}/clarity-check returns 504 when the
    idle-detection poll reports a non-success verdict (stall or infra backstop).

    The endpoint now polls via the shared ``_poll_sentinel_with_idle_detect``
    (same machinery as the chat send) instead of a 60 s ``datetime`` deadline
    loop, so the timeout contract is exercised by patching that helper to return
    a failing ``PollResult`` — no Path-interception gymnastics required.
    """

    def test_returns_504_on_timeout(self, ideas_dir, idea_with_prd):
        from autodev.pipeline.sentinel_poller import PollResult

        async def mock_post(*args, **kwargs):
            return MagicMock(status=200)

        with patch("ui.server.load_config") as mock_config, \
             patch(
                 "ui.server._poll_sentinel_with_idle_detect",
                 AsyncMock(return_value=PollResult(False, "timeout")),
             ):

            mock_config.return_value = {
                "ideas_dir": str(ideas_dir),
                "hooks_url": "http://localhost:18789/hooks/agent",
                "hooks_token": "secret",
            }

            with patch("aiohttp.ClientSession.post", new=mock_post):
                response = client.post(f"/api/ideas/{idea_with_prd}/clarity-check")

        assert response.status_code == 504
        assert "timed out" in response.json()["detail"]

    def test_returns_504_on_stall(self, ideas_dir, idea_with_prd):
        """A mid-run stall (agent active then silent) also yields the 504 contract."""
        from autodev.pipeline.sentinel_poller import PollResult

        async def mock_post(*args, **kwargs):
            return MagicMock(status=200)

        with patch("ui.server.load_config") as mock_config, \
             patch(
                 "ui.server._poll_sentinel_with_idle_detect",
                 AsyncMock(return_value=PollResult(False, "stalled")),
             ):
            mock_config.return_value = {
                "ideas_dir": str(ideas_dir),
                "hooks_url": "http://localhost:18789/hooks/agent",
                "hooks_token": "secret",
            }
            with patch("aiohttp.ClientSession.post", new=mock_post):
                response = client.post(f"/api/ideas/{idea_with_prd}/clarity-check")

        assert response.status_code == 504


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
