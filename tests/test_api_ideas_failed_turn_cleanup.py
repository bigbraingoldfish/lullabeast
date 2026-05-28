"""Tests for trailing failed-turn cleanup on POST /api/ideas/{id}/message.

When a chat turn fails (408 timeout or 502/503 gateway error) the session.json
ends up with the failed pair persisted: ``(user_message, assistant_with_error)``.
Without server-side cleanup, every retry appends another pair, producing the
stacked-red-bubbles screenshot the user reported.

The client filters ``_gatewayFailed`` rows from ``baseMsgs`` before sending,
so the chat *looks* clean while the user is in the session — but a browser
refresh reloads from ``session.json`` and the stacking reappears.

Fix: ``_strip_trailing_failed_pairs(messages)`` walks back from the end of
the messages list and drops failed pairs (and orphan trailing error bubbles)
until it hits a non-failed item.  Only trailing failures are stripped;
mid-conversation errors are preserved as conversation history (the user
explicitly chose the more-conservative semantic).

The helper is called inside ``post_ideas_message`` right after the
``pre_session`` read, so the persisted ``session.json`` matches what the UI
shows after the client-side filter runs.
"""

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from ui.server import _strip_trailing_failed_pairs, app

client = TestClient(app)


FAKE_CONFIG = {
    "ideas_dir": "/tmp/test-failed-turn-cleanup",
    "hooks_url": "http://localhost:19999/hooks/agent",
    "hooks_token": "test-token",
}


# ---------------------------------------------------------------------------
# Unit tests — _strip_trailing_failed_pairs helper
# ---------------------------------------------------------------------------


def _u(content: str = "hi", **extra) -> dict:
    return {"role": "user", "content": content, "ts": "2026-01-01T00:00:00Z", **extra}


def _a(content: str = "ok", *, error: bool = False, **extra) -> dict:
    out = {"role": "assistant", "content": content, "ts": "2026-01-01T00:00:01Z"}
    if error:
        out["error"] = True
    out.update(extra)
    return out


def test_strip_no_op_when_list_is_empty():
    assert _strip_trailing_failed_pairs([]) == []


def test_strip_no_op_when_no_trailing_error():
    """Conversation ending in (user, successful assistant) is preserved unchanged."""
    msgs = [_u("hi"), _a("hello"), _u("more"), _a("more!")]
    assert _strip_trailing_failed_pairs(msgs) == msgs


def test_strip_single_trailing_failed_pair():
    """The screenshot pattern: one failed turn at the end → both rows dropped."""
    msgs = [_u("hi"), _a("hello"), _u("how can we make it better?"), _a("timed out", error=True)]
    result = _strip_trailing_failed_pairs(msgs)
    assert result == [_u("hi"), _a("hello")]


def test_strip_multiple_contiguous_trailing_failed_pairs():
    """Several stacked retries (real screenshot) → all trailing failures collapse."""
    msgs = [
        _u("hi"), _a("hello"),
        _u("retry 1"), _a("timed out", error=True),
        _u("retry 2"), _a("timed out", error=True),
        _u("retry 3"), _a("timed out", error=True),
    ]
    result = _strip_trailing_failed_pairs(msgs)
    assert result == [_u("hi"), _a("hello")]


def test_strip_preserves_non_trailing_failed_pair():
    """Mid-conversation failure stays as history — only trailing failures go.

    User explicitly chose this semantic ("preserves any historical errors in
    the middle of the conversation").  If a future refactor changes this to
    'all failures' the test must fail so the policy change is explicit.
    """
    msgs = [
        _u("hi"), _a("hello"),
        _u("question A"), _a("answer A failed", error=True),
        _u("question B"), _a("answer B succeeded"),
    ]
    result = _strip_trailing_failed_pairs(msgs)
    assert result == msgs  # unchanged — failure is mid-conversation


def test_strip_orphan_trailing_error_with_no_preceding_user():
    """Edge case: a malformed session.json with a lone trailing error bubble
    (no preceding user) — strip the bubble; leave the rest alone.
    """
    msgs = [_u("hi"), _a("hello"), _a("orphan error", error=True)]
    result = _strip_trailing_failed_pairs(msgs)
    assert result == [_u("hi"), _a("hello")]


def test_strip_does_not_drop_trailing_user_only():
    """A trailing user message with no assistant after it (mid-write race
    snapshot) must be preserved — it's not a failed turn, it's an in-progress
    one.  Dropping it would lose the user's last input on refresh.
    """
    msgs = [_u("hi"), _a("hello"), _u("waiting on this")]
    result = _strip_trailing_failed_pairs(msgs)
    assert result == msgs


def test_strip_returns_new_list_not_mutating_input():
    """Helper must be pure — the caller's input list must be unchanged."""
    msgs = [_u("a"), _a("b"), _u("c"), _a("d", error=True)]
    original = list(msgs)
    _strip_trailing_failed_pairs(msgs)
    assert msgs == original, "helper mutated its input"


# ---------------------------------------------------------------------------
# Integration test — chat endpoint persists the cleaned messages
# ---------------------------------------------------------------------------


def _make_idea_with_failed_pair(ideas_dir: Path) -> tuple[str, Path]:
    """Build an idea directory whose session.json ends with a failed pair."""
    idea_id = str(uuid.uuid4())
    idea_dir = ideas_dir / idea_id
    idea_dir.mkdir(parents=True)
    (idea_dir / "turns").mkdir()
    session = {
        "name": "Stacked errors",
        "messages": [
            _u("hi"),
            _a("hello"),
            _u("how can we make it better?"),
            _a("Agent timed out — the model may be slow. You can retry.", error=True),
        ],
        "prd_content": "",
        "roadmap_content": "",
        "annotations": [],
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }
    (idea_dir / "session.json").write_text(json.dumps(session))
    return idea_id, idea_dir


def test_post_message_strips_trailing_failed_pair_from_persisted_session(tmp_path):
    """End-to-end: an idea with a trailing failed pair receives a new POST.
    Once the request is in flight (we intercept just after pre-save), the
    session.json on disk must no longer contain the prior failed pair —
    only the surviving history plus the new user + pending placeholder.

    This is the test that the bug screenshot stops reproducing after refresh.
    """
    ideas_dir = tmp_path / "ideas"
    ideas_dir.mkdir()
    idea_id, idea_dir = _make_idea_with_failed_pair(ideas_dir)
    cfg = {**FAKE_CONFIG, "ideas_dir": str(ideas_dir)}

    captured: dict = {"messages": None}

    async def fake_post(url, **kwargs):
        # Snapshot session.json AFTER the pre-save but BEFORE the poll —
        # this is where the trailing-pair strip is observable.
        captured["messages"] = json.loads(
            (idea_dir / "session.json").read_text()
        )["messages"]
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=b"")
        return mock_resp

    fake_session = MagicMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.post = AsyncMock(side_effect=fake_post)

    # Pre-fab a successful agent response so the poll succeeds quickly.
    (idea_dir / "turns" / "3.md").write_text("Better in many ways.")
    (idea_dir / "turns" / "3.done").write_text("done")

    with patch("ui.server.load_config", return_value=cfg):
        with patch("aiohttp.ClientSession", return_value=fake_session):
            with patch("asyncio.create_task"):
                resp = client.post(
                    f"/api/ideas/{idea_id}/message",
                    json={"content": "actually, how can we improve?", "turn": 3},
                )

    assert resp.status_code == 200, resp.text

    pre_save_messages = captured["messages"]
    assert pre_save_messages is not None, "fake_post never observed pre-save"

    # The prior failed pair (user "how can we make it better?" + error
    # assistant) must be gone.  The new user + pending pair takes its place.
    failed_user_content = "how can we make it better?"
    failed_assistant_content = "Agent timed out — the model may be slow. You can retry."
    contents = [m.get("content") for m in pre_save_messages]

    assert failed_user_content not in contents, (
        f"prior failed user message survived the strip: {contents}"
    )
    assert failed_assistant_content not in contents, (
        f"prior error assistant bubble survived the strip: {contents}"
    )

    # Sanity: the new user message is present.
    assert "actually, how can we improve?" in contents


def test_post_message_preserves_history_when_no_trailing_failure(tmp_path):
    """Idempotency: an idea with a clean history (no trailing failures)
    must have its message list unchanged except for the new user + pending
    pair appended.  Guards against the helper accidentally clipping good
    history.
    """
    ideas_dir = tmp_path / "ideas"
    ideas_dir.mkdir()
    idea_id = str(uuid.uuid4())
    idea_dir = ideas_dir / idea_id
    idea_dir.mkdir()
    (idea_dir / "turns").mkdir()
    clean_msgs = [_u("first"), _a("reply-1"), _u("second"), _a("reply-2")]
    (idea_dir / "session.json").write_text(json.dumps({
        "name": "Clean",
        "messages": clean_msgs,
        "prd_content": "",
        "roadmap_content": "",
        "annotations": [],
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }))
    cfg = {**FAKE_CONFIG, "ideas_dir": str(ideas_dir)}

    captured: dict = {"messages": None}

    async def fake_post(url, **kwargs):
        captured["messages"] = json.loads(
            (idea_dir / "session.json").read_text()
        )["messages"]
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=b"")
        return mock_resp

    fake_session = MagicMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.post = AsyncMock(side_effect=fake_post)

    (idea_dir / "turns" / "3.md").write_text("reply-3")
    (idea_dir / "turns" / "3.done").write_text("done")

    with patch("ui.server.load_config", return_value=cfg):
        with patch("aiohttp.ClientSession", return_value=fake_session):
            with patch("asyncio.create_task"):
                resp = client.post(
                    f"/api/ideas/{idea_id}/message",
                    json={"content": "third", "turn": 3},
                )

    assert resp.status_code == 200
    pre_save_messages = captured["messages"]
    contents = [m.get("content") for m in pre_save_messages]
    # All clean history preserved + new pair appended.
    for original in ("first", "reply-1", "second", "reply-2", "third"):
        assert original in contents, f"clean history lost: {original} missing from {contents}"
