"""W3-A: Queue snapshot endpoint bug fixes.

Fixes:
1. Realpath mismatch (trailing slash, symlink) causes is_active_project=False
   for entries that are genuinely the active project.
2. `started_at` missing from the snapshot return dict.
3. ACTIVE entry with path mismatch should still return roadmap-derived fields.
"""
import json
import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from ui.server import app

client = TestClient(app)

ROADMAP_CONTENT = (
    "- [x] `CORE-E1` | LOW | Phase one\n  > Test: ok.\n"
    "- [ ] `CORE-E2` | MEDIUM | Phase two\n  > Test: ok.\n"
    "- [ ] `CORE-E3` | HIGH | Phase three\n  > Test: ok.\n"
)


def _make_entry(proj_path, state="ACTIVE", entry_id=None):
    return {
        "id": entry_id or str(uuid.uuid4()),
        "project_path": str(proj_path),
        "idea_id": None,
        "name": "test-project",
        "state": state,
        "position": 1,
        "parent_id": None,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "started_at": "2026-04-30T08:00:00Z",
        "completed_at": None,
        "blocked_at": None,
        "skip_count": 0,
        "preflight_validated_at": datetime.now(timezone.utc).isoformat(),
        "notes": "",
    }


def _make_cfg(tmp_path, proj_path):
    queue_file = tmp_path / "pipeline_queue.json"
    ps_file = tmp_path / "pipeline_state.json"
    return {
        "pipeline_state_path": str(ps_file),
        "phase_state_path": str(tmp_path / "phase_state.json"),
        "lock_path": str(tmp_path / "pipeline.lock"),
        "events_path": str(tmp_path / "pipeline_events.jsonl"),
        "project_dir_path": str(proj_path),
        "pipeline_queue_path": str(queue_file),
        "autodev_pipeline_root": str(tmp_path),
    }


def _write_queue(cfg, entries):
    path = cfg["pipeline_queue_path"]
    data = {"queue": entries, "queue_mode": "auto", "last_updated": datetime.now(timezone.utc).isoformat()}
    with open(path, "w") as f:
        json.dump(data, f)


def _write_pipeline_state(cfg, project_path, status="WAITING_FOR_HUMAN"):
    with open(cfg["pipeline_state_path"], "w") as f:
        json.dump({
            "project_path": str(project_path),
            "pipeline_status": status,
            "current_phase": 2,
            "current_phase_raw_id": "T2",
            "current_agent": "executor",
            "executor_retries": 1,
            "planner_retries": 0,
            "reviewer_retries": 0,
            "last_action_timestamp": "2026-04-30T09:00:00Z",
        }, f)


# ---------------------------------------------------------------------------
# Path normalisation — trailing slash
# ---------------------------------------------------------------------------

def test_snapshot_active_when_pipeline_state_has_trailing_slash(tmp_path):
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_CONTENT)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(str(proj), entry_id=eid)])
    # Orchestrator stored path with trailing slash
    with open(cfg["pipeline_state_path"], "w") as f:
        json.dump({
            "project_path": str(proj) + "/",  # trailing slash
            "pipeline_status": "WAITING_FOR_HUMAN",
            "current_phase": 2,
            "current_phase_raw_id": "T2",
            "current_agent": "executor",
            "executor_retries": 1,
            "planner_retries": 0,
            "reviewer_retries": 0,
        }, f)

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=False):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active_project"] is True
    assert data["pipeline_status"] == "WAITING_FOR_HUMAN"
    assert data["current_agent"] == "executor"


def test_snapshot_active_when_entry_has_trailing_slash(tmp_path):
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_CONTENT)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    entry = _make_entry(str(proj) + "/", entry_id=eid)  # entry has trailing slash
    _write_queue(cfg, [entry])
    _write_pipeline_state(cfg, proj)  # ps has clean path

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=False):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    assert resp.json()["is_active_project"] is True


def test_snapshot_active_when_symlink_used_in_entry(tmp_path):
    real_proj = tmp_path / "real_proj"
    real_proj.mkdir()
    (real_proj / "roadmap.md").write_text(ROADMAP_CONTENT)
    symlink_proj = tmp_path / "sym_proj"
    os.symlink(str(real_proj), str(symlink_proj))

    cfg = _make_cfg(tmp_path, real_proj)
    eid = str(uuid.uuid4())
    entry = _make_entry(str(symlink_proj), entry_id=eid)  # entry uses symlink path
    _write_queue(cfg, [entry])
    _write_pipeline_state(cfg, real_proj)  # ps uses real path

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=False):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    assert resp.json()["is_active_project"] is True


# ---------------------------------------------------------------------------
# started_at always returned from queue entry
# ---------------------------------------------------------------------------

def test_snapshot_returns_started_at_from_queue_entry(tmp_path):
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_CONTENT)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(str(proj), entry_id=eid)])
    # Intentionally do NOT write pipeline_state — is_active_project will be False
    # (pipeline_state.json missing means no match)

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=False):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    assert resp.json()["started_at"] == "2026-04-30T08:00:00Z"


def test_snapshot_returns_started_at_null_when_entry_has_none(tmp_path):
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_CONTENT)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    entry = _make_entry(str(proj), entry_id=eid)
    entry["started_at"] = None
    _write_queue(cfg, [entry])

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=False):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    assert resp.json()["started_at"] is None


def test_snapshot_returns_started_at_even_when_path_mismatch(tmp_path):
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_CONTENT)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(str(proj), entry_id=eid)])
    # Write pipeline_state with a DIFFERENT project so is_active_project=False
    other_proj = tmp_path / "other_proj"
    other_proj.mkdir()
    _write_pipeline_state(cfg, other_proj)

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=False):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active_project"] is False
    # started_at should still be present from queue entry
    assert data["started_at"] == "2026-04-30T08:00:00Z"


# ---------------------------------------------------------------------------
# ACTIVE entry with path mismatch still returns roadmap-derived fields
# ---------------------------------------------------------------------------

def test_snapshot_active_entry_returns_phases_total_despite_path_mismatch(tmp_path):
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_CONTENT)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(str(proj), entry_id=eid)])
    # Different project in pipeline_state → mismatch
    other = tmp_path / "other"
    other.mkdir()
    _write_pipeline_state(cfg, other)

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=False):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active_project"] is False
    assert data["phases_total"] == 3
    assert data["phases_complete"] == 1  # one [x] in ROADMAP_CONTENT


# ---------------------------------------------------------------------------
# Regression: pipeline_state fields still suppressed for non-active projects
# ---------------------------------------------------------------------------

def test_snapshot_suppresses_pipeline_state_fields_when_not_active(tmp_path):
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_CONTENT)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(str(proj), entry_id=eid)])
    # Different project active
    other = tmp_path / "other"
    other.mkdir()
    _write_pipeline_state(cfg, other, status="RUNNING")

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=False):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active_project"] is False
    assert data["pipeline_status"] is None
    assert data["current_agent"] is None
    assert data["executor_retries"] is None
    assert data["planner_retries"] is None


def test_snapshot_returns_all_pipeline_state_fields_when_active(tmp_path):
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_CONTENT)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(str(proj), entry_id=eid)])
    _write_pipeline_state(cfg, proj, status="RUNNING")

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=False):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active_project"] is True
    assert data["pipeline_status"] == "RUNNING"
    assert data["current_agent"] == "executor"
    assert data["executor_retries"] == 1


def test_snapshot_integration_active_project_started_at_and_phases(tmp_path):
    """W3-A integration: ACTIVE + matching pipeline_state → is_active, started_at, roadmap counts."""
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_CONTENT)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    started = "2026-04-30T08:15:00Z"
    entry = _make_entry(str(proj), entry_id=eid)
    entry["started_at"] = started
    _write_queue(cfg, [entry])
    _write_pipeline_state(cfg, proj, status="RUNNING")

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=True):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active_project"] is True
    assert data["started_at"] == started
    assert data["state"] == "ACTIVE"
    assert data["phases_total"] == 3
    assert data["phases_complete"] == 1
    assert data["pipeline_status"] == "RUNNING"
