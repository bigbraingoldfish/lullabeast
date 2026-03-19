"""Tests for POST /api/ideas/{id}/convert endpoint."""
import json
import asyncio
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestApiIdeasConvert:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()
        self.prompt_file = tmp_path / "conversion_prompt.txt"
        self.prompt_file.write_text("Convert this PRD to a roadmap.")

    def _mock_config(self):
        return {
            "ideas_dir": str(self.ideas_dir),
            "hooks_url": "http://localhost:18789/hooks/agent",
            "hooks_token": "test-token",
            "conversion_prompt_path": str(self.prompt_file),
        }

    def _write_session(self, idea_id, prd_content="", roadmap_content=None):
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
        return idea_dir

    def _make_mock_aiohttp(self):
        mock_response = MagicMock()
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_cls = MagicMock(return_value=mock_session)
        return mock_cls, mock_session

    def test_returns_404_when_idea_not_found(self):
        client = load_server()
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.post("/api/ideas/nonexistent/convert")
        assert r.status_code == 404

    def test_returns_422_when_no_prd_content(self):
        client = load_server()
        self._write_session("1", prd_content="")
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.post("/api/ideas/1/convert")
        assert r.status_code == 422

    def test_returns_503_when_conversion_prompt_missing(self):
        client = load_server()
        self._write_session("2", prd_content="## Problem Statement\nSome content.")
        config = self._mock_config()
        config["conversion_prompt_path"] = "/nonexistent/path.txt"
        with patch("ui.server.load_config", return_value=config):
            r = client.post("/api/ideas/2/convert")
        assert r.status_code == 503

    def test_returns_408_on_timeout(self):
        """Returns 408 when roadmap_draft.done is never written within timeout."""
        client = load_server()
        self._write_session("3", prd_content="## Problem Statement\nContent.")
        mock_cls, _ = self._make_mock_aiohttp()

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server.CONVERT_TIMEOUT", 1), \
             patch("ui.server.CONVERT_POLL_INTERVAL", 0.1):
            r = client.post("/api/ideas/3/convert")
        assert r.status_code == 408

    def test_returns_200_with_roadmap_content_on_success(self):
        """Returns 200 with roadmap_content when sentinel is found."""
        client = load_server()
        idea_dir = self._write_session("4", prd_content="## Problem Statement\nContent.")
        roadmap_text = "# Project Roadmap\n\n- [ ] `phase-1` | LOW | First phase"
        mock_cls, _ = self._make_mock_aiohttp()

        def write_sentinel(*args, **kwargs):
            (idea_dir / "roadmap_draft.md").write_text(roadmap_text)
            (idea_dir / "roadmap_draft.done").write_text("")
            return asyncio.sleep(0)

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server.CONVERT_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", side_effect=write_sentinel):
            r = client.post("/api/ideas/4/convert")

        assert r.status_code == 200
        body = r.json()
        assert "roadmap_content" in body
        assert roadmap_text in body["roadmap_content"]

    def test_stores_roadmap_content_in_session_json(self):
        """roadmap_content is atomically written to session.json after conversion."""
        client = load_server()
        idea_dir = self._write_session("5", prd_content="## Problem Statement\nContent.")
        roadmap_text = "# My Roadmap\n\n- [ ] `phase-1` | LOW | Step one"
        mock_cls, _ = self._make_mock_aiohttp()

        def write_sentinel(*args, **kwargs):
            (idea_dir / "roadmap_draft.md").write_text(roadmap_text)
            (idea_dir / "roadmap_draft.done").write_text("")
            return asyncio.sleep(0)

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server.CONVERT_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", side_effect=write_sentinel):
            r = client.post("/api/ideas/5/convert")

        assert r.status_code == 200
        session = json.loads((idea_dir / "session.json").read_text())
        assert session.get("roadmap_content") == roadmap_text
