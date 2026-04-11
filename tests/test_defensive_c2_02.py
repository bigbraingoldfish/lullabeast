"""C2-02: Bare session.post sites in Ideas flows must check HTTP status before
entering the poll loop.  A 4xx/5xx from the webhook should surface as HTTP 502,
not silently fall through to a poll timeout minutes later.

Covered endpoints:
  - POST /api/ideas/{id}/clarity-check
  - POST /api/ideas/{id}/convert
  - POST /api/ideas/{id}/alignment-check
  - POST /api/ideas/{id}/adversarial-check
  - POST /api/ideas/{id}/format-correction
"""
import json
import pytest
from datetime import datetime as real_datetime
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from fastapi.testclient import TestClient
from ui.server import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_idea(ideas_dir: Path, idea_id: str) -> Path:
    idea_path = ideas_dir / idea_id
    idea_path.mkdir(parents=True, exist_ok=True)
    session = {
        "messages": [],
        "prd_content": (
            "# PRD\n\n## Problem Statement\nTest.\n\n"
            "## Goals & Success Metrics\nGoal.\n\n"
            "## Functional Requirements\n1. A\n"
        ),
        "created": "2024-01-01T00:00:00Z",
        "updated": "2024-01-01T00:00:00Z",
    }
    (idea_path / "session.json").write_text(json.dumps(session))
    (idea_path / "roadmap_draft.md").write_text("# Roadmap\n")
    return idea_path


def _base_config(ideas_dir: Path) -> dict:
    return {
        "ideas_dir": str(ideas_dir),
        "hooks_url": "http://localhost:18789/hooks/agent",
        "hooks_token": "secret",
    }


async def _mock_post_401(*args, **kwargs):
    """Async mock returning HTTP 401 — simulates auth failure at gateway."""
    return MagicMock(status=401)


def _make_fast_expire_datetime(skip_calls: int = 3):
    """Return a datetime mock whose utcnow() returns a past epoch for the first
    `skip_calls` invocations (allowing session-key + op_start setup), then returns
    a far-future timestamp so that the first while-loop deadline check fails
    immediately — preventing a real 60-408s wait in pre-fix test runs.

    After the fix, the status check raises HTTPException(502) before any deadline
    is set, so this mock is irrelevant for the passing case.
    """
    _state = {"n": 0}

    class _MockDT:
        @staticmethod
        def utcnow() -> real_datetime:
            _state["n"] += 1
            if _state["n"] <= skip_calls:
                # Normal time — used for session_key timestamp / op_start
                return real_datetime(2000, 1, 1, 0, 0, 0)
            # Far future — deadline is at epoch+60; this timestamp >> that
            return real_datetime(9999, 12, 31, 23, 59, 59)

    return _MockDT


# ---------------------------------------------------------------------------
# clarity-check
# ---------------------------------------------------------------------------

class TestC202ClarityCheck:
    def test_401_from_webhook_raises_502_not_timeout(self, tmp_path):
        """401 from gateway must return 502, not eventually return 504 (timeout masking error)."""
        idea_id = "idea-clarity"
        _make_idea(tmp_path, idea_id)

        with patch("ui.server.load_config", return_value=_base_config(tmp_path)), \
             patch("ui.server.asyncio.sleep", new_callable=AsyncMock), \
             patch("ui.server.datetime", _make_fast_expire_datetime()), \
             patch("aiohttp.ClientSession.post", new=_mock_post_401):
            resp = client.post(f"/api/ideas/{idea_id}/clarity-check")

        assert resp.status_code != 504, (
            "Got poll timeout (504) — webhook 401 was not caught before poll loop (C2-02 unfixed)"
        )
        assert resp.status_code == 502, f"Expected 502, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------

class TestC202Convert:
    def test_401_from_webhook_raises_502_not_timeout(self, tmp_path):
        """401 from gateway must return 502, not eventually return 408 (convert timeout)."""
        idea_id = "idea-convert"
        _make_idea(tmp_path, idea_id)

        with patch("ui.server.load_config", return_value=_base_config(tmp_path)), \
             patch("ui.server.asyncio.sleep", new_callable=AsyncMock), \
             patch("ui.server.datetime", _make_fast_expire_datetime()), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server._read_conversion_prompt_text", return_value="Convert prompt"), \
             patch("aiohttp.ClientSession.post", new=_mock_post_401):
            resp = client.post(f"/api/ideas/{idea_id}/convert")

        assert resp.status_code != 408, (
            "Got convert timeout (408) — webhook 401 was not caught before poll loop (C2-02 unfixed)"
        )
        assert resp.status_code == 502, f"Expected 502, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# alignment-check
# ---------------------------------------------------------------------------

class TestC202AlignmentCheck:
    def test_401_from_webhook_raises_502_not_timeout(self, tmp_path):
        """401 from gateway must return 502, not eventually return 408."""
        idea_id = "idea-alignment"
        _make_idea(tmp_path, idea_id)

        with patch("ui.server.load_config", return_value=_base_config(tmp_path)), \
             patch("ui.server.asyncio.sleep", new_callable=AsyncMock), \
             patch("ui.server.datetime", _make_fast_expire_datetime()), \
             patch("ui.server._inject_converter_skill"), \
             patch("aiohttp.ClientSession.post", new=_mock_post_401):
            resp = client.post(f"/api/ideas/{idea_id}/alignment-check")

        assert resp.status_code != 408, (
            "Got alignment timeout (408) — webhook 401 was not caught before poll loop (C2-02 unfixed)"
        )
        assert resp.status_code == 502, f"Expected 502, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# adversarial-check
# ---------------------------------------------------------------------------

class TestC202AdversarialCheck:
    def test_401_from_webhook_raises_502_not_timeout(self, tmp_path):
        """401 from gateway must return 502, not eventually return 408."""
        idea_id = "idea-adversarial"
        _make_idea(tmp_path, idea_id)

        with patch("ui.server.load_config", return_value=_base_config(tmp_path)), \
             patch("ui.server.asyncio.sleep", new_callable=AsyncMock), \
             patch("ui.server.datetime", _make_fast_expire_datetime()), \
             patch("ui.server._inject_converter_skill"), \
             patch("aiohttp.ClientSession.post", new=_mock_post_401):
            resp = client.post(f"/api/ideas/{idea_id}/adversarial-check")

        assert resp.status_code != 408, (
            "Got adversarial timeout (408) — webhook 401 was not caught before poll loop (C2-02 unfixed)"
        )
        assert resp.status_code == 502, f"Expected 502, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# fix-roadmap-format  (finding calls it "format-correction")
# ---------------------------------------------------------------------------

class TestC202FormatCorrection:
    def test_401_from_webhook_raises_502_not_timeout(self, tmp_path):
        """401 from gateway must return 502, not eventually return 408."""
        idea_id = "idea-format"
        idea_path = _make_idea(tmp_path, idea_id)
        # fix-roadmap-format reads roadmap_content from session.json
        import json as _json
        session_data = _json.loads((idea_path / "session.json").read_text())
        session_data["roadmap_content"] = "# Roadmap draft\n"
        (idea_path / "session.json").write_text(_json.dumps(session_data))

        with patch("ui.server.load_config", return_value=_base_config(tmp_path)), \
             patch("ui.server.asyncio.sleep", new_callable=AsyncMock), \
             patch("ui.server.datetime", _make_fast_expire_datetime()), \
             patch("ui.server._inject_converter_skill"), \
             patch("aiohttp.ClientSession.post", new=_mock_post_401):
            resp = client.post(f"/api/ideas/{idea_id}/fix-roadmap-format")

        assert resp.status_code != 408, (
            "Got format timeout (408) — webhook 401 was not caught before poll loop (C2-02 unfixed)"
        )
        assert resp.status_code == 502, f"Expected 502, got {resp.status_code}: {resp.text}"
