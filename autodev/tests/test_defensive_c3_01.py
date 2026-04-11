"""C3-01: write_state must re-raise on failed atomic write.

After a failed write, the caller must not assume disk == memory.
Silently returning would leave the caller believing state was persisted
when it was not, causing stale-state reads on restart.
"""
import json
import os
import sys
import importlib
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODEV_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME", "1")

    import orchestrator as orch_mod
    importlib.reload(orch_mod)

    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.state = {
        "pipeline_status": "RUNNING",
        "current_agent": "planner",
        "current_phase": 0,
        "current_phase_raw_id": "",
        "status": "RUNNING",
        "last_action": "test",
    }
    inst.lock_fd = None
    inst.openclaw_config = {"hooks_url": "http://x", "hooks_token": "t"}
    inst.skill_manager = MagicMock()

    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))
    monkeypatch.setattr(orch_mod, "AUTODEV_RUNTIME_ROOT", str(tmp_path))

    return inst, orch_mod, tmp_path


class TestC301WriteStateReRaise:

    def test_write_state_raises_on_disk_failure(self, orch):
        """write_state must raise (not silently return) when atomic write fails."""
        inst, mod, tmp_path = orch

        with patch("tempfile.mkstemp", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                inst.write_state()

    def test_write_state_raises_on_replace_failure(self, orch):
        """write_state must raise when os.replace fails."""
        inst, mod, tmp_path = orch

        with patch("os.replace", side_effect=OSError("replace failed")):
            with pytest.raises(OSError):
                inst.write_state()

    def test_write_state_succeeds_normally(self, orch):
        """Sanity: write_state still works when no error occurs."""
        inst, mod, tmp_path = orch
        state_file = tmp_path / "pipeline_state.json"

        inst.write_state()  # must not raise

        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["pipeline_status"] == "RUNNING"
