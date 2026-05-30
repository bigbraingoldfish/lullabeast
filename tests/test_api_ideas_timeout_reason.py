"""Reason-aware Ideas chat timeout messages.

The chat poll (`_poll_sentinel_with_idle_detect`) returns a `PollResult` whose
`reason` distinguishes WHY a turn failed. The chat *send* waits for a
**definitive** verdict — `stalled` (the agent started then went silent) or
`timeout` (it ran the full backstop). (It passes `startup_grace=None`, so the
premature `no_first_activity` early-fail never fires on this path.) Before
reason-awareness both collapsed to a single generic "the model may be slow"
message, discarding insight we already had.

`_ideas_timeout_message` is the SOLE author of the user-facing text: the 408
response body and the persisted session placeholder both use it, and the
frontend renders it verbatim (no second copy of the wording — avoids the
dual-source drift that bit the timeout *values*).
"""
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autodev.pipeline.sentinel_poller import PollResult
from fastapi.testclient import TestClient

from ui.server import _ideas_timeout_message, app

client = TestClient(app)

FAKE_CONFIG = {
    "ideas_dir": "/tmp/test-timeout-reason",
    "hooks_url": "http://localhost:19999/hooks/agent",
    "hooks_token": "test-token",
}


# ── unit: the message mapper ─────────────────────────────────────────────────

def test_stalled_message_describes_mid_response_stall():
    msg = _ideas_timeout_message("stalled", 900)
    low = msg.lower()
    assert "stall" in low or "went quiet" in low
    assert "retry" in low


def test_timeout_message_includes_duration_in_minutes():
    msg = _ideas_timeout_message("timeout", 900)
    # 900s → ~15 min; the message should give the operator the magnitude.
    assert "15" in msg and "min" in msg.lower()


def test_unknown_reason_falls_back_to_generic():
    for reason in (None, "succeeded", "weird"):
        assert _ideas_timeout_message(reason, 900) == (
            "Agent timed out — the model may be slow. You can retry."
        )


def test_each_reason_yields_a_distinct_message():
    msgs = {
        _ideas_timeout_message(r, 900)
        for r in ("stalled", "timeout")
    }
    assert len(msgs) == 2, "each failure reason must produce distinct guidance"


# ── integration: the 408 carries reason + message; placeholder matches ───────

def _make_idea(ideas_dir: Path) -> str:
    idea_id = str(uuid.uuid4())
    d = ideas_dir / idea_id
    (d / "turns").mkdir(parents=True)
    (d / "session.json").write_text(json.dumps({
        "name": "Reason test",
        "messages": [],
        "prd_content": "",
        "roadmap_content": "",
        "annotations": [],
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }))
    return idea_id


@pytest.mark.parametrize("reason", ["stalled", "timeout"])
def test_408_body_and_placeholder_carry_reason_specific_message(tmp_path, reason):
    ideas_dir = tmp_path / "ideas"
    ideas_dir.mkdir()
    idea_id = _make_idea(ideas_dir)
    idea_dir = ideas_dir / idea_id
    cfg = {**FAKE_CONFIG, "ideas_dir": str(ideas_dir)}

    async def fake_post(url, **kwargs):
        resp = MagicMock()
        resp.status = 200
        resp.read = AsyncMock(return_value=b"")
        return resp

    fake_session = MagicMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.post = AsyncMock(side_effect=fake_post)

    expected = _ideas_timeout_message(reason, 900.0)

    with patch("ui.server.load_config", return_value=cfg):
        with patch("aiohttp.ClientSession", return_value=fake_session):
            with patch("asyncio.create_task"):
                with patch(
                    "ui.server._poll_sentinel_with_idle_detect",
                    AsyncMock(return_value=PollResult(False, reason)),
                ):
                    resp = client.post(
                        f"/api/ideas/{idea_id}/message",
                        json={"content": "go", "turn": 1},
                    )

    assert resp.status_code == 408, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), "408 detail must be a structured {reason, message}"
    assert detail["reason"] == reason
    assert detail["message"] == expected
    assert detail["message"] != "Agent timed out — the model may be slow. You can retry." \
        or reason not in ("no_first_activity", "stalled", "timeout")

    # The persisted placeholder the UI shows on refresh must match the 408 body
    # (single source — same string in both places).
    session = json.loads((idea_dir / "session.json").read_text())
    pend = [m for m in session["messages"] if m.get("role") == "assistant" and m.get("error")]
    assert pend, "expected an errored assistant placeholder"
    assert pend[-1]["content"] == expected
