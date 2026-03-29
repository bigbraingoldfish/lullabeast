"""Tests for POST /api/ideas/{id}/alignment-check endpoint."""
import json
import time
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestApiIdeasAlignmentCheck:

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
            r = client.post("/api/ideas/nonexistent/alignment-check")
        assert r.status_code == 404

    def test_returns_400_when_roadmap_draft_missing(self):
        client = load_server()
        self._write_session("1", with_roadmap=False)
        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server._inject_converter_skill"):
            r = client.post("/api/ideas/1/alignment-check")
        assert r.status_code == 400
        assert "roadmap" in r.json()["detail"].lower()

    def test_inject_called_with_both_skills(self):
        """_inject_converter_skill called with 'roadmap-generation' and 'alignment-check'."""
        client = load_server()
        idea_dir = self._write_session("2", with_roadmap=True)
        mock_cls, _ = self._make_mock_aiohttp()
        report_text = "# Alignment Report\n## Material Gaps Addressed\n## Overall Assessment\nAll good."

        async def write_sentinel(*args, **kwargs):
            (idea_dir / "alignment_report.md").write_text(report_text)
            (idea_dir / "alignment_report.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill") as mock_inject, \
             patch("ui.server.asyncio.create_task"), \
             patch("ui.server.ALIGNMENT_CHECK_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", side_effect=write_sentinel):
            r = client.post("/api/ideas/2/alignment-check")

        assert r.status_code == 200
        skill_names = [c[0][0] for c in mock_inject.call_args_list]
        assert "roadmap-generation" in skill_names
        assert "alignment-check" in skill_names

    def test_session_key_pattern(self):
        """Session key matches ideas:{id}:alignment-{ts}."""
        client = load_server()
        idea_dir = self._write_session("3", with_roadmap=True)
        mock_cls, mock_session = self._make_mock_aiohttp()
        report_text = "# Alignment Report\n## Overall Assessment\nGood."

        async def write_sentinel(*args, **kwargs):
            (idea_dir / "alignment_report.md").write_text(report_text)
            (idea_dir / "alignment_report.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.asyncio.create_task"), \
             patch("ui.server.ALIGNMENT_CHECK_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", side_effect=write_sentinel):
            r = client.post("/api/ideas/3/alignment-check")

        assert r.status_code == 200
        call_args = mock_session.post.call_args
        payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
        assert payload["sessionKey"].startswith("ideas:3:alignment-")
        assert payload["agentId"] == "roadmap-converter"

    def test_returns_408_on_timeout(self):
        client = load_server()
        self._write_session("4", with_roadmap=True)
        mock_cls, _ = self._make_mock_aiohttp()
        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.ALIGNMENT_CHECK_TIMEOUT", 1), \
             patch("ui.server.ALIGNMENT_CHECK_POLL_INTERVAL", 0.1):
            r = client.post("/api/ideas/4/alignment-check")
        assert r.status_code == 408
        assert "timed out" in r.json()["detail"].lower()

    def test_returns_200_with_alignment_report(self):
        client = load_server()
        idea_dir = self._write_session("5", with_roadmap=True)
        mock_cls, _ = self._make_mock_aiohttp()
        report_text = "# Alignment Report\n## Overall Assessment\nAll phases covered."

        async def write_sentinel(*args, **kwargs):
            (idea_dir / "alignment_report.md").write_text(report_text)
            (idea_dir / "alignment_report.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.asyncio.create_task"), \
             patch("ui.server.ALIGNMENT_CHECK_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", side_effect=write_sentinel):
            r = client.post("/api/ideas/5/alignment-check")

        assert r.status_code == 200
        body = r.json()
        assert "alignment_report" in body
        assert report_text in body["alignment_report"]
        assert "roadmap_updated" in body
        assert "roadmap_content" in body

    def test_roadmap_updated_true_when_mtime_changes(self):
        """roadmap_updated=True when roadmap_draft.md mtime changes during check."""
        client = load_server()
        idea_dir = self._write_session("6", with_roadmap=True)
        mock_cls, _ = self._make_mock_aiohttp()
        report_text = "# Alignment Report\n## Material Gaps Addressed\n- Gap 1: Add phase.\n## Overall Assessment\nFixed."
        new_roadmap = "# Roadmap\n- [ ] `CORE-E1` | LOW | First\n- [ ] `CORE-E2` | LOW | Added"

        async def write_sentinel(*args, **kwargs):
            # Simulate agent updating roadmap then writing sentinels
            time.sleep(0.01)  # ensure mtime changes
            (idea_dir / "roadmap_draft.md").write_text(new_roadmap)
            (idea_dir / "alignment_report.md").write_text(report_text)
            (idea_dir / "alignment_report.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.asyncio.create_task"), \
             patch("ui.server.ALIGNMENT_CHECK_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", side_effect=write_sentinel):
            r = client.post("/api/ideas/6/alignment-check")

        assert r.status_code == 200
        body = r.json()
        assert body["roadmap_updated"] is True
        assert body["roadmap_content"] == new_roadmap

    def test_roadmap_updated_false_when_mtime_unchanged(self):
        """roadmap_updated=False when roadmap_draft.md is not touched."""
        client = load_server()
        idea_dir = self._write_session("7", with_roadmap=True)
        mock_cls, _ = self._make_mock_aiohttp()
        report_text = "# Alignment Report\n## Overall Assessment\nNo gaps found."

        async def write_sentinel(*args, **kwargs):
            # Only write report + sentinel, leave roadmap_draft.md untouched
            (idea_dir / "alignment_report.md").write_text(report_text)
            (idea_dir / "alignment_report.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.asyncio.create_task"), \
             patch("ui.server.ALIGNMENT_CHECK_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", side_effect=write_sentinel):
            r = client.post("/api/ideas/7/alignment-check")

        assert r.status_code == 200
        body = r.json()
        assert body["roadmap_updated"] is False
        assert body["roadmap_content"] is None

    def test_alignment_report_stored_in_session_json(self):
        client = load_server()
        idea_dir = self._write_session("8", with_roadmap=True)
        mock_cls, _ = self._make_mock_aiohttp()
        report_text = "# Alignment Report\n## Overall Assessment\nComplete."

        async def write_sentinel(*args, **kwargs):
            (idea_dir / "alignment_report.md").write_text(report_text)
            (idea_dir / "alignment_report.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.asyncio.create_task"), \
             patch("ui.server.ALIGNMENT_CHECK_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", side_effect=write_sentinel):
            r = client.post("/api/ideas/8/alignment-check")

        assert r.status_code == 200
        session = json.loads((idea_dir / "session.json").read_text())
        assert session.get("alignment_report") == report_text

    def test_notification_stored_as_pending_system_event(self):
        """Alignment result is stored in session.json pending_system_events (not webhook)."""
        client = load_server()
        idea_dir = self._write_session("9", with_roadmap=True)
        mock_cls, _ = self._make_mock_aiohttp()
        report_text = "# Alignment Report\n## Overall Assessment\nDone."

        async def write_sentinel(*args, **kwargs):
            (idea_dir / "alignment_report.md").write_text(report_text)
            (idea_dir / "alignment_report.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.ALIGNMENT_CHECK_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", side_effect=write_sentinel):
            r = client.post("/api/ideas/9/alignment-check")

        assert r.status_code == 200
        import json
        session = json.loads((idea_dir / "session.json").read_text())
        events = session.get("pending_system_events", [])
        assert len(events) == 1, f"Expected 1 pending system event, got {events}"
        assert "[SYSTEM]" in events[0]
        assert "Alignment check complete" in events[0]
