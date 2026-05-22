"""P0 Stage B9 + B10: /api/ideas/{id}/convert polls both sentinels.

After P0, the converter writes both ``roadmap_draft.md`` and
``verification_draft.md`` in the same session. The endpoint:

- Bumps ``CONVERT_TIMEOUT`` from 300 s to 480 s to accommodate dual-doc generation.
- Unlinks BOTH stale sentinels before polling.
- Polls until BOTH sentinels are fresh, then reads both files.
- Returns ``{roadmap_content, verification_content}`` and persists both into session.json.
"""
import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestConvertTimeoutConstant:
    """Module-level invariant: CONVERT_TIMEOUT bumped to 480 in Stage B9."""

    def test_convert_timeout_is_480_seconds(self):
        from ui import server as srv
        assert srv.CONVERT_TIMEOUT == 480, (
            "CONVERT_TIMEOUT must be 480 s after P0 Stage B9 — generating both "
            "roadmap and verification.md needs more headroom than the prior 300 s."
        )


class TestConvertBothSentinels:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()
        self.prompt_file = tmp_path / "conversion_prompt.txt"
        self.prompt_file.write_text("Convert this PRD to a roadmap.")

    def _mock_config(self):
        repo = Path(__file__).resolve().parents[1]
        return {
            "ideas_dir": str(self.ideas_dir),
            "hooks_url": "http://localhost:18789/hooks/agent",
            "hooks_token": "test-token",
            "conversion_prompt_path": str(self.prompt_file),
            "autodev_repo_path": str(repo),
        }

    def _write_session(self, idea_id, prd_content):
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
        mock_response.status = 200
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        return MagicMock(return_value=mock_session), mock_session

    def test_returns_both_contents_when_both_sentinels_fresh(self):
        client = load_server()
        idea_dir = self._write_session("idea-a", prd_content="## Problem\nThing.")
        roadmap_text = "# Project Roadmap\n\n- [ ] `CORE-E1` | LOW | First phase"
        verification_text = (
            "# Verification\n\n"
            "## Project type\nweb-app\n\n"
            "## Entry point\n- Command: `npm run dev`\n\n"
            "## Public surface\n1. Do thing\n\n"
            "## Verification stack\n- Acceptance tool: playwright\n"
        )
        mock_cls, _ = self._make_mock_aiohttp()

        async def write_both_sentinels(*args, **kwargs):
            (idea_dir / "roadmap_draft.md").write_text(roadmap_text)
            (idea_dir / "verification_draft.md").write_text(verification_text)
            (idea_dir / "verification_draft.done").write_text("")
            (idea_dir / "roadmap_draft.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.CONVERT_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", new=write_both_sentinels):
            r = client.post("/api/ideas/idea-a/convert")

        assert r.status_code == 200, r.text
        body = r.json()
        assert "roadmap_content" in body
        assert "verification_content" in body, (
            "Response must include verification_content alongside roadmap_content "
            "so the Ideas-screen tab can render both."
        )
        assert roadmap_text in body["roadmap_content"]
        assert verification_text in body["verification_content"]

    def test_writes_verification_content_to_session_json(self):
        client = load_server()
        idea_dir = self._write_session("idea-b", prd_content="## Problem\nB.")
        roadmap_text = "# Roadmap\n\n- [ ] `CORE-E1` | LOW | x"
        verification_text = "# Verification\n\n## Project type\ncli\n"
        mock_cls, _ = self._make_mock_aiohttp()

        async def write_both(*args, **kwargs):
            (idea_dir / "roadmap_draft.md").write_text(roadmap_text)
            (idea_dir / "verification_draft.md").write_text(verification_text)
            (idea_dir / "verification_draft.done").write_text("")
            (idea_dir / "roadmap_draft.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.CONVERT_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", new=write_both):
            r = client.post("/api/ideas/idea-b/convert")

        assert r.status_code == 200
        session = json.loads((idea_dir / "session.json").read_text())
        assert session.get("verification_content") == verification_text

    def test_times_out_when_only_roadmap_sentinel_arrives(self):
        """If the converter writes the roadmap sentinel but not the verification
        sentinel, the endpoint must keep polling (and ultimately time out)
        rather than completing on roadmap alone."""
        client = load_server()
        idea_dir = self._write_session("idea-c", prd_content="## Problem\nC.")
        mock_cls, _ = self._make_mock_aiohttp()

        async def write_only_roadmap(*args, **kwargs):
            (idea_dir / "roadmap_draft.md").write_text("# RM")
            (idea_dir / "roadmap_draft.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.CONVERT_TIMEOUT", 1), \
             patch("ui.server.CONVERT_POLL_INTERVAL", 0.1), \
             patch("ui.server.asyncio.sleep", new=write_only_roadmap):
            r = client.post("/api/ideas/idea-c/convert")

        assert r.status_code == 408, (
            "Convert must time out when verification_draft.done never lands — "
            "the contract now requires both artefacts."
        )

    def test_unlinks_stale_verification_sentinel_before_poll(self):
        """A pre-existing ``verification_draft.done`` from a prior run must not
        latch the poll. Mirrors the existing ``roadmap_draft.done`` guard at
        line 5073 of ``ui/server.py``."""
        client = load_server()
        idea_dir = self._write_session("idea-d", prd_content="## Problem\nD.")
        old_verification = "# OLD VERIFICATION"
        old_roadmap = "# OLD ROADMAP"
        (idea_dir / "roadmap_draft.md").write_text(old_roadmap)
        (idea_dir / "roadmap_draft.done").write_text("")
        (idea_dir / "verification_draft.md").write_text(old_verification)
        (idea_dir / "verification_draft.done").write_text("")
        # Force mtimes far in the past so the freshness guard cannot accept these.
        _old = 1.0
        for fname in ("roadmap_draft.md", "roadmap_draft.done",
                      "verification_draft.md", "verification_draft.done"):
            os.utime(idea_dir / fname, (_old, _old))

        new_verification = "# Verification\n\n## Project type\nweb-app\n"
        new_roadmap = "# NEW ROADMAP"
        mock_cls, _ = self._make_mock_aiohttp()

        async def write_fresh(*args, **kwargs):
            (idea_dir / "roadmap_draft.md").write_text(new_roadmap)
            (idea_dir / "verification_draft.md").write_text(new_verification)
            (idea_dir / "verification_draft.done").write_text("")
            (idea_dir / "roadmap_draft.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.CONVERT_POLL_INTERVAL", 0.05), \
             patch("ui.server.CONVERT_TIMEOUT", 5), \
             patch("ui.server.asyncio.sleep", new=write_fresh):
            r = client.post("/api/ideas/idea-d/convert")

        assert r.status_code == 200, r.text
        body = r.json()
        assert new_verification in body["verification_content"]
        assert old_verification not in body["verification_content"]
