"""Reason-aware converter timeout messages (P4 follow-up).

`/convert` and `/fix-roadmap-format` share the chat send's idle-detection poll,
so they know WHY a run failed (`stalled` vs `timeout`), but both used to
discard the reason and report blanket copy. `_ideas_convert_timeout_message`
is now the SOLE author of the user-facing text for both endpoints (the
converter twin of `_ideas_timeout_message`), and the 504 body carries the
chat-style structured ``{reason, message}``.

Compat pin: the dashboard's legacy string-fallback matcher (for responses
already in flight during an upgrade) keys on "Conversion timed out", so the
timeout variant must keep starting with "{op_label} timed out".

Fixture patterns mirror ``test_api_ideas_timeout_reason.py``.
"""
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autodev.pipeline.sentinel_poller import PollResult
from fastapi.testclient import TestClient

from ui.server import (
    CONVERT_TIMEOUT,
    FORMAT_CORRECTION_TIMEOUT,
    _ideas_convert_timeout_message,
    app,
)

client = TestClient(app)


# ── unit: the message mapper ─────────────────────────────────────────────────

def test_stalled_message_says_stalled_and_retry():
    msg = _ideas_convert_timeout_message("stalled", "Conversion", 480)
    low = msg.lower()
    assert "stall" in low
    assert "retry" in low


def test_timeout_message_includes_duration_in_minutes():
    msg = _ideas_convert_timeout_message("timeout", "Conversion", 480)
    assert "8" in msg and "min" in msg.lower()


def test_timeout_variant_keeps_legacy_matcher_prefix():
    """The dashboard's string fallback for in-flight upgrade responses matches
    on "Conversion timed out" — the timeout copy must keep that prefix."""
    assert _ideas_convert_timeout_message("timeout", "Conversion", CONVERT_TIMEOUT).startswith(
        "Conversion timed out"
    )


def test_each_reason_yields_distinct_message():
    msgs = {_ideas_convert_timeout_message(r, "Conversion", 480) for r in ("stalled", "timeout", None)}
    assert len(msgs) == 3, "each failure reason must produce distinct guidance"


def test_copy_carries_no_em_dashes():
    """UI copy standard for new surfaces: plain punctuation, no em dashes."""
    for r in ("stalled", "timeout", None):
        for op in ("Conversion", "Format correction"):
            assert "—" not in _ideas_convert_timeout_message(r, op, 480)


def test_op_label_prefixes_both_operations():
    for op in ("Conversion", "Format correction"):
        for r in ("stalled", "timeout", None):
            assert _ideas_convert_timeout_message(r, op, 480).startswith(op)


# ── integration: both 504s carry structured {reason, message} ────────────────

def _make_idea(ideas_dir: Path) -> str:
    idea_id = str(uuid.uuid4())
    d = ideas_dir / idea_id
    (d / "turns").mkdir(parents=True)
    (d / "session.json").write_text(json.dumps({
        "name": "Convert reason test",
        "messages": [
            {"role": "user", "content": "hi", "ts": "2026-01-01T00:00:00Z"},
            {"role": "assistant", "content": "ok", "ts": "2026-01-01T00:00:01Z"},
        ],
        "prd_content": "## Problem Statement\nSome content.",
        "roadmap_content": "# A roadmap\n\n## Phase 1\ncontent",
        "verification_content": "v",
        "annotations": [],
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }))
    return idea_id


def _fake_webhook_session():
    async def fake_post(url, **kwargs):
        resp = MagicMock()
        resp.status = 200
        resp.read = AsyncMock(return_value=b"")
        return resp

    fake_session = MagicMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.post = AsyncMock(side_effect=fake_post)
    return fake_session


def _post(cfg, path, reason):
    with patch("ui.server.load_config", return_value=cfg), \
         patch("aiohttp.ClientSession", return_value=_fake_webhook_session()), \
         patch("ui.server._inject_converter_skill"), \
         patch(
             "ui.server._poll_sentinel_with_idle_detect",
             AsyncMock(return_value=PollResult(False, reason)),
         ):
        return client.post(path)


@pytest.mark.parametrize("reason", ["stalled", "timeout"])
def test_convert_504_carries_reason_and_mapped_message(tmp_path, reason):
    ideas_dir = tmp_path / "ideas"
    ideas_dir.mkdir()
    idea_id = _make_idea(ideas_dir)
    cfg = {
        "ideas_dir": str(ideas_dir),
        "hooks_url": "http://localhost:19999/hooks/agent",
        "hooks_token": "t",
    }
    resp = _post(cfg, f"/api/ideas/{idea_id}/convert", reason)
    assert resp.status_code == 504, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), "504 detail must be a structured {reason, message}"
    assert detail["reason"] == reason
    assert detail["message"] == _ideas_convert_timeout_message(reason, "Conversion", CONVERT_TIMEOUT)


@pytest.mark.parametrize("reason", ["stalled", "timeout"])
def test_fix_format_504_carries_reason_and_mapped_message(tmp_path, reason):
    ideas_dir = tmp_path / "ideas"
    ideas_dir.mkdir()
    idea_id = _make_idea(ideas_dir)
    cfg = {
        "ideas_dir": str(ideas_dir),
        "hooks_url": "http://localhost:19999/hooks/agent",
        "hooks_token": "t",
    }
    resp = _post(cfg, f"/api/ideas/{idea_id}/fix-roadmap-format", reason)
    assert resp.status_code == 504, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), "504 detail must be a structured {reason, message}"
    assert detail["reason"] == reason
    assert detail["message"] == _ideas_convert_timeout_message(
        reason, "Format correction", FORMAT_CORRECTION_TIMEOUT
    )
