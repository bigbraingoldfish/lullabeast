"""C2-02: Bare session.post sites in Ideas flows must check HTTP status before
entering the poll loop.  A 4xx/5xx from the webhook should surface as HTTP 502,
not silently fall through to a poll timeout minutes later.

Covered endpoints:
  - POST /api/ideas/{id}/clarity-check
  - POST /api/ideas/{id}/convert
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
    """Return a datetime mock whose now()/utcnow() returns a past epoch for the
    first `skip_calls` invocations (allowing the session-key timestamp setup),
    then a far-future timestamp so any datetime-based deadline check fails
    immediately — preventing a real 60-408s wait in pre-fix test runs.

    Production now builds the session-key timestamp with ``datetime.now(timezone.utc)``
    (the deprecated ``utcnow()`` was removed), so the mock primarily implements
    ``now``; ``utcnow`` stays as a back-compat alias. After the C2-02 fix the status
    check raises HTTPException(502) before any deadline is set, so the far-future
    branch is irrelevant for the passing case — the mock only needs ``.timestamp()``
    to keep working when the session key is built.
    """
    _state = {"n": 0}

    class _MockDT:
        @staticmethod
        def now(tz=None) -> real_datetime:
            _state["n"] += 1
            if _state["n"] <= skip_calls:
                # Normal time — used for the session_key timestamp_ms
                return real_datetime(2000, 1, 1, 0, 0, 0)
            # Far future — overshoots any datetime-based deadline
            return real_datetime(9999, 12, 31, 23, 59, 59)

        # No production caller uses utcnow() anymore; keep the alias so the mock
        # tolerates either spelling.
        utcnow = now

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
        """401 from gateway must return 502, not eventually return a convert timeout (504)."""
        idea_id = "idea-convert"
        _make_idea(tmp_path, idea_id)

        with patch("ui.server.load_config", return_value=_base_config(tmp_path)), \
             patch("ui.server.asyncio.sleep", new_callable=AsyncMock), \
             patch("ui.server.datetime", _make_fast_expire_datetime()), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server._read_conversion_prompt_text", return_value="Convert prompt"), \
             patch("aiohttp.ClientSession.post", new=_mock_post_401):
            resp = client.post(f"/api/ideas/{idea_id}/convert")

        assert resp.status_code not in (408, 504), (
            "Got convert timeout — webhook 401 was not caught before poll loop (C2-02 unfixed)"
        )
        assert resp.status_code == 502, f"Expected 502, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# fix-roadmap-format  (finding calls it "format-correction")
# ---------------------------------------------------------------------------

class TestC202FormatCorrection:
    def test_401_from_webhook_raises_502_not_timeout(self, tmp_path):
        """401 from gateway must return 502, not eventually return a format timeout (504)."""
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

        assert resp.status_code not in (408, 504), (
            "Got format timeout — webhook 401 was not caught before poll loop (C2-02 unfixed)"
        )
        assert resp.status_code == 502, f"Expected 502, got {resp.status_code}: {resp.text}"
