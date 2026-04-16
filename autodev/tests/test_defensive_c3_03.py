"""C3-03: apply_cli_project_path must not call write_state when update_symlink fails."""
import json
import os
import sys
import importlib
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))

    import orchestrator as orch_mod
    importlib.reload(orch_mod)
    from orchestrator import Orchestrator as FreshOrch, apply_cli_project_path

    state_file = tmp_path / "pipeline_state.json"
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(state_file))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path))

    inst = FreshOrch.__new__(FreshOrch)
    inst.state = {
        "current_phase": 0,
        "current_phase_raw_id": "",
        "current_agent": "planner",
        "pipeline_status": "RUNNING",
        "project_path": str(tmp_path / "old_proj"),
        "status": "RUNNING",
        "planner_retries": 0,
        "executor_retries": 0,
        "reviewer_retries": 0,
        "last_action": "test",
        "last_action_timestamp": "2026-01-01T00:00:00Z",
    }
    inst.lock_fd = None

    return inst, state_file, tmp_path, orch_mod, apply_cli_project_path


def test_write_state_not_called_when_symlink_fails(setup, tmp_path):
    """When update_symlink returns False, write_state must NOT commit the new project path
    to disk. The old on-disk state should remain unchanged."""
    inst, state_file, base, mod, apply_fn = setup

    # Write a baseline state so we can check it's unchanged
    old_state = dict(inst.state)
    old_state["project_path"] = str(base / "old_proj")
    state_file.write_text(json.dumps(old_state), encoding="utf-8")

    new_proj = str(base / "new_proj")

    write_state_calls = []
    original_ws = inst.__class__.write_state

    def tracking_ws(self_arg):
        write_state_calls.append("called")
        return original_ws(self_arg)

    with patch.object(inst, "update_symlink", return_value=False), \
         patch.object(inst.__class__, "write_state", tracking_ws):
        apply_fn(inst, new_proj)

    assert len(write_state_calls) == 0, (
        "write_state was called even though update_symlink returned False; "
        "this commits stale RUNNING state for the new project path."
    )


def test_state_file_unchanged_when_symlink_fails(setup, tmp_path):
    """On symlink failure the on-disk pipeline_state.json must not be updated."""
    inst, state_file, base, mod, apply_fn = setup

    old_proj = str(base / "old_proj")
    old_state = dict(inst.state)
    old_state["project_path"] = old_proj
    state_file.write_text(json.dumps(old_state), encoding="utf-8")

    new_proj = str(base / "new_proj")
    with patch.object(inst, "update_symlink", return_value=False):
        apply_fn(inst, new_proj)

    on_disk = json.loads(state_file.read_text(encoding="utf-8"))
    assert on_disk["project_path"] == old_proj, (
        f"pipeline_state.json was overwritten with new project path despite symlink failure. "
        f"Got project_path={on_disk['project_path']!r}"
    )


def test_successful_path_writes_state(setup, tmp_path):
    """Sanity: when symlink succeeds, write_state IS called and file is updated."""
    inst, state_file, base, mod, apply_fn = setup

    new_proj = str(base / "new_proj")
    with patch.object(inst, "update_symlink", return_value=True):
        apply_fn(inst, new_proj)

    assert state_file.exists(), "pipeline_state.json should be written when symlink succeeds"
    on_disk = json.loads(state_file.read_text(encoding="utf-8"))
    assert on_disk["project_path"] == new_proj
