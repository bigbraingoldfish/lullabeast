"""C4-02: _queue_preflight must handle OSError from os.listdir gracefully.

If the project directory exists but is unreadable (e.g. permission denied),
os.listdir raises OSError. Without a try/except this crashes queue selection.
The fix: wrap os.listdir in try/except OSError and return (False, "path_unreadable").
"""
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
def orch(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))

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

    return inst, orch_mod, tmp_path


class TestC402QueuePreflightOSError:

    def test_listdir_oserror_returns_failed_preflight(self, orch, tmp_path):
        """OSError from os.listdir must return (False, ...) not raise."""
        inst, mod, base = orch

        # Create a valid-looking dir + .git so we get past the first two checks
        proj = base / "proj"
        proj.mkdir()
        (proj / ".git").mkdir()

        with patch("os.listdir", side_effect=OSError("Permission denied")):
            ok, reason = inst._queue_preflight(str(proj))

        assert ok is False
        assert reason  # some non-empty reason string

    def test_normal_missing_roadmap_still_fails(self, orch, tmp_path):
        """Sanity: missing roadmap still returns failed preflight (no regression)."""
        inst, mod, base = orch

        proj = base / "proj2"
        proj.mkdir()
        (proj / ".git").mkdir()
        # No roadmap file added

        ok, reason = inst._queue_preflight(str(proj))
        assert ok is False

    def test_valid_project_still_passes(self, orch, tmp_path):
        """Sanity: valid project directory still passes preflight."""
        inst, mod, base = orch

        proj = base / "proj3"
        proj.mkdir()
        (proj / ".git").mkdir()
        (proj / "roadmap.md").write_text("# Roadmap")

        ok, reason = inst._queue_preflight(str(proj))
        assert ok is True
