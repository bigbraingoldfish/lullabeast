"""Tests for updated POST /api/ideas/{id}/convert — agentId and skill injection."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock, call


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestApiIdeasConvertUpdated:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()
        self.workspace = tmp_path / "workspace"
        self.prompt_file = tmp_path / "conversion_prompt.txt"
        self.prompt_file.write_text("Convert this PRD to a roadmap.")

    def _mock_config(self):
        return {
            "ideas_dir": str(self.ideas_dir),
            "hooks_url": "http://localhost:18789/hooks/agent",
            "hooks_token": "test-token",
            "conversion_prompt_path": str(self.prompt_file),
            "roadmap_converter_workspace": str(self.workspace),
            "autodev_repo_path": "/tmp/repo",
        }

    def _write_session(self, idea_id, prd_content=""):
        idea_dir = self.ideas_dir / idea_id
        idea_dir.mkdir(parents=True, exist_ok=True)
        session = {
            "messages": [],
            "prd_content": prd_content,
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }
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

    def test_agent_id_is_roadmap_converter_not_prd_creator(self):
        """Webhook payload agentId must be 'roadmap-converter', not 'prd-creator'."""
        client = load_server()
        idea_dir = self._write_session("1", prd_content="## Problem\nContent.")
        roadmap_text = "# Roadmap\n- [ ] `CORE-E1` | LOW | First"
        mock_cls, mock_session = self._make_mock_aiohttp()

        async def write_sentinel(*args, **kwargs):
            (idea_dir / "roadmap_draft.md").write_text(roadmap_text)
            (idea_dir / "roadmap_draft.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.CONVERT_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", side_effect=write_sentinel):
            r = client.post("/api/ideas/1/convert")

        assert r.status_code == 200
        call_args = mock_session.post.call_args
        payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
        assert payload["agentId"] == "roadmap-converter"
        assert payload["agentId"] != "prd-creator"

    def test_inject_converter_skill_called_with_roadmap_generation(self):
        """_inject_converter_skill('roadmap-generation', config) called before POST."""
        client = load_server()
        idea_dir = self._write_session("2", prd_content="## Problem\nContent.")
        roadmap_text = "# Roadmap\n- [ ] `CORE-E1` | LOW | First"
        mock_cls, _ = self._make_mock_aiohttp()

        async def write_sentinel(*args, **kwargs):
            (idea_dir / "roadmap_draft.md").write_text(roadmap_text)
            (idea_dir / "roadmap_draft.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill") as mock_inject, \
             patch("ui.server.CONVERT_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", side_effect=write_sentinel):
            r = client.post("/api/ideas/2/convert")

        assert r.status_code == 200
        mock_inject.assert_called_once()
        args = mock_inject.call_args[0]
        assert args[0] == "roadmap-generation"

    def test_inject_called_before_webhook_post(self):
        """Skill injection must happen before the webhook POST."""
        client = load_server()
        idea_dir = self._write_session("3", prd_content="## Problem\nContent.")
        roadmap_text = "# Roadmap"
        mock_cls, mock_session = self._make_mock_aiohttp()
        call_order = []

        def record_inject(*args, **kwargs):
            call_order.append("inject")

        async def record_post(*args, **kwargs):
            call_order.append("post")
            return MagicMock()

        mock_session.post = record_post

        async def write_sentinel(*args, **kwargs):
            (idea_dir / "roadmap_draft.md").write_text(roadmap_text)
            (idea_dir / "roadmap_draft.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill", side_effect=record_inject), \
             patch("ui.server.CONVERT_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", side_effect=write_sentinel):
            r = client.post("/api/ideas/3/convert")

        assert r.status_code == 200
        assert call_order.index("inject") < call_order.index("post")

    def test_returns_404_when_idea_not_found(self):
        client = load_server()
        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server._inject_converter_skill"):
            r = client.post("/api/ideas/nonexistent/convert")
        assert r.status_code == 404

    def test_returns_422_when_no_prd_content(self):
        client = load_server()
        self._write_session("4", prd_content="")
        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server._inject_converter_skill"):
            r = client.post("/api/ideas/4/convert")
        assert r.status_code == 422

    def test_returns_503_when_conversion_prompt_missing(self):
        client = load_server()
        self._write_session("5", prd_content="## Problem\nContent.")
        config = self._mock_config()
        config["conversion_prompt_path"] = "/nonexistent/path.txt"
        with patch("ui.server.load_config", return_value=config), \
             patch("ui.server._inject_converter_skill"):
            r = client.post("/api/ideas/5/convert")
        assert r.status_code == 503

    def test_returns_408_on_timeout(self):
        client = load_server()
        self._write_session("6", prd_content="## Problem\nContent.")
        mock_cls, _ = self._make_mock_aiohttp()
        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.CONVERT_TIMEOUT", 1), \
             patch("ui.server.CONVERT_POLL_INTERVAL", 0.1):
            r = client.post("/api/ideas/6/convert")
        assert r.status_code == 408

    def test_session_key_contains_idea_id(self):
        """Session key format: ideas:{id}:convert-{ts}."""
        client = load_server()
        idea_dir = self._write_session("7", prd_content="## Problem\nContent.")
        mock_cls, mock_session = self._make_mock_aiohttp()

        async def write_sentinel(*args, **kwargs):
            (idea_dir / "roadmap_draft.md").write_text("# Roadmap")
            (idea_dir / "roadmap_draft.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.CONVERT_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", side_effect=write_sentinel):
            r = client.post("/api/ideas/7/convert")

        assert r.status_code == 200
        call_args = mock_session.post.call_args
        payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
        assert payload["sessionKey"].startswith("ideas:7:convert-")
