"""Phase 2 (Remote-Call Resilience) — ui/server.py hardening.

Covers:
- T2.4: convert / clarity-check / fix-roadmap-format pass an aiohttp.ClientTimeout
  to their gateway POST (no unbounded hang).
- T2.5: _post_agent_webhook maps aiohttp.ClientPayloadError (and the ClientError
  base, and asyncio.TimeoutError) to HTTP 503, not an uncaught 500.
- T2.6: readiness writes readiness_error.json on a connection failure (not just
  HTTP>=400), and GET /readiness + /readiness/poll surface that as a distinct
  error state.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import aiohttp
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "ui"))

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import ui.server as server  # noqa: E402
from ui.server import app, _post_agent_webhook  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# T2.4 — convert / clarity / format bound their gateway POST with a ClientTimeout
# ---------------------------------------------------------------------------

def _mk_idea(tmp_path, *, prd=True, roadmap=False):
    idea_id = "idea-timeout"
    d = tmp_path / idea_id
    d.mkdir(parents=True, exist_ok=True)
    session = {"messages": [], "created": "x", "updated": "x"}
    if prd:
        session["prd_content"] = (
            "# Idea\n## Problem Statement\nP.\n## Goals & Success Metrics\nG.\n"
            "## Functional Requirements\n1. A\n"
        )
    if roadmap:
        session["roadmap_content"] = "# Roadmap\n## P1\n- [ ] T1 — do\n"
    (d / "session.json").write_text(json.dumps(session))
    return idea_id


@pytest.mark.parametrize("endpoint,need_roadmap", [
    ("clarity-check", False),
    ("convert", False),
    ("fix-roadmap-format", True),
])
def test_gateway_post_is_timeout_bounded(tmp_path, endpoint, need_roadmap):
    idea_id = _mk_idea(tmp_path, prd=True, roadmap=need_roadmap)
    captured = {}

    async def mock_post(*args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        # Short-circuit before the idle poll: a >=400 status makes the handler
        # raise immediately, so we never touch the poll/stamp machinery.
        return MagicMock(status=502)

    cfg = {
        "ideas_dir": str(tmp_path),
        "hooks_url": "http://localhost:18789/hooks/agent",
        "hooks_token": "secret",
    }
    with patch("ui.server.load_config", return_value=cfg), \
            patch("ui.server._inject_converter_skill", lambda *a, **k: None), \
            patch("aiohttp.ClientSession.post", new=mock_post):
        resp = client.post(f"/api/ideas/{idea_id}/{endpoint}")

    # The POST happened (and short-circuited at 502); the timeout must be set.
    assert isinstance(captured.get("timeout"), aiohttp.ClientTimeout), (
        f"{endpoint}: session.post must receive an aiohttp.ClientTimeout"
    )


# ---------------------------------------------------------------------------
# T2.5 — _post_agent_webhook broadened catch
# ---------------------------------------------------------------------------

def _client_session_raising(exc):
    sess = MagicMock()
    sess.post = AsyncMock(side_effect=exc)
    sess.read = AsyncMock(return_value=b"")
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=sess)


@pytest.mark.parametrize("exc", [
    aiohttp.ClientPayloadError("truncated mid-stream"),   # the new case
    aiohttp.ClientError("generic client error"),          # base class
    asyncio.TimeoutError(),                                # regression
])
def test_post_agent_webhook_maps_client_errors_to_503(exc):
    with patch("ui.server.aiohttp.ClientSession", _client_session_raising(exc)):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(_post_agent_webhook("http://h/hooks/agent", "tok", {"a": 1}))
    assert ei.value.status_code == 503


# ---------------------------------------------------------------------------
# T2.6 — readiness error artifact on connection failure + UI surfacing
# ---------------------------------------------------------------------------

def test_readiness_writes_error_artifact_on_connection_failure(tmp_path):
    idea_id = "idea-readiness"
    (tmp_path / idea_id).mkdir(parents=True)
    cfg = {
        "ideas_dir": str(tmp_path),
        "hooks_url": "http://localhost:18789/hooks/agent",
        "hooks_token": "secret",
    }

    async def boom(*args, **kwargs):
        raise aiohttp.ClientConnectionError("connection refused")

    with patch("aiohttp.ClientSession.post", new=boom):
        asyncio.run(server._trigger_readiness_assessment(idea_id, cfg))

    err = tmp_path / idea_id / "readiness_error.json"
    assert err.exists(), "connection-failure readiness must write readiness_error.json"
    payload = json.loads(err.read_text())
    assert payload.get("idea_id") == idea_id


def test_get_readiness_surfaces_error_status(tmp_path):
    idea_id = "idea-readiness-2"
    d = tmp_path / idea_id
    d.mkdir(parents=True)
    (d / "readiness_error.json").write_text(json.dumps({"error": "infra down", "idea_id": idea_id}))
    cfg = {"ideas_dir": str(tmp_path)}
    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get(f"/api/ideas/{idea_id}/readiness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"


def test_poll_readiness_reports_error_terminal(tmp_path):
    idea_id = "idea-readiness-3"
    d = tmp_path / idea_id
    d.mkdir(parents=True)
    (d / "readiness_error.json").write_text(json.dumps({"error": "infra down", "idea_id": idea_id}))
    cfg = {"ideas_dir": str(tmp_path)}
    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get(f"/api/ideas/{idea_id}/readiness/poll")
    assert resp.status_code == 200
    body = resp.json()
    # Terminal so the frontend stops polling and fetches the error status.
    assert body.get("error") is True
