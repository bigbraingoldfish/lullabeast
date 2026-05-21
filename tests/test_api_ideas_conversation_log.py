"""Tests for server-maintained conversation_log.md in Ideas chat flow.

The log is a single, server-owned, append-only file at
``~/.openclaw/ideas/{idea_id}/conversation_log.md`` that the prd-creator
agent can Read on demand. Each completed (user, assistant) pair is
appended after a successful turn. Timeouts and webhook failures must NOT
append to the log.
"""
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestConversationLog:
    """Per-idea conversation_log.md write behavior."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()
        monkeypatch.setenv("OPENCLAW_IDEAS_DIR", str(self.ideas_dir))

    def _mock_config(self):
        return {
            "ideas_dir": str(self.ideas_dir),
            "hooks_url": "http://localhost:18789/hooks/agent",
            "hooks_token": "t",
        }

    def _write_session(self, idea_id, messages=None):
        idir = self.ideas_dir / idea_id
        idir.mkdir(parents=True, exist_ok=True)
        with open(idir / "session.json", "w") as f:
            json.dump({
                "messages": messages or [],
                "prd_content": "",
                "created": "2026-03-19T10:00:00Z",
                "updated": "2026-03-19T10:00:00Z",
            }, f)

    def _write_turn_files(self, idea_id, turn_n, response_text, prd_text=""):
        turns = self.ideas_dir / idea_id / "turns"
        turns.mkdir(parents=True, exist_ok=True)
        (turns / f"{turn_n}.md").write_text(response_text)
        (turns / f"{turn_n}.done").write_text("done")
        if prd_text is not None:
            (self.ideas_dir / idea_id / "prd_draft.md").write_text(prd_text)

    def _mock_resp(self, status=200):
        r = MagicMock()
        r.status = status
        r.read = AsyncMock(return_value=b"")
        r.__aenter__ = AsyncMock(return_value=r)
        r.__aexit__ = AsyncMock(return_value=None)
        return r

    def _mock_session(self, resp):
        s = MagicMock()
        s.post = AsyncMock(return_value=resp)
        s.__aenter__ = AsyncMock(return_value=s)
        s.__aexit__ = AsyncMock(return_value=None)
        return s

    # --- Helper unit tests (cheap, no HTTP) ------------------------------

    def test_append_conversation_log_creates_file_with_turn_block(self):
        from pathlib import Path
        from ui.server import _append_conversation_log

        idir = self.ideas_dir / "h1"
        idir.mkdir()
        _append_conversation_log(idir, 1, "What should I build?", "Tell me more.")

        log = idir / "conversation_log.md"
        assert log.exists()
        text = log.read_text()
        assert "## Turn 1" in text
        assert "### User" in text
        assert "What should I build?" in text
        assert "### Assistant" in text
        assert "Tell me more." in text

    def test_append_conversation_log_idempotent_on_duplicate_turn(self):
        from ui.server import _append_conversation_log

        idir = self.ideas_dir / "h2"
        idir.mkdir()
        _append_conversation_log(idir, 1, "u", "a")
        first_text = (idir / "conversation_log.md").read_text()
        _append_conversation_log(idir, 1, "DIFFERENT", "ALSO DIFFERENT")
        second_text = (idir / "conversation_log.md").read_text()
        assert first_text == second_text, "duplicate turn append must be a no-op"

    def test_append_conversation_log_appends_subsequent_turns(self):
        from ui.server import _append_conversation_log

        idir = self.ideas_dir / "h3"
        idir.mkdir()
        _append_conversation_log(idir, 1, "first user", "first agent")
        _append_conversation_log(idir, 2, "second user", "second agent")

        text = (idir / "conversation_log.md").read_text()
        assert "## Turn 1" in text
        assert "## Turn 2" in text
        # Order preserved
        assert text.find("## Turn 1") < text.find("## Turn 2")
        assert "first user" in text and "second user" in text

    def test_append_conversation_log_no_leftover_tmp(self):
        from ui.server import _append_conversation_log

        idir = self.ideas_dir / "h4"
        idir.mkdir()
        _append_conversation_log(idir, 1, "u", "a")
        leftovers = [p for p in idir.iterdir() if p.name.startswith(".conv_log_")]
        assert leftovers == [], f"leftover tmp files: {leftovers}"

    def test_ensure_conversation_log_bootstraps_from_session_pairs(self):
        from pathlib import Path
        from ui.server import _ensure_conversation_log_exists

        idir = self.ideas_dir / "boot"
        idir.mkdir()
        prior = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        _ensure_conversation_log_exists(idir, prior)

        log = idir / "conversation_log.md"
        assert log.exists()
        text = log.read_text()
        assert "## Turn 1" in text and "## Turn 2" in text
        assert "u1" in text and "a1" in text and "u2" in text and "a2" in text

    def test_ensure_conversation_log_no_op_when_already_exists(self):
        from ui.server import _ensure_conversation_log_exists, _append_conversation_log

        idir = self.ideas_dir / "boot_existing"
        idir.mkdir()
        _append_conversation_log(idir, 1, "preserved-user", "preserved-agent")
        original = (idir / "conversation_log.md").read_text()

        _ensure_conversation_log_exists(idir, [
            {"role": "user", "content": "ignored"},
            {"role": "assistant", "content": "ignored"},
        ])
        assert (idir / "conversation_log.md").read_text() == original

    def test_ensure_conversation_log_no_op_when_no_complete_pairs(self):
        from ui.server import _ensure_conversation_log_exists

        idir = self.ideas_dir / "boot_empty"
        idir.mkdir()
        _ensure_conversation_log_exists(idir, [])
        assert not (idir / "conversation_log.md").exists()

        _ensure_conversation_log_exists(idir, [{"role": "user", "content": "orphan"}])
        assert not (idir / "conversation_log.md").exists()

    # --- Endpoint integration tests --------------------------------------

    def test_log_written_after_successful_turn(self):
        client = load_server()
        idea_id = "log_ok"
        self._write_session(idea_id)
        self._write_turn_files(idea_id, 1, "Agent says hi", "# PRD\n")
        session = self._mock_session(self._mock_resp(200))

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=session):
                with patch("asyncio.create_task"):
                    r = client.post(f"/api/ideas/{idea_id}/message",
                                    json={"content": "user input", "turn": 1})

        assert r.status_code == 200, r.text
        log = self.ideas_dir / idea_id / "conversation_log.md"
        assert log.exists(), "log must be written after successful turn"
        text = log.read_text()
        assert "## Turn 1" in text
        assert "user input" in text
        assert "Agent says hi" in text

    def test_log_appends_across_multiple_turns(self):
        client = load_server()
        idea_id = "log_multi"
        self._write_session(idea_id)

        # Turn 1
        self._write_turn_files(idea_id, 1, "agent-1", "# PRD\n")
        session = self._mock_session(self._mock_resp(200))
        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=session):
                with patch("asyncio.create_task"):
                    r1 = client.post(f"/api/ideas/{idea_id}/message",
                                     json={"content": "user-1", "turn": 1})
        assert r1.status_code == 200

        # Turn 2 — re-prep sentinel files for the new turn
        self._write_turn_files(idea_id, 2, "agent-2", "# PRD\n")
        session2 = self._mock_session(self._mock_resp(200))
        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=session2):
                with patch("asyncio.create_task"):
                    r2 = client.post(f"/api/ideas/{idea_id}/message",
                                     json={"content": "user-2", "turn": 2})
        assert r2.status_code == 200

        text = (self.ideas_dir / idea_id / "conversation_log.md").read_text()
        assert "## Turn 1" in text
        assert "## Turn 2" in text
        assert text.find("## Turn 1") < text.find("## Turn 2")
        assert "user-1" in text and "agent-1" in text
        assert "user-2" in text and "agent-2" in text

    def test_log_not_written_on_timeout(self):
        client = load_server()
        idea_id = "log_timeout"
        self._write_session(idea_id)
        # No turn files — sentinel never appears
        session = self._mock_session(self._mock_resp(200))

        async def fake_sleep(_s):
            return None

        cfg = self._mock_config()
        cfg["poll_timeout"] = 1
        cfg["poll_interval"] = 0.05

        with patch("ui.server.load_config", return_value=cfg):
            with patch("ui.server.aiohttp.ClientSession", return_value=session):
                with patch("ui.server.asyncio.sleep", side_effect=fake_sleep):
                    r = client.post(f"/api/ideas/{idea_id}/message",
                                    json={"content": "u", "turn": 1})

        assert r.status_code == 408
        assert not (self.ideas_dir / idea_id / "conversation_log.md").exists()

    def test_log_not_written_on_webhook_failure(self):
        client = load_server()
        idea_id = "log_502"
        self._write_session(idea_id)
        bad = self._mock_resp(503)
        session = self._mock_session(bad)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=session):
                r = client.post(f"/api/ideas/{idea_id}/message",
                                json={"content": "u", "turn": 1})

        assert r.status_code == 502
        assert not (self.ideas_dir / idea_id / "conversation_log.md").exists()

    def test_log_not_written_on_webhook_connection_error(self):
        client = load_server()
        idea_id = "log_503"
        self._write_session(idea_id)
        session = MagicMock()
        session.post = AsyncMock(side_effect=aiohttp.ClientConnectionError("nope"))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=session):
                r = client.post(f"/api/ideas/{idea_id}/message",
                                json={"content": "u", "turn": 1})

        assert r.status_code == 503
        assert not (self.ideas_dir / idea_id / "conversation_log.md").exists()

    def test_log_bootstrapped_for_legacy_idea_then_new_turn_appended(self):
        client = load_server()
        idea_id = "log_legacy"
        # Legacy idea: session.json has prior turns but NO conversation_log.md
        self._write_session(idea_id, messages=[
            {"role": "user", "content": "legacy-u1", "ts": "2026-01-01T00:00:00Z"},
            {"role": "assistant", "content": "legacy-a1", "ts": "2026-01-01T00:01:00Z"},
            {"role": "user", "content": "legacy-u2", "ts": "2026-01-01T00:02:00Z"},
            {"role": "assistant", "content": "legacy-a2", "ts": "2026-01-01T00:03:00Z"},
        ])
        assert not (self.ideas_dir / idea_id / "conversation_log.md").exists()

        self._write_turn_files(idea_id, 3, "agent-new", "# PRD\n")
        session = self._mock_session(self._mock_resp(200))
        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=session):
                with patch("asyncio.create_task"):
                    r = client.post(f"/api/ideas/{idea_id}/message",
                                    json={"content": "user-new", "turn": 3})
        assert r.status_code == 200

        text = (self.ideas_dir / idea_id / "conversation_log.md").read_text()
        for must in ("## Turn 1", "## Turn 2", "## Turn 3",
                     "legacy-u1", "legacy-a1", "legacy-u2", "legacy-a2",
                     "user-new", "agent-new"):
            assert must in text, f"missing {must!r} in bootstrapped log"
