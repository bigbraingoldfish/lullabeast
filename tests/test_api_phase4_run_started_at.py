"""UI REVIEW Phase 4 (finding 3-A) — ``run_started_at`` backend wiring.

The RoadmapPanel "(from previous run)" staleness badge compares the completion
report's mtime against ``pState.run_started_at``, but that field had no Python
writer and was absent from ``/api/state``, so the badge could never render. These
pin the SERVER half of the wiring:

- ``GET /api/state`` exposes ``run_started_at`` (it is an explicit whitelist, so
  the field does not appear automatically — it must be added deliberately).
- The shared launch reset template ``_clean_pipeline_state_for_project`` (used by
  ``/api/setup/launch`` and ``/api/setup/switch-project``) stamps it.

The ORCHESTRATOR half (stamp at fresh-start, preserve across phase advance /
revival) is pinned in ``autodev/tests/test_run_started_at_lifecycle.py``.

Fixture pattern mirrors ``test_api_state.py``.
"""
import json
import os
import tempfile
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ui.server import app, _clean_pipeline_state_for_project

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


def _write_state(cfg, state):
    with open(cfg["pipeline_state_path"], "w") as f:
        json.dump(state, f)


def test_state_exposes_run_started_at(temp_dir):
    """A ``run_started_at`` present in pipeline_state.json is returned verbatim by
    ``/api/state`` so the UI badge can compute staleness. Catches the missing
    whitelist entry (the endpoint does not pass the whole state dict through)."""
    cfg = _config(temp_dir)
    _write_state(cfg, {
        "pipeline_status": "RUNNING",
        "current_phase": 1,
        "run_started_at": "2026-06-08T12:00:00+00:00",
    })
    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/state")
    assert resp.status_code == 200
    assert resp.json()["run_started_at"] == "2026-06-08T12:00:00+00:00"


def test_state_run_started_at_null_when_absent(temp_dir):
    """When the state file lacks the field, the response still carries the key as
    ``None`` (stable shape) — never a KeyError or a missing key the UI would read
    as ``undefined`` inconsistently."""
    cfg = _config(temp_dir)
    _write_state(cfg, {"pipeline_status": "RUNNING", "current_phase": 1})
    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/state")
    assert resp.status_code == 200
    body = resp.json()
    assert "run_started_at" in body
    assert body["run_started_at"] is None


def test_clean_pipeline_state_stamps_run_started_at():
    """The shared launch/switch reset template stamps an ISO8601 ``run_started_at``
    so a Setup-launched run has a start marker for the staleness badge. Catches the
    two server fresh-run writers failing to stamp."""
    state = _clean_pipeline_state_for_project("/some/project")
    assert "run_started_at" in state
    parsed = datetime.fromisoformat(state["run_started_at"])
    assert parsed.tzinfo is not None, "run_started_at must be timezone-aware ISO8601"
