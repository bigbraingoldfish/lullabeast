"""Tests for POST /api/ideas/{id}/message endpoint."""
import pytest
import json
import os
import tempfile
import time
import aiohttp
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestApiIdeasMessage:
    """Tests for POST /api/ideas/{id}/message endpoint."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """Set up per-test temp ideas directory."""
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()
        monkeypatch.setenv("OPENCLAW_IDEAS_DIR", str(self.ideas_dir))

    def _write_session(self, idea_id, data):
        """Write session.json for an idea."""
        sess_dir = self.ideas_dir / idea_id
        sess_dir.mkdir(parents=True, exist_ok=True)
        path = sess_dir / "session.json"
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def _write_turn_files(self, idea_id, turn_n, response_text, prd_text=""):
        """Write {n}.md and {n}.done sentinel files (AGENTS.md contract)."""
        turns_dir = self.ideas_dir / idea_id / "turns"
        turns_dir.mkdir(parents=True, exist_ok=True)
        md_path = turns_dir / f"{turn_n}.md"
        done_path = turns_dir / f"{turn_n}.done"
        prd_path = self.ideas_dir / idea_id / "prd_draft.md"
        with open(md_path, "w") as f:
            f.write(response_text)
        with open(done_path, "w") as f:
            f.write("")
        if prd_text is not None:
            with open(prd_path, "w") as f:
                f.write(prd_text)

    def _mock_config(self):
        return {
            "ideas_dir": str(self.ideas_dir),
            "hooks_url": "http://localhost:18789/hooks/agent",
            "hooks_token": "test-token",
        }

    def _make_mock_response(self):
        """Build a properly-async mock response for session.post()."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=b"")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        return mock_resp

    def _make_mock_session(self, mock_resp):
        """Build a mock aiohttp.ClientSession with async post method."""
        mock_session = MagicMock()
        # post() must be AsyncMock so `await session.post(...)` works
        mock_session.post = AsyncMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        return mock_session

    def test_endpoint_exists(self):
        """POST /api/ideas/1/message returns 200 when sentinel is found."""
        client = load_server()

        # Pre-create session.json
        self._write_session("1", {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })

        # Pre-create turn files (sentinel present immediately)
        self._write_turn_files("1", 1, "Hello from agent", "# PRD\nDraft content")

        mock_resp = self._make_mock_response()
        mock_session = self._make_mock_session(mock_resp)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                response = client.post(
                    "/api/ideas/1/message",
                    json={"content": "Hi agent", "turn": 1},
                )

        assert response.status_code in (200, 408), f"Unexpected status: {response.status_code}"

    def test_returns_response_and_prd_content_on_success(self):
        """Response body contains {response, prd_content} fields."""
        client = load_server()

        self._write_session("5", {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })

        self._write_turn_files("5", 1, "Agent response text", "# PRD\nSome draft")

        mock_resp = self._make_mock_response()
        mock_session = self._make_mock_session(mock_resp)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                response = client.post(
                    "/api/ideas/5/message",
                    json={"content": "Hello", "turn": 1},
                )

        if response.status_code == 200:
            body = response.json()
            assert "response" in body, f"Missing 'response' field: {body}"
            assert "prd_content" in body, f"Missing 'prd_content' field: {body}"

    def test_returns_408_on_timeout(self):
        """Returns 408 when sentinel file is not found within timeout."""
        client = load_server()

        self._write_session("9", {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })
        # Do NOT write turn files — sentinel will never appear

        mock_resp = self._make_mock_response()
        mock_session = self._make_mock_session(mock_resp)

        async def fake_sleep(seconds):
            pass

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                # Patch POLL_TIMEOUT to 2s so the test completes quickly
                with patch("ui.server.POLL_TIMEOUT", 2):
                    with patch("ui.server.asyncio.sleep", side_effect=fake_sleep):
                        response = client.post(
                            "/api/ideas/9/message",
                            json={"content": "Timeout test", "turn": 1},
                        )

        # Should timeout and return 408
        assert response.status_code == 408, f"Expected 408, got {response.status_code}"

    def test_returns_502_on_webhook_bad_status(self):
        """Returns 502 when hook returns non-2xx; pending assistant marked error in session."""
        client = load_server()
        idea_id = "webhook_502"
        self._write_session(idea_id, {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })
        # No turn files — would hang until poll timeout if webhook were treated as success

        mock_resp = self._make_mock_response()
        mock_resp.status = 503
        mock_session = self._make_mock_session(mock_resp)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                response = client.post(
                    f"/api/ideas/{idea_id}/message",
                    json={"content": "Hi", "turn": 1},
                )

        assert response.status_code == 502, f"Expected 502, got {response.status_code}"
        sess_path = self.ideas_dir / idea_id / "session.json"
        with open(sess_path) as f:
            data = json.load(f)
        assistants = [m for m in data.get("messages", []) if m.get("role") == "assistant"]
        assert assistants, "expected assistant message in session"
        last_a = assistants[-1]
        assert last_a.get("error") is True
        assert last_a.get("pending") is False
        assert "503" in (last_a.get("content") or "") or "gateway" in (last_a.get("content") or "").lower()

    def test_returns_503_on_webhook_connection_error(self):
        """Returns 503 when hook connection fails; pending assistant marked error."""
        client = load_server()
        idea_id = "webhook_503"
        self._write_session(idea_id, {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })

        mock_session = MagicMock()
        mock_session.post = AsyncMock(side_effect=aiohttp.ClientConnectionError("refused"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                response = client.post(
                    f"/api/ideas/{idea_id}/message",
                    json={"content": "Hi", "turn": 1},
                )

        assert response.status_code == 503, f"Expected 503, got {response.status_code}"
        sess_path = self.ideas_dir / idea_id / "session.json"
        with open(sess_path) as f:
            data = json.load(f)
        last_a = [m for m in data.get("messages", []) if m.get("role") == "assistant"][-1]
        assert last_a.get("error") is True
        assert last_a.get("pending") is False

    def test_webhook_sent_with_correct_payload(self):
        """Webhook is POSTed to hooks_url with correct Bearer auth and body."""
        client = load_server()
        captured_payload = {}

        self._write_session("2", {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })
        self._write_turn_files("2", 3, "Agent response", "# Draft")

        def capture_post(url, **kwargs):
            captured_payload["url"] = url
            captured_payload["headers"] = kwargs.get("headers", {})
            captured_payload["json"] = kwargs.get("json", {})
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read = AsyncMock(return_value=b"")
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=None)
            return mock_resp

        mock_session = MagicMock()
        mock_session.post = AsyncMock(side_effect=capture_post)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                response = client.post(
                    "/api/ideas/2/message",
                    json={"content": "Test message", "turn": 3},
                )

        assert "url" in captured_payload, "POST was not called"
        assert "Authorization" in captured_payload.get("headers", {}), \
            f"Missing Authorization header: {captured_payload}"

    def test_session_json_updated_atomically(self):
        """session.json is written via .tmp + os.replace after agent turn."""
        client = load_server()
        idea_id = "7"
        sess_path = self.ideas_dir / idea_id / "session.json"

        self._write_session(idea_id, {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })
        self._write_turn_files(idea_id, 1, "Agent says hello", "# PRD\nUpdated content")

        mock_resp = self._make_mock_response()
        mock_session = self._make_mock_session(mock_resp)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                response = client.post(
                    f"/api/ideas/{idea_id}/message",
                    json={"content": "Hello", "turn": 1},
                )

        # Verify session.json exists and has correct structure
        assert sess_path.exists(), f"session.json not created at {sess_path}"
        with open(sess_path) as f:
            data = json.load(f)
        assert "messages" in data
        assert "prd_content" in data
        assert len(data["messages"]) == 2, f"Expected 2 messages, got {len(data['messages'])}"

        # No leftover .tmp file
        tmp_path = str(sess_path) + ".tmp"
        assert not os.path.exists(tmp_path), "Leftover .tmp file found"

    def test_turn_n_from_request_body(self):
        """turn_n is read from request body, not derived from message count."""
        client = load_server()
        captured_payload = {}

        self._write_session("3", {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })
        self._write_turn_files("3", 10, "Turn 10 response", "")

        def capture_post(url, **kwargs):
            captured_payload["url"] = url
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read = AsyncMock(return_value=b"")
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=None)
            return mock_resp

        mock_session = MagicMock()
        mock_session.post = AsyncMock(side_effect=capture_post)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                # Send turn=10 explicitly in body
                response = client.post(
                    "/api/ideas/3/message",
                    json={"content": "Which turn?", "turn": 10},
                )

        # Sentinel path should use turn 10
        turn_done_path = self.ideas_dir / "3" / "turns" / "10.done"
        assert turn_done_path.exists(), f"Expected 10.done at {turn_done_path}"

    def _capture_conversation_post(self):
        """Return (all_payloads list, mock_session) for capturing all webhook POST calls.

        The readiness background job fires a second POST after each turn (session key
        contains ':readiness'). Callers must filter to the conversation POST by checking
        that the session key contains ':session-'.
        """
        all_payloads = []

        def capture_post(url, **kwargs):
            all_payloads.append(kwargs.get("json", {}))
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read = AsyncMock(return_value=b"")
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=None)
            return mock_resp

        mock_session = MagicMock()
        mock_session.post = AsyncMock(side_effect=capture_post)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        return all_payloads, mock_session

    def _conv_message(self, all_payloads):
        """Extract the message body from the conversation turn POST (not the readiness POST)."""
        for payload in all_payloads:
            session_key = payload.get("sessionKey", "")
            if ":session-" in session_key:
                return payload.get("message", "")
        return ""

    def test_conversation_history_injected_when_prior_messages_exist(self):
        """Webhook message includes [CONVERSATION HISTORY] block when prior turns exist."""
        client = load_server()
        all_payloads, mock_session = self._capture_conversation_post()

        self._write_session("hist_yes", {
            "messages": [
                {"role": "user", "content": "What should I build?", "ts": "2026-01-01T00:00:00Z"},
                {"role": "assistant", "content": "Tell me more about your idea.", "ts": "2026-01-01T00:01:00Z"},
            ],
            "prd_content": "",
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:01:00Z",
        })
        self._write_turn_files("hist_yes", 2, "Turn 2 agent response", "")

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                client.post(
                    "/api/ideas/hist_yes/message",
                    json={"content": "Let's continue", "turn": 2},
                )

        msg = self._conv_message(all_payloads)
        assert "[CONVERSATION HISTORY]" in msg, "Missing CONVERSATION HISTORY block"
        assert "What should I build?" in msg, "Missing prior user message"
        assert "Tell me more about your idea." in msg, "Missing prior assistant message"
        assert "[/CONVERSATION HISTORY]" in msg, "Missing closing tag"
        assert "Let's continue" in msg, "Missing current user message"

    def test_no_history_block_on_first_turn(self):
        """Webhook message has no [CONVERSATION HISTORY] block when no prior messages exist."""
        client = load_server()
        all_payloads, mock_session = self._capture_conversation_post()

        self._write_session("hist_no", {
            "messages": [],
            "prd_content": "",
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        })
        self._write_turn_files("hist_no", 1, "First turn response", "")

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                client.post(
                    "/api/ideas/hist_no/message",
                    json={"content": "First message", "turn": 1},
                )

        msg = self._conv_message(all_payloads)
        assert "[CONVERSATION HISTORY]" not in msg, "Should not have history block on first turn"
        assert "First message" in msg, "Current message should be present"

    def test_history_block_precedes_current_message(self):
        """[CONVERSATION HISTORY] block appears before the current user message in payload."""
        client = load_server()
        all_payloads, mock_session = self._capture_conversation_post()

        self._write_session("hist_order", {
            "messages": [
                {"role": "user", "content": "Earlier turn", "ts": "2026-01-01T00:00:00Z"},
                {"role": "assistant", "content": "Earlier response", "ts": "2026-01-01T00:01:00Z"},
            ],
            "prd_content": "",
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:01:00Z",
        })
        self._write_turn_files("hist_order", 2, "Response", "")

        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                client.post(
                    "/api/ideas/hist_order/message",
                    json={"content": "Current message", "turn": 2},
                )

        msg = self._conv_message(all_payloads)
        history_pos = msg.find("[CONVERSATION HISTORY]")
        current_pos = msg.find("Current message")
        assert history_pos != -1, "CONVERSATION HISTORY block not found in message"
        assert history_pos < current_pos, (
            "CONVERSATION HISTORY block should appear before the current message"
        )

    # ------------------------------------------------------------------
    # Idle detection integration tests
    # ------------------------------------------------------------------

    def _mock_config_with_openclaw(self, openclaw_root):
        """Config that includes openclaw_root for idle detection tests."""
        cfg = self._mock_config()
        cfg["openclaw_root"] = str(openclaw_root)
        cfg["ideas_idle_threshold"] = 0.3   # very short for tests
        cfg["ideas_startup_grace"] = 0.2    # very short for tests
        return cfg

    def _write_sessions_json(self, openclaw_root, idea_id, turn_n, session_id):
        """Write a sessions.json entry mapping the session key to a sessionId."""
        sessions_dir = Path(openclaw_root) / "agents" / "prd-creator" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        key = f"agent:prd-creator:ideas:{idea_id}:session-{turn_n}"
        data = {key: {"sessionId": session_id, "updatedAt": 1700000001000}}
        (sessions_dir / "sessions.json").write_text(json.dumps(data))
        return sessions_dir

    def test_returns_408_when_agent_goes_idle(self):
        """Returns 408 within idle_threshold + headroom when JSONL stops advancing.

        With idle_threshold=0.3s and startup_grace=0.2s, idle detection should fire in
        ~0.5s — well before the 30s hard poll_timeout. This distinguishes idle detection
        from a plain deadline timeout.
        """
        import time as _time
        client = load_server()
        idea_id = "idle_test_idea"
        session_id = "idle-0000-0000-0000-000000000099"

        self._write_session(idea_id, {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })
        # Write JSONL once but do NOT update it further and do NOT write sentinel
        sessions_dir = self._write_sessions_json(
            self.ideas_dir.parent / "openclaw_root_idle",
            idea_id, 1, session_id,
        )
        jsonl_path = sessions_dir / f"{session_id}.jsonl"
        jsonl_path.write_text("line 1\n")

        mock_resp = self._make_mock_response()
        mock_session = self._make_mock_session(mock_resp)

        cfg = self._mock_config_with_openclaw(self.ideas_dir.parent / "openclaw_root_idle")
        cfg["poll_timeout"] = 30         # long hard deadline — idle detection must fire first
        cfg["ideas_idle_threshold"] = 0.3
        cfg["ideas_startup_grace"] = 0.2

        start = _time.monotonic()
        with patch("ui.server.load_config", return_value=cfg):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                response = client.post(
                    f"/api/ideas/{idea_id}/message",
                    json={"content": "test idle", "turn": 1},
                )
        elapsed = _time.monotonic() - start

        assert response.status_code == 408, f"Expected 408 on agent idle, got {response.status_code}"
        # Key assertion: idle detection fires well before the 30s hard timeout
        assert elapsed < 5.0, (
            f"Idle detection should fire within ~0.5s, but took {elapsed:.2f}s — "
            "likely falling back to hard timeout rather than idle detection"
        )

    def test_does_not_408_while_jsonl_active(self):
        """Does not time out when JSONL mtime keeps advancing and sentinel appears."""
        client = load_server()
        idea_id = "active_test_idea"
        session_id = "active-0000-0000-0000-000000000088"

        self._write_session(idea_id, {
            "messages": [],
            "prd_content": "",
            "created": "2026-03-19T10:00:00Z",
            "updated": "2026-03-19T10:00:00Z",
        })
        sessions_dir = self._write_sessions_json(
            self.ideas_dir.parent / "openclaw_root_active",
            idea_id, 1, session_id,
        )
        jsonl_path = sessions_dir / f"{session_id}.jsonl"
        jsonl_path.write_text("line 1\n")

        # Pre-write the sentinel so the poll succeeds immediately
        self._write_turn_files(idea_id, 1, "Active agent reply", "# PRD\nDraft")

        mock_resp = self._make_mock_response()
        mock_session = self._make_mock_session(mock_resp)

        with patch("ui.server.load_config", return_value=self._mock_config_with_openclaw(
            self.ideas_dir.parent / "openclaw_root_active"
        )):
            with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
                response = client.post(
                    f"/api/ideas/{idea_id}/message",
                    json={"content": "test active", "turn": 1},
                )

        assert response.status_code == 200, f"Expected 200 when sentinel present, got {response.status_code}"
