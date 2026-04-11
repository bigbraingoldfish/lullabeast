"""C3-02: Queue advance must confirm symlink success before committing ACTIVE + write_state.

If update_symlink returns False:
 - queue row must NOT be ACTIVE on disk
 - write_state must NOT be called (new project state not committed)
"""
import json
import os
import sys
import importlib
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def orch(tmp_path, monkeypatch):
    """Orchestrator instance with queue/state files under tmp_path."""
    queue_file = tmp_path / "pipeline_queue.json"
    state_file = tmp_path / "pipeline_state.json"

    monkeypatch.setenv("AUTODEV_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME", "1")

    import orchestrator as orch_mod
    importlib.reload(orch_mod)
    from orchestrator import Orchestrator as FreshOrch

    inst = FreshOrch.__new__(FreshOrch)
    inst.state = {
        "current_phase": 0,
        "current_phase_raw_id": "",
        "current_agent": "planner",
        "pipeline_status": "RUNNING",
        "project_path": str(tmp_path / "current_proj"),
        "status": "RUNNING",
    }
    inst.lock_fd = None
    inst.openclaw_config = {}
    inst.skill_manager = MagicMock()
    inst.logger = MagicMock()

    monkeypatch.setattr(orch_mod, "QUEUE_FILE", str(queue_file))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(state_file))
    monkeypatch.setattr(orch_mod, "AUTODEV_RUNTIME_ROOT", str(tmp_path))

    return inst, queue_file, state_file, tmp_path, orch_mod


def _make_entry(name, state="READY", position=1, project_path=None):
    return {
        "id": str(uuid.uuid4()),
        "project_path": project_path or f"/tmp/proj_{name}",
        "idea_id": None,
        "name": name,
        "state": state,
        "position": position,
        "parent_id": None,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "completed_at": None,
        "blocked_at": None,
        "skip_count": 0,
        "preflight_validated_at": datetime.now(timezone.utc).isoformat(),
        "notes": "",
    }


def _write_queue(path, entries):
    data = {"queue": entries, "queue_mode": "auto",
            "last_updated": datetime.now(timezone.utc).isoformat()}
    with open(str(path), "w") as f:
        json.dump(data, f)


class TestC302SymlinkOrderingOnFailure:

    def test_queue_row_not_active_when_symlink_fails(self, orch, tmp_path):
        """When update_symlink returns False, the queue row must NOT be left ACTIVE."""
        inst, queue_file, state_file, base, mod = orch
        proj = base / "testproj"
        proj.mkdir()

        entry = _make_entry("testproj", project_path=str(proj))
        _write_queue(queue_file, [entry])

        with patch.object(inst, "update_symlink", return_value=False), \
             patch.object(inst, "_queue_preflight", return_value=(True, None)), \
             patch.object(inst, "_apply_pending_escalation_command"):
            inst._select_next_queue_project()

        # Read the queue back from disk and check row state
        queue_on_disk = json.loads(queue_file.read_text())
        row = queue_on_disk["queue"][0]
        assert row["state"] != "ACTIVE", (
            f"Queue row became ACTIVE on disk even though symlink update failed; "
            f"got state={row['state']!r}. Agents will read the wrong project."
        )

    def test_write_state_not_called_when_symlink_fails(self, orch, tmp_path):
        """When update_symlink returns False, write_state must NOT be called."""
        inst, queue_file, state_file, base, mod = orch
        proj = base / "testproj2"
        proj.mkdir()

        entry = _make_entry("testproj2", project_path=str(proj))
        _write_queue(queue_file, [entry])

        write_state_calls = []
        original_ws = inst.__class__.write_state

        def tracking_ws(self_arg):
            write_state_calls.append("called")
            return original_ws(self_arg)

        with patch.object(inst, "update_symlink", return_value=False), \
             patch.object(inst, "_queue_preflight", return_value=(True, None)), \
             patch.object(inst, "_apply_pending_escalation_command"), \
             patch.object(inst.__class__, "write_state", tracking_ws):
            inst._select_next_queue_project()

        assert len(write_state_calls) == 0, (
            "write_state was called after symlink failure; "
            "this commits stale RUNNING state for the new project."
        )

    def test_successful_advance_still_works(self, orch, tmp_path):
        """Sanity: when symlink succeeds, queue row becomes ACTIVE and state is written."""
        inst, queue_file, state_file, base, mod = orch
        proj = base / "goodproj"
        proj.mkdir()

        entry = _make_entry("goodproj", project_path=str(proj))
        _write_queue(queue_file, [entry])

        with patch.object(inst, "update_symlink", return_value=True), \
             patch.object(inst, "_queue_preflight", return_value=(True, None)), \
             patch.object(inst, "_apply_pending_escalation_command"):
            result = inst._select_next_queue_project()

        assert result is True

        queue_on_disk = json.loads(queue_file.read_text())
        row = queue_on_disk["queue"][0]
        assert row["state"] == "ACTIVE", f"Expected ACTIVE, got {row['state']!r}"
        assert state_file.exists(), "pipeline_state.json should be written on success"
