"""P2-A — ``agent_activity_age_seconds`` on ``GET /api/state`` (agent liveness proxy).

The OpenClaw plugin refreshes ``{project}/.autodev/pipeline/{agent}_activity.stamp``
on every model/tool hook, so ``now - mtime`` distinguishes a working agent (small
age) from a stalled one (growing age) — the signal the Pipeline screen's liveness
pulse renders (P2-B). The stamp is the sibling of ``phase_state.json`` written by
the orchestrator, so the server derives the path from the phase_state directory.

Unlike the present-only outcome fields, this is **always present** (null when there
is no current agent or the stamp is absent/unreadable) so the UI can rely on the
key existing.

Fixture pattern mirrors ``test_api_phase4_run_started_at.py``.
"""
import json
import os
import tempfile
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ui.server import app

client = TestClient(app)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def _config(temp_dir):
    project_root = os.path.join(temp_dir, "pipeline_project")
    os.makedirs(project_root, exist_ok=True)
    return {
        "pipeline_state_path": os.path.join(temp_dir, "pipeline_state.json"),
        "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
        "lock_path": os.path.join(temp_dir, "pipeline.lock"),
        "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
        "project_dir_path": project_root,
    }


def _write(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


def _stamp_path(cfg, agent):
    # The stamp is the orchestrator-written sibling of phase_state.json.
    return os.path.join(os.path.dirname(cfg["phase_state_path"]), f"{agent}_activity.stamp")


def test_age_present_when_stamp_exists(temp_dir):
    """A stamp touched ~45s ago yields an age in that neighborhood — the working
    vs stalled signal the liveness pulse keys off."""
    cfg = _config(temp_dir)
    _write(cfg["pipeline_state_path"], {
        "pipeline_status": "WAITING_FOR_SENTINEL", "current_agent": "executor",
    })
    _write(cfg["phase_state_path"], {})
    stamp = _stamp_path(cfg, "executor")
    open(stamp, "w").close()
    old = time.time() - 45
    os.utime(stamp, (old, old))
    with patch("ui.server.load_config", return_value=cfg):
        body = client.get("/api/state").json()
    age = body["agent_activity_age_seconds"]
    assert age is not None
    assert 44 <= age < 120, f"expected ~45s, got {age}"


def test_age_null_when_stamp_absent(temp_dir):
    """No stamp on disk → null (key still present), so the UI shows 'no signal'
    rather than a misleading age."""
    cfg = _config(temp_dir)
    _write(cfg["pipeline_state_path"], {"pipeline_status": "RUNNING", "current_agent": "executor"})
    _write(cfg["phase_state_path"], {})
    with patch("ui.server.load_config", return_value=cfg):
        body = client.get("/api/state").json()
    assert "agent_activity_age_seconds" in body
    assert body["agent_activity_age_seconds"] is None


def test_age_null_when_no_current_agent(temp_dir):
    """No current agent (idle/stopped) → null even if a stale stamp lingers on disk."""
    cfg = _config(temp_dir)
    _write(cfg["pipeline_state_path"], {"pipeline_status": "STOPPED"})
    _write(cfg["phase_state_path"], {})
    # A leftover stamp from a prior run must not produce a bogus age with no agent.
    open(_stamp_path(cfg, "executor"), "w").close()
    with patch("ui.server.load_config", return_value=cfg):
        body = client.get("/api/state").json()
    assert body["agent_activity_age_seconds"] is None
