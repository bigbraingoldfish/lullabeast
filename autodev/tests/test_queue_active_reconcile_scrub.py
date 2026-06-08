"""Defect C (C2) — the orchestrator's parked-entry reconcile must scrub the FULL set.

`_queue_restore_parked_entry_to_active` historically popped only 3 of the 5 parked_*
keys (it left ``parked_state_snapshot`` and ``answered_at`` behind). Restoring a row to
ACTIVE while a stale snapshot lingers is exactly the drift this fixes. These tests are RED
against the pre-fix tree.

Hermetic: ``Orchestrator.__new__`` + monkeypatched module path constants, mirroring
``test_defensive_p6_queue_parsing.py``.
"""
import importlib
import json
import os
import sys
import uuid

import pytest
from unittest.mock import MagicMock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))
    import orchestrator as orch_mod
    importlib.reload(orch_mod)

    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.lock_fd = None
    inst.state = {"pipeline_status": "WAITING_FOR_HUMAN", "current_agent": "escalation"}

    queue_file = tmp_path / "pipeline_queue.json"
    state_file = tmp_path / "pipeline_state.json"
    monkeypatch.setattr(orch_mod, "QUEUE_FILE", str(queue_file))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(state_file))
    monkeypatch.setattr(orch_mod, "OPENCLAW_ROOT", str(tmp_path))
    # SYMLINK_TARGET (tmp_path/pipeline-project) is left absent on disk so the method
    # falls back to self.state["project_path"] for the current-project match.
    monkeypatch.setattr(orch_mod, "_write_pipeline_event", MagicMock())
    monkeypatch.setattr(orch_mod, "_write_run_manifest", MagicMock())
    return inst, orch_mod, queue_file, tmp_path


def _write_queue(path, entry):
    with open(str(path), "w") as f:
        json.dump({"queue": [entry], "queue_version": 1}, f)


def _read_queue(path):
    with open(str(path)) as f:
        return json.load(f)


def test_restore_parked_entry_clears_full_field_set(orch):
    """ESCALATION row carrying ALL five parked_* fields -> ACTIVE with NONE remaining.

    Regression caught: a reintroduction of the 3-of-5 partial scrub (leaving
    parked_state_snapshot / answered_at on an ACTIVE row)."""
    inst, _mod, queue_file, tmp_path = orch
    project = tmp_path / "proj_mc"
    project.mkdir()
    inst.state["project_path"] = str(project)

    entry = {
        "id": str(uuid.uuid4()),
        "project_path": str(project),
        "name": "mc",
        "state": "ESCALATION",
        "position": 1,
        "parent_id": None,
        "started_at": "2026-06-08T04:40:01+00:00",
        "parked_state_snapshot": {"current_phase_raw_id": "CORE-E1", "phase_base_commit": "deadbeef"},
        "parked_at": "2026-06-08T05:08:42+00:00",
        "parked_reason": "escalation",
        "parked_pipeline_status": "WAITING_FOR_HUMAN",
        "answered_at": "2026-06-08T06:00:00+00:00",
    }
    _write_queue(queue_file, entry)

    inst._queue_restore_parked_entry_to_active()

    row = _read_queue(queue_file)["queue"][0]
    assert row["state"] == "ACTIVE"
    for stale in ("parked_state_snapshot", "parked_at", "parked_reason",
                  "parked_pipeline_status", "answered_at"):
        assert stale not in row, f"{stale} should have been scrubbed on restore"


def test_reconcile_paths_use_shared_scrub_helper(orch):
    """Structural pin: the three orchestrator parked-exit sites (two selection
    activations + the restore helper) route through the shared scrub_parked_fields,
    so the canonical set cannot drift and the old inline partial cannot creep back."""
    _inst, mod, _qf, _tp = orch
    src = open(mod.__file__).read()
    assert src.count("scrub_parked_fields(") >= 3, (
        "expected the two selection activations + _queue_restore_parked_entry_to_active "
        "to all call scrub_parked_fields"
    )
