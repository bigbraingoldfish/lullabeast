"""GET /api/ideas/{id}/turn-status — read-only agent-liveness snapshot.

The chat twin of /api/state's ``agent_activity_age_seconds`` (ideas-chat-
robustness Phase 2). Reports the ``prd_creator_activity.stamp`` age — the same
mtime the idle-detection poll watches — plus the turn's artifact presence, with
``state`` derived exactly like the poller: ``done`` / ``working`` / ``quiet``
past ``ideas_idle_threshold`` / ``waiting`` before any stamp exists.
"""
import json
import os
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ui.server import app

client = TestClient(app)


def _make_idea(ideas_dir: Path) -> Path:
    idea_dir = ideas_dir / str(uuid.uuid4())
    (idea_dir / "turns").mkdir(parents=True)
    return idea_dir


def _cfg(ideas_dir: Path, threshold: float = 300.0) -> dict:
    return {"ideas_dir": str(ideas_dir), "ideas_idle_threshold": threshold}


def _get(idea_dir: Path, threshold: float = 300.0, turn=None):
    q = f"?turn={turn}" if turn is not None else ""
    with patch("ui.server.load_config", return_value=_cfg(idea_dir.parent, threshold)):
        return client.get(f"/api/ideas/{idea_dir.name}/turn-status{q}")


def test_unknown_idea_is_404(tmp_path):
    with patch("ui.server.load_config", return_value=_cfg(tmp_path)):
        resp = client.get("/api/ideas/no-such-idea/turn-status")
    assert resp.status_code == 404


def test_no_stamp_reports_waiting_with_null_age(tmp_path):
    idea_dir = _make_idea(tmp_path)
    resp = _get(idea_dir)
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "waiting"
    assert body["stamp_age_seconds"] is None


def test_fresh_stamp_reports_working_with_age(tmp_path):
    idea_dir = _make_idea(tmp_path)
    (idea_dir / "prd_creator_activity.stamp").write_text("")
    resp = _get(idea_dir)
    body = resp.json()
    assert body["state"] == "working"
    assert 0 <= body["stamp_age_seconds"] < 30


def test_stamp_silent_past_threshold_reports_quiet(tmp_path):
    idea_dir = _make_idea(tmp_path)
    stamp = idea_dir / "prd_creator_activity.stamp"
    stamp.write_text("")
    old = time.time() - 120
    os.utime(stamp, (old, old))
    resp = _get(idea_dir, threshold=60.0)
    body = resp.json()
    assert body["state"] == "quiet"
    assert body["stamp_age_seconds"] >= 60
    # The threshold is surfaced so the UI ambers on the same knob the poller uses.
    assert body["idle_threshold_seconds"] == 60.0


def test_done_sentinel_wins_over_stamp_age(tmp_path):
    idea_dir = _make_idea(tmp_path)
    (idea_dir / "turns" / "3.done").write_text("done")
    (idea_dir / "turns" / "3.md").write_text("reply")
    resp = _get(idea_dir, turn=3)
    body = resp.json()
    assert body["state"] == "done"
    assert body["done_present"] is True
    assert body["md_present"] is True


def test_artifact_presence_requires_the_turn_param(tmp_path):
    idea_dir = _make_idea(tmp_path)
    (idea_dir / "turns" / "1.done").write_text("done")
    body = _get(idea_dir).json()  # no ?turn → artifact flags stay False
    assert body["done_present"] is False
    assert body["md_present"] is False
    assert body["state"] == "waiting"


def test_read_only_no_side_effects_and_no_store(tmp_path):
    idea_dir = _make_idea(tmp_path)
    session_path = idea_dir / "session.json"
    session_path.write_text(json.dumps({"messages": []}))
    before = session_path.read_text()
    resp = _get(idea_dir, turn=1)
    assert resp.headers.get("cache-control") == "no-store"
    assert session_path.read_text() == before
    assert set(os.listdir(idea_dir)) == {"turns", "session.json"}
