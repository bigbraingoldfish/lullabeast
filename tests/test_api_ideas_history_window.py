"""Bounded conversation-history window in Ideas chat webhook prompt.

The server must inject at most ``IDEAS_HISTORY_WINDOW_TURNS`` (3) complete
(user, assistant) pairs into the ``[CONVERSATION HISTORY]`` block prepended
to each ``POST /api/ideas/{id}/message`` webhook, and must respect a hard
character budget (``AUTODEV_IDEAS_HISTORY_CHAR_BUDGET``, default 20000).
Older pairs become a single ``[NOTE] N earlier turn(s) omitted...`` line
pointing the agent at ``conversation_log.md``.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestHistoryWindow:

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

    def _write_session(self, idea_id, messages):
        idir = self.ideas_dir / idea_id
        idir.mkdir(parents=True, exist_ok=True)
        with open(idir / "session.json", "w") as f:
            json.dump({
                "messages": messages,
                "prd_content": "",
                "created": "2026-01-01T00:00:00Z",
                "updated": "2026-01-01T00:00:00Z",
            }, f)

    def _write_turn_files(self, idea_id, turn_n, response_text="ok", prd_text=""):
        turns = self.ideas_dir / idea_id / "turns"
        turns.mkdir(parents=True, exist_ok=True)
        (turns / f"{turn_n}.md").write_text(response_text)
        (turns / f"{turn_n}.done").write_text("done")
        if prd_text is not None:
            (self.ideas_dir / idea_id / "prd_draft.md").write_text(prd_text)

    def _capture(self):
        payloads = []

        def post(url, **kwargs):
            payloads.append(kwargs.get("json", {}))
            r = MagicMock()
            r.status = 200
            r.read = AsyncMock(return_value=b"")
            r.__aenter__ = AsyncMock(return_value=r)
            r.__aexit__ = AsyncMock(return_value=None)
            return r

        s = MagicMock()
        s.post = AsyncMock(side_effect=post)
        s.__aenter__ = AsyncMock(return_value=s)
        s.__aexit__ = AsyncMock(return_value=None)
        return payloads, s

    def _conv_msg(self, payloads):
        for p in payloads:
            if ":session-" in (p.get("sessionKey") or ""):
                return p.get("message", "")
        return ""

    @staticmethod
    def _pair(u, a, idx):
        return [
            {"role": "user", "content": u, "ts": f"2026-01-01T00:{idx:02d}:00Z"},
            {"role": "assistant", "content": a, "ts": f"2026-01-01T00:{idx:02d}:30Z"},
        ]

    def test_window_caps_at_three_turns_and_notes_omitted_count(self):
        client = load_server()
        idea_id = "win_cap"
        msgs = []
        for i in range(1, 6):  # 5 complete pairs
            msgs.extend(self._pair(f"u{i}", f"a{i}", i))
        self._write_session(idea_id, msgs)
        self._write_turn_files(idea_id, 6)

        payloads, session = self._capture()
        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=session):
                with patch("asyncio.create_task"):
                    r = client.post(f"/api/ideas/{idea_id}/message",
                                    json={"content": "current", "turn": 6})
        assert r.status_code == 200, r.text

        msg = self._conv_msg(payloads)
        assert "[CONVERSATION HISTORY]" in msg
        # Last three pairs are turns 3, 4, 5 in the user-facing sequence
        assert "[Turn 3]" in msg
        assert "[Turn 4]" in msg
        assert "[Turn 5]" in msg
        assert "[Turn 1]" not in msg
        assert "[Turn 2]" not in msg
        assert "u3" in msg and "a3" in msg
        assert "u5" in msg and "a5" in msg
        assert "u1" not in msg and "u2" not in msg
        assert "[NOTE]" in msg
        assert "2 earlier turn(s) omitted" in msg
        assert f"ideas/{idea_id}/conversation_log.md" in msg

    def test_no_history_block_or_note_on_first_turn(self):
        client = load_server()
        idea_id = "win_first"
        self._write_session(idea_id, [])
        self._write_turn_files(idea_id, 1)

        payloads, session = self._capture()
        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=session):
                with patch("asyncio.create_task"):
                    r = client.post(f"/api/ideas/{idea_id}/message",
                                    json={"content": "first", "turn": 1})
        assert r.status_code == 200
        msg = self._conv_msg(payloads)
        assert "[CONVERSATION HISTORY]" not in msg
        assert "[NOTE]" not in msg
        assert "first" in msg  # current message still injected

    def test_window_under_three_turns_renders_all_without_note(self):
        client = load_server()
        idea_id = "win_two"
        msgs = []
        for i in range(1, 3):  # 2 complete pairs
            msgs.extend(self._pair(f"u{i}", f"a{i}", i))
        self._write_session(idea_id, msgs)
        self._write_turn_files(idea_id, 3)

        payloads, session = self._capture()
        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=session):
                with patch("asyncio.create_task"):
                    r = client.post(f"/api/ideas/{idea_id}/message",
                                    json={"content": "cur", "turn": 3})
        assert r.status_code == 200
        msg = self._conv_msg(payloads)
        assert "[Turn 1]" in msg and "[Turn 2]" in msg
        assert "[NOTE]" not in msg, "no omitted-turn note when all pairs fit the window"

    def test_window_trims_when_over_char_budget(self, monkeypatch):
        client = load_server()
        idea_id = "win_budget"
        # 3 pairs ~ 400 chars each → ~1200 chars; budget 600 forces drop
        msgs = []
        for i in range(1, 4):
            msgs.extend(self._pair(f"USER-{i}-" + ("x" * 200),
                                   f"AGENT-{i}-" + ("y" * 200), i))
        self._write_session(idea_id, msgs)
        self._write_turn_files(idea_id, 4)

        monkeypatch.setenv("AUTODEV_IDEAS_HISTORY_CHAR_BUDGET", "600")

        payloads, session = self._capture()
        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=session):
                with patch("asyncio.create_task"):
                    r = client.post(f"/api/ideas/{idea_id}/message",
                                    json={"content": "x", "turn": 4})
        assert r.status_code == 200
        msg = self._conv_msg(payloads)
        # At least one pair dropped under budget pressure → note reports it
        assert "[NOTE]" in msg
        assert "earlier turn(s) omitted" in msg
        # The earliest pair must be gone
        assert "USER-1-" not in msg
        # Most recent pair must still be present
        assert "USER-3-" in msg

    def test_window_truncates_single_oversized_pair(self, monkeypatch):
        client = load_server()
        idea_id = "win_truncate"
        big_user = "U" * 5000
        big_agent = "A" * 5000
        self._write_session(idea_id, self._pair(big_user, big_agent, 1))
        self._write_turn_files(idea_id, 2)

        monkeypatch.setenv("AUTODEV_IDEAS_HISTORY_CHAR_BUDGET", "500")

        payloads, session = self._capture()
        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=session):
                with patch("asyncio.create_task"):
                    r = client.post(f"/api/ideas/{idea_id}/message",
                                    json={"content": "x", "turn": 2})
        assert r.status_code == 200
        msg = self._conv_msg(payloads)
        # History block must be present but the giant pair was truncated
        assert "[CONVERSATION HISTORY]" in msg
        assert "truncated" in msg.lower()
        # The full 5000-char run of U/A must NOT survive verbatim
        assert "U" * 4000 not in msg
        assert "A" * 4000 not in msg

    def test_window_excludes_orphaned_user_message(self):
        client = load_server()
        idea_id = "win_orphan"
        msgs = [
            {"role": "user", "content": "orphan-u", "ts": "2026-01-01T00:00:00Z"},
            # No assistant follow-up → orphan pair
            {"role": "user", "content": "kept-u", "ts": "2026-01-01T00:01:00Z"},
            {"role": "assistant", "content": "kept-a", "ts": "2026-01-01T00:02:00Z"},
        ]
        self._write_session(idea_id, msgs)
        self._write_turn_files(idea_id, 2)

        payloads, session = self._capture()
        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=session):
                with patch("asyncio.create_task"):
                    r = client.post(f"/api/ideas/{idea_id}/message",
                                    json={"content": "x", "turn": 2})
        assert r.status_code == 200
        msg = self._conv_msg(payloads)
        assert "orphan-u" not in msg, "orphaned user message must be excluded"
        assert "kept-u" in msg and "kept-a" in msg

    def test_pointer_text_includes_correct_idea_id(self):
        client = load_server()
        idea_id = "pointer_check"
        msgs = []
        for i in range(1, 5):  # 4 pairs → 1 omitted under cap of 3
            msgs.extend(self._pair(f"u{i}", f"a{i}", i))
        self._write_session(idea_id, msgs)
        self._write_turn_files(idea_id, 5)

        payloads, session = self._capture()
        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=session):
                with patch("asyncio.create_task"):
                    r = client.post(f"/api/ideas/{idea_id}/message",
                                    json={"content": "x", "turn": 5})
        assert r.status_code == 200
        msg = self._conv_msg(payloads)
        assert f"~/.openclaw/ideas/{idea_id}/conversation_log.md" in msg

    # --- Direct helper tests --------------------------------------------

    def test_complete_pairs_extracts_pairs_in_order(self):
        from ui.server import _complete_pairs

        msgs = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        pairs = _complete_pairs(msgs)
        assert len(pairs) == 2
        assert pairs[0][0]["content"] == "u1" and pairs[0][1]["content"] == "a1"
        assert pairs[1][0]["content"] == "u2" and pairs[1][1]["content"] == "a2"

    def test_complete_pairs_skips_errored_and_orphan_user(self):
        from ui.server import _complete_pairs

        msgs = [
            {"role": "user", "content": "errored", "error": True},
            {"role": "assistant", "content": "should-not-pair-with-errored"},
            {"role": "user", "content": "orphan"},  # next is a user, not assistant
            {"role": "user", "content": "ok-u"},
            {"role": "assistant", "content": "ok-a"},
        ]
        pairs = _complete_pairs(msgs)
        # Only the final clean (user, assistant) pair should be returned
        assert len(pairs) == 1
        assert pairs[0][0]["content"] == "ok-u"
        assert pairs[0][1]["content"] == "ok-a"

    def test_complete_pairs_skips_errored_assistant(self):
        from ui.server import _complete_pairs

        msgs = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "bad", "error": True},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        pairs = _complete_pairs(msgs)
        # u1 → errored assistant: pair skipped. u2 → a2 kept.
        assert len(pairs) == 1
        assert pairs[0][0]["content"] == "u2"
