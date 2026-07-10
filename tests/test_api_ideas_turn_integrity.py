"""Turn integrity for the Ideas chat (ideas-chat-robustness Phase 3).

Two contracts:

1. Fresh OpenClaw session per attempt — a retry of turn *n* posts the webhook
   under ``ideas:{id}:session-{n}-r{k}`` instead of resuming the (possibly
   still-streaming) prior attempt's session, mirroring the pipeline's
   per-attempt session keys. The message's ``[SESSION]`` first line stays bare:
   it carries the output-path contract (idea id + turn), identical for every
   attempt, so the agent-side path parsing is untouched.

2. Success-path output gate — a ``.done`` without a usable ``turns/{n}.md`` is
   a failed turn, not a blank bubble. Both resolution paths (the held POST and
   the GET /session late-heal reconciler) give the same single-source verdict
   (``IDEAS_EMPTY_REPLY_MESSAGE``); no auto-retry, the user retries.
"""
import json
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from ui.server import (
    IDEAS_EMPTY_REPLY_MESSAGE,
    _ideas_timeout_message,
    _reconcile_ideas_session_after_late_done,
    app,
)

client = TestClient(app)


def _make_idea(ideas_dir: Path) -> str:
    idea_id = str(uuid.uuid4())
    d = ideas_dir / idea_id
    (d / "turns").mkdir(parents=True)
    (d / "session.json").write_text(json.dumps({
        "name": "Integrity test",
        "messages": [],
        "prd_content": "",
        "annotations": [],
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }))
    return idea_id


def _cfg(ideas_dir: Path) -> dict:
    return {
        "ideas_dir": str(ideas_dir),
        "hooks_url": "http://localhost:19999/hooks/agent",
        "hooks_token": "test-token",
    }


def _capturing_session(payloads: list):
    """aiohttp.ClientSession mock that records every webhook JSON payload."""
    def capture_post(url, **kwargs):
        payloads.append(kwargs.get("json") or {})
        resp = MagicMock()
        resp.status = 200
        resp.read = AsyncMock(return_value=b"")
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=None)
        return resp

    session = MagicMock()
    session.post = AsyncMock(side_effect=capture_post)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


def _post_turn(ideas_dir: Path, idea_id: str, turn: int, payloads: list):
    with patch("ui.server.load_config", return_value=_cfg(ideas_dir)):
        with patch("ui.server.aiohttp.ClientSession", return_value=_capturing_session(payloads)):
            with patch("asyncio.create_task"):
                return client.post(
                    f"/api/ideas/{idea_id}/message",
                    json={"content": "go", "turn": turn},
                )


def _write_turn_files(ideas_dir: Path, idea_id: str, turn: int, md_text):
    turns = ideas_dir / idea_id / "turns"
    if md_text is not None:
        (turns / f"{turn}.md").write_text(md_text)
    (turns / f"{turn}.done").write_text("done")


# ── 1. fresh session key per attempt ─────────────────────────────────────────

def test_retry_of_same_turn_gets_fresh_suffixed_session_key(tmp_path):
    idea_id = _make_idea(tmp_path)
    payloads: list = []

    _write_turn_files(tmp_path, idea_id, 1, "reply one")
    assert _post_turn(tmp_path, idea_id, 1, payloads).status_code == 200

    _write_turn_files(tmp_path, idea_id, 1, "reply two")
    assert _post_turn(tmp_path, idea_id, 1, payloads).status_code == 200

    _write_turn_files(tmp_path, idea_id, 1, "reply three")
    assert _post_turn(tmp_path, idea_id, 1, payloads).status_code == 200

    keys = [p["sessionKey"] for p in payloads]
    assert keys[0] == f"ideas:{idea_id}:session-1"
    assert keys[1] == f"ideas:{idea_id}:session-1-r1"
    assert keys[2] == f"ideas:{idea_id}:session-1-r2"


def test_session_line_in_message_stays_bare_across_attempts(tmp_path):
    """The [SESSION] first line is the agent's output-path contract — it must
    name the same turn on every attempt (no -r suffix)."""
    idea_id = _make_idea(tmp_path)
    payloads: list = []
    for _ in range(2):
        _write_turn_files(tmp_path, idea_id, 2, "reply")
        _post_turn(tmp_path, idea_id, 2, payloads)
    for p in payloads:
        assert p["message"].startswith(f"[SESSION] ideas:{idea_id}:session-2\n")


def test_attempt_counter_is_persisted_per_turn(tmp_path):
    """The counter lives in session.json (survives client refreshes), keyed by
    turn number, and is pre-saved before the webhook fires."""
    idea_id = _make_idea(tmp_path)
    payloads: list = []
    _write_turn_files(tmp_path, idea_id, 1, "reply")
    _post_turn(tmp_path, idea_id, 1, payloads)
    _write_turn_files(tmp_path, idea_id, 1, "reply")
    _post_turn(tmp_path, idea_id, 1, payloads)

    session = json.loads((tmp_path / idea_id / "session.json").read_text())
    assert session.get("turn_attempts", {}).get("1") == 2


def test_distinct_turns_each_start_on_the_bare_key(tmp_path):
    idea_id = _make_idea(tmp_path)
    payloads: list = []
    _write_turn_files(tmp_path, idea_id, 1, "reply")
    _post_turn(tmp_path, idea_id, 1, payloads)
    _write_turn_files(tmp_path, idea_id, 2, "reply")
    _post_turn(tmp_path, idea_id, 2, payloads)
    keys = [p["sessionKey"] for p in payloads]
    assert keys == [
        f"ideas:{idea_id}:session-1",
        f"ideas:{idea_id}:session-2",
    ]


# ── 2. success-path output gate ──────────────────────────────────────────────

def test_done_without_md_yields_empty_reply_verdict_not_blank_success(tmp_path):
    idea_id = _make_idea(tmp_path)
    payloads: list = []
    _write_turn_files(tmp_path, idea_id, 1, None)  # .done, no .md

    resp = _post_turn(tmp_path, idea_id, 1, payloads)

    # 504, not 408 — browsers transparently re-POST on 408 (phantom retries).
    assert resp.status_code == 504, resp.text
    detail = resp.json()["detail"]
    assert detail["reason"] == "empty_reply"
    assert detail["message"] == IDEAS_EMPTY_REPLY_MESSAGE

    # The bad sentinel is consumed so a fast retry can't trip over it inside
    # the mtime slack window (instant-fail cascade observed live).
    assert not (tmp_path / idea_id / "turns" / "1.done").exists()

    # Persisted placeholder matches the response body (single source).
    session = json.loads((tmp_path / idea_id / "session.json").read_text())
    last = session["messages"][-1]
    assert last["role"] == "assistant"
    assert last.get("error") is True
    assert last.get("pending") in (False, None)
    assert last["content"] == IDEAS_EMPTY_REPLY_MESSAGE


def test_whitespace_only_md_is_also_an_empty_reply(tmp_path):
    idea_id = _make_idea(tmp_path)
    payloads: list = []
    _write_turn_files(tmp_path, idea_id, 1, "  \n\t\n")

    resp = _post_turn(tmp_path, idea_id, 1, payloads)
    assert resp.status_code == 504
    assert resp.json()["detail"]["reason"] == "empty_reply"


def test_empty_reply_reason_maps_to_the_single_source_string():
    assert _ideas_timeout_message("empty_reply", 900.0) == IDEAS_EMPTY_REPLY_MESSAGE
    assert "—" not in IDEAS_EMPTY_REPLY_MESSAGE  # UI copy standard: no em dashes


# ── 3. the late-heal reconciler gives the same verdict ───────────────────────

def _pending_session(turn: int, attempt_start: float) -> dict:
    return {
        "messages": [
            {"role": "user", "content": "go", "ts": "2026-01-01T00:00:00Z",
             "ideas_turn": turn, "attempt_start_wall": attempt_start},
            {"role": "assistant", "content": "Working on your request...",
             "ts": "2026-01-01T00:00:00Z", "pending": True},
        ],
        "name": "x",
    }


def test_reconcile_resolves_done_without_md_to_error_verdict(tmp_path):
    idea_dir = tmp_path / "idea"
    (idea_dir / "turns").mkdir(parents=True)
    attempt = time.time() - 30
    (idea_dir / "turns" / "1.done").write_text("done")

    data, changed = _reconcile_ideas_session_after_late_done(
        idea_dir, _pending_session(1, attempt), quiet_secs=300.0
    )
    assert changed is True
    last = data["messages"][-1]
    assert last["error"] is True
    assert last["pending"] is False
    assert last["content"] == IDEAS_EMPTY_REPLY_MESSAGE

    # Idempotent: a second reconcile of the same verdict is a no-op, so
    # GET /session doesn't rewrite session.json on every read.
    data2, changed2 = _reconcile_ideas_session_after_late_done(
        idea_dir, data, quiet_secs=300.0
    )
    assert changed2 is False
    assert data2["messages"][-1]["content"] == IDEAS_EMPTY_REPLY_MESSAGE


def test_reconcile_still_heals_to_success_when_md_has_content(tmp_path):
    idea_dir = tmp_path / "idea"
    (idea_dir / "turns").mkdir(parents=True)
    attempt = time.time() - 30
    (idea_dir / "turns" / "1.md").write_text("Real reply")
    (idea_dir / "turns" / "1.done").write_text("done")

    data, changed = _reconcile_ideas_session_after_late_done(
        idea_dir, _pending_session(1, attempt), quiet_secs=300.0
    )
    assert changed is True
    last = data["messages"][-1]
    assert last["content"] == "Real reply"
    assert last.get("error") is False


def test_reconcile_upgrades_empty_verdict_when_md_lands_later(tmp_path):
    """If the reply .md arrives after the empty verdict (e.g. a very late
    write), the next reconcile heals the error row into the real reply."""
    idea_dir = tmp_path / "idea"
    (idea_dir / "turns").mkdir(parents=True)
    attempt = time.time() - 30
    (idea_dir / "turns" / "1.done").write_text("done")

    data, _ = _reconcile_ideas_session_after_late_done(
        idea_dir, _pending_session(1, attempt), quiet_secs=300.0
    )
    (idea_dir / "turns" / "1.md").write_text("Late but real")
    data2, changed2 = _reconcile_ideas_session_after_late_done(
        idea_dir, data, quiet_secs=300.0
    )
    assert changed2 is True
    assert data2["messages"][-1]["content"] == "Late but real"
    assert data2["messages"][-1]["error"] is False
