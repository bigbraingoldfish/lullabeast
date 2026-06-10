"""W3-B (extended by the queue redesign): phases_complete and phases_total on
EVERY entry in GET /api/queue.

Originally W3-B enriched ACTIVE entries only; the flat-table queue screen
renders 0/N progress on queued rows too, so the uniform summary block computes
the counts for all states. Tests verify:
- Any entry gets phases_total and phases_complete when roadmap.md is present
- Keys are present-but-None when the roadmap is missing or unreadable
- phases_total == 0 for an empty roadmap (file exists, no phase lines)
"""
import json
import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from ui.server import app, _roadmap_phase_checkbox_stats

client = TestClient(app)

ROADMAP_4_PHASES_2_DONE = (
    "- [x] `CORE-E1` | LOW | Phase one\n  > Test: ok.\n"
    "- [x] `CORE-E2` | MEDIUM | Phase two\n  > Test: ok.\n"
    "- [ ] `CORE-E3` | HIGH | Phase three\n  > Test: ok.\n"
    "- [ ] `CORE-E4` | CRITICAL | Phase four\n  > Test: ok.\n"
)

ROADMAP_EMPTY = ""


def _make_entry(proj_path, state="ACTIVE", entry_id=None, position=1):
    return {
        "id": entry_id or str(uuid.uuid4()),
        "project_path": str(proj_path),
        "idea_id": None,
        "name": "test-project",
        "state": state,
        "position": position,
        "parent_id": None,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "started_at": "2026-04-30T08:00:00Z" if state == "ACTIVE" else None,
        "completed_at": None,
        "blocked_at": None,
        "skip_count": 0,
        "preflight_validated_at": datetime.now(timezone.utc).isoformat(),
        "notes": "",
    }


def _make_cfg(tmp_path):
    queue_file = tmp_path / "pipeline_queue.json"
    ps_file = tmp_path / "pipeline_state.json"
    return {
        "pipeline_state_path": str(ps_file),
        "phase_state_path": str(tmp_path / "phase_state.json"),
        "lock_path": str(tmp_path / "pipeline.lock"),
        "events_path": str(tmp_path / "pipeline_events.jsonl"),
        "project_dir_path": str(tmp_path / "proj"),
        "pipeline_queue_path": str(queue_file),
        "autodev_pipeline_root": str(tmp_path),
    }


def _write_queue(cfg, entries, ps=None):
    queue_path = cfg["pipeline_queue_path"]
    data = {
        "queue": entries,
        "queue_mode": "auto",
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    with open(queue_path, "w") as f:
        json.dump(data, f)

    if ps:
        with open(cfg["pipeline_state_path"], "w") as f:
            json.dump(ps, f)


def _entry_by_id(queue_json, eid):
    return next((e for e in queue_json["queue"] if e["id"] == eid), None)


# ---------------------------------------------------------------------------
# Core: ACTIVE entry gets enriched
# ---------------------------------------------------------------------------

def test_active_entry_gets_phases_total_and_phases_complete(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_4_PHASES_2_DONE)

    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    entry = _make_entry(proj, state="ACTIVE", entry_id=eid)
    ps = {"project_path": str(proj), "pipeline_status": "RUNNING", "current_agent": "executor"}
    _write_queue(cfg, [entry], ps=ps)

    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/queue")

    assert resp.status_code == 200
    data = resp.json()
    found = _entry_by_id(data, eid)
    assert found is not None
    assert found["phases_total"] == 4
    assert found["phases_complete"] == 2


def test_active_entry_with_all_phases_done(tmp_path):
    roadmap = (
        "- [x] `CORE-E1` | LOW | Phase one\n  > Test: ok.\n"
        "- [x] `CORE-E2` | MEDIUM | Phase two\n  > Test: ok.\n"
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(roadmap)

    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    entry = _make_entry(proj, state="ACTIVE", entry_id=eid)
    ps = {"project_path": str(proj), "pipeline_status": "PIPELINE_COMPLETE"}
    _write_queue(cfg, [entry], ps=ps)

    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/queue")

    assert resp.status_code == 200
    found = _entry_by_id(resp.json(), eid)
    assert found["phases_total"] == 2
    assert found["phases_complete"] == 2


# ---------------------------------------------------------------------------
# Non-ACTIVE entries: enriched too (queue redesign — uniform summary block)
# ---------------------------------------------------------------------------

def test_ready_entry_gets_phases_fields(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_4_PHASES_2_DONE)

    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    entry = _make_entry(proj, state="READY", entry_id=eid)
    _write_queue(cfg, [entry])

    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/queue")

    assert resp.status_code == 200
    found = _entry_by_id(resp.json(), eid)
    assert found is not None
    assert found["phases_total"] == 4
    assert found["phases_complete"] == 2


def test_blocked_entry_gets_phases_fields(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_4_PHASES_2_DONE)

    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    entry = _make_entry(proj, state="BLOCKED", entry_id=eid)
    _write_queue(cfg, [entry])

    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/queue")

    assert resp.status_code == 200
    found = _entry_by_id(resp.json(), eid)
    assert found["phases_total"] == 4
    assert found["phases_complete"] == 2


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

def test_active_entry_with_no_roadmap_file(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    # Intentionally no roadmap.md

    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    entry = _make_entry(proj, state="ACTIVE", entry_id=eid)
    ps = {"project_path": str(proj), "pipeline_status": "RUNNING"}
    _write_queue(cfg, [entry], ps=ps)

    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/queue")

    assert resp.status_code == 200
    found = _entry_by_id(resp.json(), eid)
    assert found is not None
    # Uniform summary block: keys present, values None — graceful skip, no crash
    assert found["phases_total"] is None
    assert found["phases_complete"] is None


def test_active_entry_with_empty_roadmap(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_EMPTY)

    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    entry = _make_entry(proj, state="ACTIVE", entry_id=eid)
    ps = {"project_path": str(proj), "pipeline_status": "RUNNING"}
    _write_queue(cfg, [entry], ps=ps)

    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/queue")

    assert resp.status_code == 200
    found = _entry_by_id(resp.json(), eid)
    # Empty roadmap: phases_total == 0, should not crash
    assert found["phases_total"] == 0
    assert found["phases_complete"] == 0


def test_active_entry_with_empty_project_path_does_not_crash(tmp_path):
    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    entry = _make_entry("", state="ACTIVE", entry_id=eid)
    entry["project_path"] = ""
    _write_queue(cfg, [entry])

    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/queue")

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Multiple entries: every row is enriched (uniform summary block)
# ---------------------------------------------------------------------------

def test_all_entries_enriched_in_mixed_queue(tmp_path):
    active_proj = tmp_path / "active_proj"
    active_proj.mkdir()
    (active_proj / "roadmap.md").write_text(ROADMAP_4_PHASES_2_DONE)

    ready_proj = tmp_path / "ready_proj"
    ready_proj.mkdir()
    (ready_proj / "roadmap.md").write_text(ROADMAP_4_PHASES_2_DONE)

    cfg = _make_cfg(tmp_path)
    active_id = str(uuid.uuid4())
    ready_id = str(uuid.uuid4())

    active_entry = _make_entry(active_proj, state="ACTIVE", entry_id=active_id, position=1)
    ready_entry = _make_entry(ready_proj, state="READY", entry_id=ready_id, position=2)
    ps = {"project_path": str(active_proj), "pipeline_status": "RUNNING"}
    _write_queue(cfg, [active_entry, ready_entry], ps=ps)

    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/queue")

    assert resp.status_code == 200
    queue = resp.json()["queue"]
    active = next(e for e in queue if e["id"] == active_id)
    ready = next(e for e in queue if e["id"] == ready_id)

    assert active["phases_total"] == 4
    assert active["phases_complete"] == 2
    assert ready["phases_total"] == 4
    assert ready["phases_complete"] == 2


def test_active_queue_phases_match_roadmap_checkbox_stats(tmp_path):
    """W3-B: GET /api/queue counts match _roadmap_phase_checkbox_stats on the same file."""
    proj = tmp_path / "proj"
    proj.mkdir()
    roadmap_path = proj / "roadmap.md"
    roadmap_path.write_text(ROADMAP_4_PHASES_2_DONE)

    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    entry = _make_entry(proj, state="ACTIVE", entry_id=eid)
    ps = {"project_path": str(proj), "pipeline_status": "RUNNING", "current_agent": "executor"}
    _write_queue(cfg, [entry], ps=ps)

    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/queue")

    assert resp.status_code == 200
    found = _entry_by_id(resp.json(), eid)
    with open(roadmap_path, "r", errors="replace") as f:
        expected_total, expected_complete = _roadmap_phase_checkbox_stats(f.read())
    assert found["phases_total"] == expected_total
    assert found["phases_complete"] == expected_complete


def test_active_queue_phases_refresh_after_roadmap_edit(tmp_path):
    """W3-B: No stale cache — second GET reflects roadmap edits on disk."""
    proj = tmp_path / "proj"
    proj.mkdir()
    roadmap_path = proj / "roadmap.md"
    roadmap_path.write_text(ROADMAP_4_PHASES_2_DONE)

    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    entry = _make_entry(proj, state="ACTIVE", entry_id=eid)
    ps = {"project_path": str(proj), "pipeline_status": "RUNNING"}
    _write_queue(cfg, [entry], ps=ps)

    with patch("ui.server.load_config", return_value=cfg):
        r1 = client.get("/api/queue")
    assert _entry_by_id(r1.json(), eid)["phases_complete"] == 2

    updated = ROADMAP_4_PHASES_2_DONE.replace("- [ ] `CORE-E3`", "- [x] `CORE-E3`", 1)
    roadmap_path.write_text(updated)

    with patch("ui.server.load_config", return_value=cfg):
        r2 = client.get("/api/queue")
    assert _entry_by_id(r2.json(), eid)["phases_complete"] == 3
