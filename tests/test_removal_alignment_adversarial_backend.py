"""Absence tests: alignment and adversarial check endpoints must NOT exist.

TDD red-state: these tests FAIL against the current codebase (endpoints exist).
They PASS after the removal is implemented.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock


def _client():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def _mock_config(ideas_dir):
    return {
        "ideas_dir": str(ideas_dir),
        "hooks_url": "http://localhost:18789/hooks/agent",
        "hooks_token": "test-token",
        "roadmap_converter_workspace": str(ideas_dir / "workspace"),
        "autodev_repo_path": str(ideas_dir / "repo"),
    }


def _write_idea(ideas_dir, idea_id):
    idea_dir = ideas_dir / idea_id
    idea_dir.mkdir(parents=True, exist_ok=True)
    session = {
        "messages": [
            {"role": "user", "content": "help", "turn": 1},
            {"role": "assistant", "content": "ok", "turn": 1},
        ],
        "prd_content": "## PRD\nContent.",
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }
    (idea_dir / "session.json").write_text(json.dumps(session))
    (idea_dir / "roadmap_draft.md").write_text("# Roadmap\n- [ ] `CORE-E1` | LOW | First")
    return idea_dir


def _mock_aiohttp_200():
    mock_response = MagicMock()
    mock_response.status = 200
    mock_session = MagicMock()
    mock_session.post = AsyncMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=mock_session)


class TestAlignmentEndpointRemoved:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()

    def test_alignment_check_endpoint_removed(self):
        """POST /api/ideas/{id}/alignment-check must return 404 (route absent).

        A valid idea with a roadmap is set up so that any remaining route handling
        that would inspect the idea dir could proceed. The only correct 404 here
        is FastAPI's route-not-found, not an idea-missing 404.
        """
        _write_idea(self.ideas_dir, "a1")
        client = _client()
        with patch("ui.server.load_config", return_value=_mock_config(self.ideas_dir)):
            r = client.post("/api/ideas/a1/alignment-check")
        assert r.status_code == 404, (
            f"Expected 404 (route removed), got {r.status_code}. "
            "The alignment-check endpoint has not been removed yet."
        )

    def test_adversarial_check_endpoint_removed(self):
        """POST /api/ideas/{id}/adversarial-check must return 404 (route absent).

        A valid idea with a roadmap is set up so that any remaining route handling
        that would inspect the idea dir could proceed. The only correct 404 here
        is FastAPI's route-not-found, not an idea-missing 404.
        """
        _write_idea(self.ideas_dir, "a2")
        client = _client()
        with patch("ui.server.load_config", return_value=_mock_config(self.ideas_dir)):
            r = client.post("/api/ideas/a2/adversarial-check")
        assert r.status_code == 404, (
            f"Expected 404 (route removed), got {r.status_code}. "
            "The adversarial-check endpoint has not been removed yet."
        )


class TestSessionResponseFieldsRemoved:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()

    def test_alignment_report_not_in_session_response(self):
        """GET /api/ideas/{id}/session must not include alignment_report.

        Red state: session.json contains alignment_report and endpoint passes it through.
        Green state: endpoint filters it out before returning.
        """
        idea_dir = self.ideas_dir / "s1"
        idea_dir.mkdir()
        session = {
            "messages": [],
            "prd_content": "## PRD",
            "alignment_report": "# Alignment Report\nSome old report.",
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }
        (idea_dir / "session.json").write_text(json.dumps(session))
        client = _client()
        with patch("ui.server.load_config", return_value=_mock_config(self.ideas_dir)):
            r = client.get("/api/ideas/s1/session")
        assert r.status_code == 200
        assert "alignment_report" not in r.json(), (
            "alignment_report still present in GET /api/ideas/{id}/session response. "
            "The session filter has not been applied yet."
        )

    def test_adversarial_report_not_in_session_response(self):
        """GET /api/ideas/{id}/session must not include adversarial_report.

        Red state: session.json contains adversarial_report and endpoint passes it through.
        Green state: endpoint filters it out before returning.
        """
        idea_dir = self.ideas_dir / "s2"
        idea_dir.mkdir()
        session = {
            "messages": [],
            "prd_content": "## PRD",
            "adversarial_report": "# Adversarial Report\nSome old report.",
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }
        (idea_dir / "session.json").write_text(json.dumps(session))
        client = _client()
        with patch("ui.server.load_config", return_value=_mock_config(self.ideas_dir)):
            r = client.get("/api/ideas/s2/session")
        assert r.status_code == 200
        assert "adversarial_report" not in r.json(), (
            "adversarial_report still present in GET /api/ideas/{id}/session response. "
            "The session filter has not been applied yet."
        )
