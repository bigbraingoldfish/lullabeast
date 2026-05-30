"""W5: Queue snapshot detail pane redesign.

Tests for:
1. Snapshot endpoint returns full (non-truncated) phase description
2. Snapshot endpoint returns new phase_state fields (last_error_code,
   escalation_message, escalation_trigger_reason, skill_injected,
   skill_agent, waiting_for_human_at)
3. Snapshot endpoint returns last_action and sentinel_wait_started_at
   from pipeline_state.json
4. New fields are null/absent when project is not active
5. New fields gracefully handle missing phase_state.json
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

ROADMAP_LONG_DESC = (
    "- [x] `CORE-E1` | LOW | Phase one short\n  > Test: ok.\n"
    "- [ ] `DATA-E2` | MEDIUM | Implement IndexedDB statistics and personal best leaderboard with persistent storage across browser sessions\n  > Test: ok.\n"
    "- [ ] `CORE-E3` | HIGH | Phase three\n  > Test: ok.\n"
)

LONG_PHASE_DESC = "Implement IndexedDB statistics and personal best leaderboard with persistent storage across browser sessions"


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


def _write_pipeline_state(cfg, project_path, **overrides):
    ps = {
        "project_path": str(project_path),
        "pipeline_status": "WAITING_FOR_SENTINEL",
        "current_phase": 1,
        "current_phase_raw_id": "DATA-E2",
        "current_agent": "reviewer",
        "executor_retries": 0,
        "planner_retries": 0,
        "reviewer_retries": 0,
        "last_action_timestamp": "2026-04-30T09:00:00Z",
    }
    ps.update(overrides)
    with open(cfg["pipeline_state_path"], "w") as f:
        json.dump(ps, f)


def _write_phase_state(project_path, **fields):
    artifacts = os.path.join(str(project_path), ".autodev", "pipeline")
    os.makedirs(artifacts, exist_ok=True)
    with open(os.path.join(artifacts, "phase_state.json"), "w") as f:
        json.dump(fields, f)


def _write_executor_output(project_path):
    """Drop an executor_output.json into a project's artifacts dir (probe fixture)."""
    artifacts = os.path.join(str(project_path), ".autodev", "pipeline")
    os.makedirs(artifacts, exist_ok=True)
    with open(os.path.join(artifacts, "executor_output.json"), "w") as f:
        json.dump({"status": "ok"}, f)


# ---------------------------------------------------------------------------
# 1. Phase description is NOT truncated
# ---------------------------------------------------------------------------

def test_snapshot_returns_full_phase_description(tmp_path):
    """Phase descriptions longer than 60 chars must not be truncated."""
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_LONG_DESC)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(str(proj), entry_id=eid)])
    _write_pipeline_state(cfg, proj)

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=True):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    data = resp.json()
    assert data["current_phase_desc"] == LONG_PHASE_DESC
    assert len(data["current_phase_desc"]) > 60


def test_snapshot_short_phase_description_unchanged(tmp_path):
    """Short descriptions pass through without modification."""
    roadmap = "- [ ] `CORE-E1` | LOW | Short goal\n  > Test: ok.\n"
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(roadmap)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(str(proj), entry_id=eid)])
    _write_pipeline_state(cfg, proj, current_phase_raw_id="CORE-E1", current_phase=0)

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=True):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    assert resp.json()["current_phase_desc"] == "Short goal"


# ---------------------------------------------------------------------------
# 2. New phase_state fields in snapshot
# ---------------------------------------------------------------------------

def test_snapshot_returns_phase_state_fields_when_active(tmp_path):
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_LONG_DESC)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(str(proj), entry_id=eid)])
    _write_pipeline_state(cfg, proj)
    _write_phase_state(
        proj,
        escalation_resets=2,
        last_error_code="ERR_STALL_TIMEOUT",
        escalation_message="Executor timed out after 30 minutes of inactivity",
        escalation_trigger_reason="stall_detected",
        skill_injected="data-persistence",
        skill_agent="executor",
        waiting_for_human_at="2026-04-30T10:00:00Z",
    )

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=True):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    data = resp.json()
    assert data["escalation_resets"] == 2
    assert data["last_error_code"] == "ERR_STALL_TIMEOUT"
    assert data["escalation_message"] == "Executor timed out after 30 minutes of inactivity"
    assert data["escalation_trigger_reason"] == "stall_detected"
    assert data["skill_injected"] == "data-persistence"
    assert data["skill_agent"] == "executor"
    assert data["waiting_for_human_at"] == "2026-04-30T10:00:00Z"


def test_snapshot_phase_state_fields_from_own_project_when_not_active(tmp_path):
    """INVERTED from the former ``…_null_when_not_active`` (which asserted the bug).

    A parked (non-active) entry WITH its own ``phase_state.json`` must return ITS OWN
    phase_state fields. Only the genuinely-live ``pipeline_state``-sourced fields
    (pipeline_status / current_agent / retries / last_action) stay ``None`` because the
    orchestrator is not running this entry right now.
    """
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_LONG_DESC)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(str(proj), state="ESCALATION", entry_id=eid)])
    # No pipeline_state on disk → this entry is NOT the active project.
    _write_phase_state(
        proj,
        escalation_message="own escalation message",
        last_error_code="ERR_OWN",
        escalation_trigger_reason="own trigger reason",
        escalation_headline="own headline",
        escalation_advisory_status="fallback",
        escalation_recommended_action="own action",
        skill_injected="ui-frontend",
        skill_agent="executor",
        waiting_for_human_at="2026-05-01T10:00:00Z",
        escalation_resets=3,
    )

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=False):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active_project"] is False
    # Own phase_state fields now flow through even though the entry is parked.
    assert data["escalation_message"] == "own escalation message"
    assert data["last_error_code"] == "ERR_OWN"
    assert data["escalation_trigger_reason"] == "own trigger reason"
    assert data["escalation_headline"] == "own headline"
    assert data["escalation_advisory_status"] == "fallback"
    assert data["escalation_recommended_action"] == "own action"
    assert data["skill_injected"] == "ui-frontend"
    assert data["skill_agent"] == "executor"
    assert data["waiting_for_human_at"] == "2026-05-01T10:00:00Z"
    assert data["escalation_resets"] == 3
    # Live pipeline_state-sourced fields stay None (orchestrator not running this entry).
    assert data["pipeline_status"] is None
    assert data["current_agent"] is None
    assert data["planner_retries"] is None
    assert data["executor_retries"] is None
    assert data["reviewer_retries"] is None
    assert data["last_action"] is None


def test_snapshot_phase_state_fields_default_when_no_phase_state_not_active(tmp_path):
    """Parked entry with NO phase_state.json → escalation fields fall back to the
    snapshot's return-layer defaults (escalation_resets → 0, the rest → None)."""
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_LONG_DESC)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(str(proj), state="ESCALATION", entry_id=eid)])
    # No pipeline_state AND no phase_state on disk.

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=False):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active_project"] is False
    assert data["escalation_resets"] == 0
    assert data["last_error_code"] is None
    assert data["escalation_message"] is None
    assert data["escalation_trigger_reason"] is None
    assert data["skill_injected"] is None
    assert data["skill_agent"] is None
    assert data["waiting_for_human_at"] is None
    assert data["escalation_headline"] is None
    assert data["escalation_advisory_status"] is None
    assert data["escalation_recommended_action"] is None


def test_snapshot_parked_escalation_returns_own_advisory_not_active_project(tmp_path):
    """G3 follow-up regression: a parked ESCALATION entry's snapshot must describe ITS
    OWN project — not the active symlink project.

    Two projects (active vs parked-ESCALATION). The parked entry's snapshot returns its
    own ``escalation_headline`` / ``escalation_advisory_status`` /
    ``escalation_recommended_action`` / ``escalation_message`` / ``escalation_resets`` —
    NOT the active project's — and ``is_active_project`` is False. File probes
    (``executor_output_exists``) resolve against the PARKED project.
    """
    proj_active = tmp_path / "active_proj"
    proj_active.mkdir()
    (proj_active / "roadmap.md").write_text(ROADMAP_LONG_DESC)
    proj_parked = tmp_path / "parked_proj"
    proj_parked.mkdir()
    (proj_parked / "roadmap.md").write_text(ROADMAP_LONG_DESC)

    cfg = _make_cfg(tmp_path, proj_active)
    active_id = str(uuid.uuid4())
    parked_id = str(uuid.uuid4())
    _write_queue(cfg, [
        _make_entry(str(proj_active), state="ACTIVE", entry_id=active_id),
        _make_entry(str(proj_parked), state="ESCALATION", entry_id=parked_id),
    ])
    # pipeline_state points at the ACTIVE project — so the parked entry is non-active.
    _write_pipeline_state(cfg, proj_active)

    # Distinct advisories on each project so a cross-read is detectable.
    _write_phase_state(
        proj_active,
        escalation_headline="ACTIVE phase needs input",
        escalation_advisory_status="generating",
        escalation_recommended_action="active action",
        escalation_message="active message",
        escalation_trigger_reason="active reason",
        escalation_resets=0,
    )
    _write_phase_state(
        proj_parked,
        escalation_headline="PARKED phase needs input",
        escalation_advisory_status="ready",
        escalation_recommended_action="Use Reset Execution on the parked project",
        escalation_message="Parked executor blocked on tests",
        escalation_trigger_reason="parked reason",
        escalation_resets=1,
    )
    # The PARKED project has its own executor output — the probe must resolve against IT.
    _write_executor_output(proj_parked)

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=True):
        resp = client.get(f"/api/queue/{parked_id}/snapshot")

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active_project"] is False
    # Advisory fields are the PARKED project's own — not the active project's.
    assert data["escalation_headline"] == "PARKED phase needs input"
    assert data["escalation_advisory_status"] == "ready"
    assert data["escalation_recommended_action"] == "Use Reset Execution on the parked project"
    assert data["escalation_message"] == "Parked executor blocked on tests"
    assert data["escalation_trigger_reason"] == "parked reason"
    assert data["escalation_resets"] == 1
    # The file probe resolves against the parked project (it has executor_output.json).
    assert data["executor_output_exists"] is True
    # Live pipeline_state-sourced fields stay None for a parked entry.
    assert data["pipeline_status"] is None
    assert data["current_agent"] is None


def test_snapshot_phase_state_fields_null_when_phase_state_missing(tmp_path):
    """Active project but no phase_state.json on disk — fields default to None."""
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_LONG_DESC)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(str(proj), entry_id=eid)])
    _write_pipeline_state(cfg, proj)
    # Intentionally do NOT write phase_state.json

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=True):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active_project"] is True
    assert data["escalation_resets"] == 0
    assert data["last_error_code"] is None
    assert data["skill_injected"] is None


def test_snapshot_escalation_message_capped_at_500_chars(tmp_path):
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_LONG_DESC)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(str(proj), entry_id=eid)])
    _write_pipeline_state(cfg, proj)
    _write_phase_state(proj, escalation_message="x" * 1000)

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=True):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    msg = resp.json()["escalation_message"]
    assert len(msg) <= 500


def test_snapshot_escalation_message_capped_at_500_chars_parked(tmp_path):
    """The 500-char cap applies to parked entries too (the shared helper enforces it)."""
    proj_active = tmp_path / "active_proj"
    proj_active.mkdir()
    (proj_active / "roadmap.md").write_text(ROADMAP_LONG_DESC)
    proj_parked = tmp_path / "parked_proj"
    proj_parked.mkdir()
    (proj_parked / "roadmap.md").write_text(ROADMAP_LONG_DESC)

    cfg = _make_cfg(tmp_path, proj_active)
    parked_id = str(uuid.uuid4())
    _write_queue(cfg, [
        _make_entry(str(proj_active), state="ACTIVE", entry_id=str(uuid.uuid4())),
        _make_entry(str(proj_parked), state="ESCALATION", entry_id=parked_id),
    ])
    _write_pipeline_state(cfg, proj_active)
    _write_phase_state(proj_parked, escalation_message="y" * 1000)

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=True):
        resp = client.get(f"/api/queue/{parked_id}/snapshot")

    assert resp.status_code == 200
    msg = resp.json()["escalation_message"]
    assert msg is not None
    assert len(msg) <= 500


# ---------------------------------------------------------------------------
# 3. last_action and sentinel_wait_started_at from pipeline_state
# ---------------------------------------------------------------------------

def test_snapshot_returns_last_action_from_pipeline_state(tmp_path):
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_LONG_DESC)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(str(proj), entry_id=eid)])
    _write_pipeline_state(cfg, proj, last_action="Executor gate passed")

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=True):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    assert resp.json()["last_action"] == "Executor gate passed"


def test_snapshot_returns_sentinel_wait_started_at(tmp_path):
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_LONG_DESC)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(str(proj), entry_id=eid)])
    _write_pipeline_state(cfg, proj, sentinel_wait_started_at="2026-04-30T09:30:00Z")

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=True):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    assert resp.json()["sentinel_wait_started_at"] == "2026-04-30T09:30:00Z"


def test_snapshot_last_action_null_when_not_active(tmp_path):
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_LONG_DESC)

    cfg = _make_cfg(tmp_path, proj)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(str(proj), entry_id=eid)])
    # No pipeline_state → not active

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=False):
        resp = client.get(f"/api/queue/{eid}/snapshot")

    assert resp.status_code == 200
    data = resp.json()
    assert data["last_action"] is None
    assert data["sentinel_wait_started_at"] is None
