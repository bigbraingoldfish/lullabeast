"""Phase 4 — Ideas chat-turn events in the shared ``pipeline_events.jsonl``.

Turn failures and late recoveries used to live only in the server log, so a
"chat feels flaky" report was reproducible-or-nothing. Each verdict now leaves
one durable line in the SAME events file the activity feed tails, via the same
writer the orchestrator and ``operator_action`` events use (one line format,
one rotation policy):

  * ``ideas_turn_timeout {reason: stalled|timeout|empty_reply}`` — the send
    path's definitive 504 verdicts.
  * ``ideas_turn_late_heal {outcome: reply|empty_reply}`` — GET /session
    resolved the placeholder from a late ``turns/{n}.done``.
  * ``ideas_turn_stranded_md_rescue`` — GET /session recovered a stranded
    ``turns/{n}.md`` after stamp silence.

The roadmap converter (agent ``roadmap-converter``) gets the same trail for
the same failure classes: ``ideas_convert_timeout {op, reason}``,
``ideas_convert_empty_draft {op}`` (op: roadmap_generation |
format_correction, matching the operation metrics), and
``ideas_convert_late_salvage {artifacts}`` when GET /session merges drafts
that landed after the request gave up.

Heals/salvages are write-once per verdict, so re-reads never double-emit.
Fixture patterns mirror ``test_api_operator_action_events.py`` (writer) and
``test_api_ideas_timeout_reason.py`` (send path).
"""
import json
import os
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autodev.pipeline.sentinel_poller import PollResult
from fastapi.testclient import TestClient

from ui.server import (
    IDEAS_EMPTY_REPLY_MESSAGE,
    _reconcile_ideas_session_after_late_done,
    _write_ideas_turn_event,
    app,
)

client = TestClient(app)


def _read_events(events_path):
    p = Path(events_path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _make_idea(ideas_dir: Path, messages=None) -> str:
    idea_id = str(uuid.uuid4())
    d = ideas_dir / idea_id
    (d / "turns").mkdir(parents=True)
    (d / "session.json").write_text(json.dumps({
        "name": "Events test",
        "messages": messages or [],
        "prd_content": "a PRD",
        "roadmap_content": "",
        "verification_content": "v",
        "annotations": [],
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }))
    return idea_id


def _unresolved_turn_messages(attempt_start: float, turn: int = 1):
    return [
        {"role": "user", "content": "hi", "ts": "2026-01-01T00:00:00Z",
         "ideas_turn": turn, "attempt_start_wall": attempt_start},
        {"role": "assistant", "content": "Working on your request...",
         "ts": "2026-01-01T00:00:00Z", "pending": True},
    ]


def _write(p: Path, text: str, mtime: float | None = None):
    p.write_text(text)
    if mtime is not None:
        os.utime(p, (mtime, mtime))


# ── writer unit tests ─────────────────────────────────────────────────────────

def test_writer_emits_canonical_prd_creator_line(tmp_path):
    """One line, same schema as every other pipeline event: agent prd-creator,
    no run binding (ideas are run-independent), idea_id + extras in detail."""
    events_path = tmp_path / "pipeline_events.jsonl"
    cfg = {"events_path": str(events_path)}
    _write_ideas_turn_event(cfg, "ideas_turn_timeout", "idea-1", {"turn": 3, "reason": "stalled"})
    row = _read_events(events_path)[-1]
    assert row["event"] == "ideas_turn_timeout"
    assert row["agent"] == "prd-creator"
    assert row["run_id"] is None
    assert row["detail"] == {"idea_id": "idea-1", "turn": 3, "reason": "stalled"}
    assert row["ts"].endswith("Z")


def test_writer_nonraising_without_events_path():
    """Telemetry must never break an API request: no events_path → silent no-op."""
    _write_ideas_turn_event({}, "ideas_turn_timeout", "idea-1")  # must not raise
    _write_ideas_turn_event(None, "ideas_turn_timeout", "idea-1")  # must not raise


def test_writer_agent_override_for_converter_events(tmp_path):
    events_path = tmp_path / "pipeline_events.jsonl"
    _write_ideas_turn_event(
        {"events_path": str(events_path)}, "ideas_convert_timeout", "idea-1",
        {"op": "roadmap_generation", "reason": "stalled"}, agent="roadmap-converter",
    )
    row = _read_events(events_path)[-1]
    assert row["agent"] == "roadmap-converter"
    assert row["detail"]["op"] == "roadmap_generation"


# ── send path: definitive 504 verdicts emit ideas_turn_timeout ────────────────

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


def _post_message(cfg, idea_id, poll_result):
    with patch("ui.server.load_config", return_value=cfg):
        with patch("aiohttp.ClientSession", return_value=_fake_webhook_session()):
            with patch("asyncio.create_task"):
                with patch(
                    "ui.server._poll_sentinel_with_idle_detect",
                    AsyncMock(return_value=poll_result),
                ):
                    return client.post(
                        f"/api/ideas/{idea_id}/message",
                        json={"content": "go", "turn": 1},
                    )


@pytest.mark.parametrize("reason", ["stalled", "timeout"])
def test_timeout_504_emits_reason_specific_event(tmp_path, reason):
    ideas_dir = tmp_path / "ideas"
    ideas_dir.mkdir()
    idea_id = _make_idea(ideas_dir)
    cfg = {
        "ideas_dir": str(ideas_dir),
        "events_path": str(tmp_path / "pipeline_events.jsonl"),
        "hooks_url": "http://localhost:19999/hooks/agent",
        "hooks_token": "t",
    }
    resp = _post_message(cfg, idea_id, PollResult(False, reason))
    assert resp.status_code == 504, resp.text
    rows = [e for e in _read_events(cfg["events_path"]) if e["event"] == "ideas_turn_timeout"]
    assert len(rows) == 1
    assert rows[0]["detail"] == {"idea_id": idea_id, "turn": 1, "reason": reason}


def test_empty_reply_gate_emits_timeout_event_with_empty_reply_reason(tmp_path):
    """.done observed but no usable turns/{n}.md — the success-path output gate's
    504 must leave the same paper trail, distinguished by reason."""
    ideas_dir = tmp_path / "ideas"
    ideas_dir.mkdir()
    idea_id = _make_idea(ideas_dir)
    cfg = {
        "ideas_dir": str(ideas_dir),
        "events_path": str(tmp_path / "pipeline_events.jsonl"),
        "hooks_url": "http://localhost:19999/hooks/agent",
        "hooks_token": "t",
    }
    resp = _post_message(cfg, idea_id, PollResult(True, "succeeded"))
    assert resp.status_code == 504, resp.text
    assert resp.json()["detail"]["reason"] == "empty_reply"
    rows = [e for e in _read_events(cfg["events_path"]) if e["event"] == "ideas_turn_timeout"]
    assert len(rows) == 1
    assert rows[0]["detail"]["reason"] == "empty_reply"


# ── heal paths: reconciler emits per recovery source, write-once ──────────────

def test_late_done_heal_emits_late_heal_reply_event(tmp_path):
    idea_dir = tmp_path / "idea"
    (idea_dir / "turns").mkdir(parents=True)
    now = time.time()
    _write(idea_dir / "turns" / "1.md", "late reply", now - 5)
    _write(idea_dir / "turns" / "1.done", "done", now - 5)
    cfg = {"events_path": str(tmp_path / "pipeline_events.jsonl")}
    data = {"messages": _unresolved_turn_messages(attempt_start=now - 100)}
    healed, changed = _reconcile_ideas_session_after_late_done(
        idea_dir, data, config=cfg, idea_id="idea-x",
    )
    assert changed is True
    rows = _read_events(cfg["events_path"])
    assert [r["event"] for r in rows] == ["ideas_turn_late_heal"]
    assert rows[0]["detail"] == {"idea_id": "idea-x", "turn": 1, "outcome": "reply"}


def test_late_done_heal_with_blank_md_emits_empty_reply_outcome(tmp_path):
    idea_dir = tmp_path / "idea"
    (idea_dir / "turns").mkdir(parents=True)
    now = time.time()
    _write(idea_dir / "turns" / "1.md", "   ", now - 5)
    _write(idea_dir / "turns" / "1.done", "done", now - 5)
    cfg = {"events_path": str(tmp_path / "pipeline_events.jsonl")}
    data = {"messages": _unresolved_turn_messages(attempt_start=now - 100)}
    healed, changed = _reconcile_ideas_session_after_late_done(
        idea_dir, data, config=cfg, idea_id="idea-x",
    )
    assert changed is True
    assert healed["messages"][-1]["content"] == IDEAS_EMPTY_REPLY_MESSAGE
    rows = _read_events(cfg["events_path"])
    assert rows[0]["event"] == "ideas_turn_late_heal"
    assert rows[0]["detail"]["outcome"] == "empty_reply"
    # Write-once verdict: a re-read must not double-emit.
    _, changed_again = _reconcile_ideas_session_after_late_done(
        idea_dir, healed, config=cfg, idea_id="idea-x",
    )
    assert changed_again is False
    assert len(_read_events(cfg["events_path"])) == 1


def test_stranded_md_rescue_emits_rescue_event(tmp_path):
    idea_dir = tmp_path / "idea"
    (idea_dir / "turns").mkdir(parents=True)
    now = time.time()
    _write(idea_dir / "turns" / "1.md", "stranded reply", now - 400)  # no .done
    _write(idea_dir / "prd_creator_activity.stamp", "", now - 400)   # silent >= quiet_secs
    cfg = {"events_path": str(tmp_path / "pipeline_events.jsonl")}
    data = {"messages": _unresolved_turn_messages(attempt_start=now - 500)}
    _, changed = _reconcile_ideas_session_after_late_done(
        idea_dir, data, quiet_secs=300.0, config=cfg, idea_id="idea-x",
    )
    assert changed is True
    rows = _read_events(cfg["events_path"])
    assert [r["event"] for r in rows] == ["ideas_turn_stranded_md_rescue"]
    assert rows[0]["detail"] == {"idea_id": "idea-x", "turn": 1}


def test_reconciler_without_config_stays_silent_and_heals(tmp_path):
    """The reconciler is also exercised directly by older suites without event
    plumbing — no config means no emission attempt, and the heal still works."""
    idea_dir = tmp_path / "idea"
    (idea_dir / "turns").mkdir(parents=True)
    now = time.time()
    _write(idea_dir / "turns" / "1.md", "late reply", now - 5)
    _write(idea_dir / "turns" / "1.done", "done", now - 5)
    data = {"messages": _unresolved_turn_messages(attempt_start=now - 100)}
    healed, changed = _reconcile_ideas_session_after_late_done(idea_dir, data)
    assert changed is True
    assert healed["messages"][-1]["content"] == "late reply"


# ── roadmap converter: same trail for the same failure classes ───────────────

def _resolved_turn_messages():
    return [
        {"role": "user", "content": "hi", "ts": "2026-01-01T00:00:00Z"},
        {"role": "assistant", "content": "ok", "ts": "2026-01-01T00:00:01Z"},
    ]


def _post_convert(cfg, idea_id, poll_result):
    with patch("ui.server.load_config", return_value=cfg), \
         patch("aiohttp.ClientSession", return_value=_fake_webhook_session()), \
         patch("ui.server._inject_converter_skill"), \
         patch(
             "ui.server._poll_sentinel_with_idle_detect",
             AsyncMock(return_value=poll_result),
         ):
        return client.post(f"/api/ideas/{idea_id}/convert")


def test_convert_timeout_emits_converter_event(tmp_path):
    ideas_dir = tmp_path / "ideas"
    ideas_dir.mkdir()
    idea_id = _make_idea(ideas_dir, messages=_resolved_turn_messages())
    cfg = {
        "ideas_dir": str(ideas_dir),
        "events_path": str(tmp_path / "pipeline_events.jsonl"),
        "hooks_url": "http://localhost:19999/hooks/agent",
        "hooks_token": "t",
    }
    resp = _post_convert(cfg, idea_id, PollResult(False, "stalled"))
    assert resp.status_code == 504, resp.text
    rows = [e for e in _read_events(cfg["events_path"]) if e["event"] == "ideas_convert_timeout"]
    assert len(rows) == 1
    assert rows[0]["agent"] == "roadmap-converter"
    assert rows[0]["detail"] == {"idea_id": idea_id, "op": "roadmap_generation", "reason": "stalled"}


def test_convert_empty_draft_emits_converter_event(tmp_path):
    """Sentinels landed but the drafts are empty/missing — the content gate's 502
    must be visible in the events file, not only in the server log."""
    ideas_dir = tmp_path / "ideas"
    ideas_dir.mkdir()
    idea_id = _make_idea(ideas_dir, messages=_resolved_turn_messages())
    cfg = {
        "ideas_dir": str(ideas_dir),
        "events_path": str(tmp_path / "pipeline_events.jsonl"),
        "hooks_url": "http://localhost:19999/hooks/agent",
        "hooks_token": "t",
    }
    resp = _post_convert(cfg, idea_id, PollResult(True, "succeeded"))
    assert resp.status_code == 502, resp.text
    rows = [e for e in _read_events(cfg["events_path"]) if e["event"] == "ideas_convert_empty_draft"]
    assert len(rows) == 1
    assert rows[0]["detail"] == {"idea_id": idea_id, "op": "roadmap_generation"}


def test_session_get_late_salvage_emits_event_once(tmp_path):
    """Drafts that land after /convert gave up are merged by GET /session —
    that salvage leaves one event, and only one (merges are idempotent)."""
    ideas_dir = tmp_path / "ideas"
    ideas_dir.mkdir()
    idea_id = _make_idea(ideas_dir, messages=_resolved_turn_messages())
    idea_dir = ideas_dir / idea_id
    now = time.time()
    _write(idea_dir / "roadmap_draft.md", "# Late roadmap", now - 5)
    _write(idea_dir / "roadmap_draft.done", "done", now - 5)
    cfg = {
        "ideas_dir": str(ideas_dir),
        "events_path": str(tmp_path / "pipeline_events.jsonl"),
    }
    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get(f"/api/ideas/{idea_id}/session")
        assert resp.status_code == 200
        assert resp.json()["roadmap_content"] == "# Late roadmap"
        client.get(f"/api/ideas/{idea_id}/session")  # re-read: no double-emit
    rows = [e for e in _read_events(cfg["events_path"]) if e["event"] == "ideas_convert_late_salvage"]
    assert len(rows) == 1
    assert rows[0]["agent"] == "roadmap-converter"
    assert rows[0]["detail"] == {"idea_id": idea_id, "artifacts": ["roadmap"]}


# ── live endpoint wiring: GET /session passes the event plumbing through ──────

def test_session_get_late_heal_emits_event_end_to_end(tmp_path):
    ideas_dir = tmp_path / "ideas"
    ideas_dir.mkdir()
    now = time.time()
    idea_id = _make_idea(ideas_dir, messages=_unresolved_turn_messages(now - 100))
    idea_dir = ideas_dir / idea_id
    _write(idea_dir / "turns" / "1.md", "late reply", now - 5)
    _write(idea_dir / "turns" / "1.done", "done", now - 5)
    cfg = {
        "ideas_dir": str(ideas_dir),
        "events_path": str(tmp_path / "pipeline_events.jsonl"),
    }
    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get(f"/api/ideas/{idea_id}/session")
    assert resp.status_code == 200
    assert resp.json()["messages"][-1]["content"] == "late reply"
    rows = [e for e in _read_events(cfg["events_path"]) if e["event"] == "ideas_turn_late_heal"]
    assert len(rows) == 1
    assert rows[0]["detail"]["idea_id"] == idea_id
