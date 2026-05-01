"""Tests for POST /api/ideas/{id}/adversarial-check endpoint."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestApiIdeasAdversarialCheck:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()
        self.workspace = tmp_path / "workspace"
        self.repo_root = tmp_path / "repo"

    def _mock_config(self):
        return {
            "ideas_dir": str(self.ideas_dir),
            "hooks_url": "http://localhost:18789/hooks/agent",
            "hooks_token": "test-token",
            "roadmap_converter_workspace": str(self.workspace),
            "autodev_repo_path": str(self.repo_root),
        }

    def _write_session(self, idea_id, prd_content="## PRD\nContent.", with_roadmap=False):
        idea_dir = self.ideas_dir / idea_id
        idea_dir.mkdir(parents=True, exist_ok=True)
        session = {
            "messages": [
                {"role": "user", "content": "help me", "turn": 1},
                {"role": "assistant", "content": "sure", "turn": 1},
            ],
            "prd_content": prd_content,
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }
        (idea_dir / "session.json").write_text(json.dumps(session))
        if with_roadmap:
            (idea_dir / "roadmap_draft.md").write_text("# Roadmap\n- [ ] `CORE-E1` | LOW | First")
        return idea_dir

    def _make_mock_aiohttp(self):
        mock_response = MagicMock()
        mock_response.status = 200  # must be int so resp.status >= 400 check works
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_cls = MagicMock(return_value=mock_session)
        return mock_cls, mock_session

    def test_returns_404_when_idea_not_found(self):
        client = load_server()
        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server._inject_converter_skill"):
            r = client.post("/api/ideas/nonexistent/adversarial-check")
        assert r.status_code == 404

    def test_returns_400_when_roadmap_draft_missing(self):
        client = load_server()
        self._write_session("1", with_roadmap=False)
        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server._inject_converter_skill"):
            r = client.post("/api/ideas/1/adversarial-check")
        assert r.status_code == 400
        assert "roadmap" in r.json()["detail"].lower()

    def test_inject_called_with_adversarial_review_only(self):
        """_inject_converter_skill called with 'adversarial-review' only — not roadmap-generation."""
        client = load_server()
        idea_dir = self._write_session("2", with_roadmap=True)
        mock_cls, _ = self._make_mock_aiohttp()
        report_text = (
            "# Adversarial Review\n"
            "## Phase Risk Assessment\n"
            "| Phase ID | Confidence | Failure Hypothesis | Mitigation |\n"
            "|----------|-----------|-------------------|------------|\n"
            "| CORE-E1 | 85 | Low risk | None |\n"
            "## Overall Pipeline Confidence\n"
            "85/100 — Pipeline looks solid."
        )

        def write_sentinel(*args, **kwargs):
            (idea_dir / "adversarial_report.md").write_text(report_text)
            (idea_dir / "adversarial_report.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill") as mock_inject, \
             patch("ui.server.asyncio.create_task"), \
             patch("ui.server.ADVERSARIAL_CHECK_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", AsyncMock(side_effect=write_sentinel)):
            r = client.post("/api/ideas/2/adversarial-check")

        assert r.status_code == 200
        skill_names = [c[0][0] for c in mock_inject.call_args_list]
        assert skill_names == ["adversarial-review"]
        assert "roadmap-generation" not in skill_names
        assert "alignment-check" not in skill_names

    def test_session_key_pattern(self):
        """Session key matches ideas:{id}:adversarial-{ts}."""
        client = load_server()
        idea_dir = self._write_session("3", with_roadmap=True)
        mock_cls, mock_session = self._make_mock_aiohttp()
        report_text = (
            "# Adversarial Review\n"
            "## Overall Pipeline Confidence\n"
            "75/100 — Moderate confidence."
        )

        def write_sentinel(*args, **kwargs):
            (idea_dir / "adversarial_report.md").write_text(report_text)
            (idea_dir / "adversarial_report.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.asyncio.create_task"), \
             patch("ui.server.ADVERSARIAL_CHECK_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", AsyncMock(side_effect=write_sentinel)):
            r = client.post("/api/ideas/3/adversarial-check")

        assert r.status_code == 200
        call_args = mock_session.post.call_args
        payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
        assert payload["sessionKey"].startswith("ideas:3:adversarial-")
        assert payload["agentId"] == "roadmap-converter"

    def test_returns_408_on_timeout(self):
        client = load_server()
        self._write_session("4", with_roadmap=True)
        mock_cls, _ = self._make_mock_aiohttp()
        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.ADVERSARIAL_CHECK_TIMEOUT", 1), \
             patch("ui.server.ADVERSARIAL_CHECK_POLL_INTERVAL", 0.1):
            r = client.post("/api/ideas/4/adversarial-check")
        assert r.status_code == 408
        assert "timed out" in r.json()["detail"].lower()

    def test_returns_200_with_adversarial_report(self):
        client = load_server()
        idea_dir = self._write_session("5", with_roadmap=True)
        mock_cls, _ = self._make_mock_aiohttp()
        report_text = (
            "# Adversarial Review\n"
            "## Phase Risk Assessment\n"
            "| Phase ID | Confidence | Failure Hypothesis | Mitigation |\n"
            "|----------|-----------|-------------------|------------|\n"
            "| CORE-E1 | 60 | Context window overflow | Split phase |\n"
            "## Overall Pipeline Confidence\n"
            "60/100 — Meaningful risk."
        )

        def write_sentinel(*args, **kwargs):
            (idea_dir / "adversarial_report.md").write_text(report_text)
            (idea_dir / "adversarial_report.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.asyncio.create_task"), \
             patch("ui.server.ADVERSARIAL_CHECK_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", AsyncMock(side_effect=write_sentinel)):
            r = client.post("/api/ideas/5/adversarial-check")

        assert r.status_code == 200
        body = r.json()
        assert "adversarial_report" in body
        assert report_text in body["adversarial_report"]

    def test_does_not_modify_roadmap_draft(self):
        """adversarial-check must not write to roadmap_draft.md."""
        client = load_server()
        idea_dir = self._write_session("6", with_roadmap=True)
        original_roadmap = (idea_dir / "roadmap_draft.md").read_text()
        mock_cls, _ = self._make_mock_aiohttp()
        report_text = (
            "# Adversarial Review\n"
            "## Overall Pipeline Confidence\n"
            "70/100 — Acceptable."
        )

        def write_sentinel(*args, **kwargs):
            (idea_dir / "adversarial_report.md").write_text(report_text)
            (idea_dir / "adversarial_report.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.asyncio.create_task"), \
             patch("ui.server.ADVERSARIAL_CHECK_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", AsyncMock(side_effect=write_sentinel)):
            r = client.post("/api/ideas/6/adversarial-check")

        assert r.status_code == 200
        # roadmap_draft.md must be unchanged
        assert (idea_dir / "roadmap_draft.md").read_text() == original_roadmap

    def test_adversarial_report_stored_in_session_json(self):
        client = load_server()
        idea_dir = self._write_session("7", with_roadmap=True)
        mock_cls, _ = self._make_mock_aiohttp()
        report_text = (
            "# Adversarial Review\n"
            "## Overall Pipeline Confidence\n"
            "80/100 — Good."
        )

        def write_sentinel(*args, **kwargs):
            (idea_dir / "adversarial_report.md").write_text(report_text)
            (idea_dir / "adversarial_report.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.asyncio.create_task"), \
             patch("ui.server.ADVERSARIAL_CHECK_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", AsyncMock(side_effect=write_sentinel)):
            r = client.post("/api/ideas/7/adversarial-check")

        assert r.status_code == 200
        session = json.loads((idea_dir / "session.json").read_text())
        assert session.get("adversarial_report") == report_text

    def test_prd_agent_notified_via_create_task(self):
        """Adversarial completion fires asyncio.create_task with _notify_prd_agent (not pending_system_events)."""
        client = load_server()
        idea_dir = self._write_session("8", with_roadmap=True)
        mock_cls, _ = self._make_mock_aiohttp()
        report_text = (
            "# Adversarial Review\n"
            "## Overall Pipeline Confidence\n"
            "90/100 — High confidence."
        )

        def write_sentinel(*args, **kwargs):
            (idea_dir / "adversarial_report.md").write_text(report_text)
            (idea_dir / "adversarial_report.done").write_text("")

        mock_create_task = MagicMock()
        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.asyncio.create_task", mock_create_task), \
             patch("ui.server.ADVERSARIAL_CHECK_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", AsyncMock(side_effect=write_sentinel)):
            r = client.post("/api/ideas/8/adversarial-check")

        assert r.status_code == 200
        import json
        session = json.loads((idea_dir / "session.json").read_text())
        # pending_system_events no longer used — notification is fire-and-forget via create_task
        assert session.get("pending_system_events", []) == []
        # create_task was called once (the PRD agent notification)
        assert mock_create_task.call_count == 1
