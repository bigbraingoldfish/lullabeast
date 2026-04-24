"""sentinel_wait_started_at in pipeline_state tracks per-attempt sentinel wait anchor."""

import json
import os
import sys
from unittest.mock import patch

import pytest

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PIPELINE_DIR = os.path.join(_REPO_DIR, "autodev", "pipeline")
for _p in [_PIPELINE_DIR, _REPO_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _minimal_orch(tmp_dir: str):
    import orchestrator as orc_module

    state_file = os.path.join(tmp_dir, "pipeline_state.json")
    lock_file = os.path.join(tmp_dir, "pipeline.lock")
    config_file = os.path.join(tmp_dir, "openclaw.json")
    phase_state_file = os.path.join(tmp_dir, "phase_state.json")
    with open(config_file, "w") as f:
        json.dump({"hooks": {"token": "t"}}, f)
    with (
        patch.object(orc_module, "STATE_FILE", state_file),
        patch.object(orc_module, "SYMLINK_TARGET", tmp_dir),
        patch.object(orc_module, "LOCK_FILE", lock_file),
        patch.object(orc_module, "CONFIG_FILE", config_file),
        patch.object(orc_module, "PHASE_STATE_FILE", phase_state_file),
    ):
        from orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch.lock_fd = None
        orch.openclaw_config = {"hooks": {"token": "t"}}
        orch.state = {
            "pipeline_status": "RUNNING",
            "current_agent": "planner",
            "current_phase": 1,
            "current_phase_raw_id": "CORE-1",
            "planner_retries": 0,
            "executor_retries": 0,
            "reviewer_retries": 0,
            "last_action": "idle",
            "project_path": tmp_dir,
        }
    return orch


class TestSentinelWaitStartedAt:
    def test_transition_to_waiting_for_sentinel_preserves_sentinel_timestamp(self, tmp_path):
        """Caller sets sentinel_wait_started_at before transition_state(WAITING_FOR_SENTINEL)."""
        from datetime import datetime, timezone

        import orchestrator as orc_module

        tmp = str(tmp_path)
        orch = _minimal_orch(tmp)
        ts = "2026-04-23T12:00:00+00:00"
        orch.state["sentinel_wait_started_at"] = ts

        with patch.object(orc_module, "SYMLINK_TARGET", tmp):
            with patch.object(orch, "write_state") as mock_write:
                orch.transition_state("WAITING_FOR_SENTINEL", "Invoking Planner via webhook")

        assert mock_write.called
        written = orch.state
        assert written.get("pipeline_status") == "WAITING_FOR_SENTINEL"
        assert written.get("sentinel_wait_started_at") == ts

    def test_transition_out_of_waiting_for_sentinel_clears_field(self, tmp_path):
        """Leaving WAITING_FOR_SENTINEL clears sentinel_wait_started_at."""
        import orchestrator as orc_module

        tmp = str(tmp_path)
        orch = _minimal_orch(tmp)
        orch.state["pipeline_status"] = "WAITING_FOR_SENTINEL"
        orch.state["sentinel_wait_started_at"] = "2026-04-23T12:00:00+00:00"

        with patch.object(orc_module, "SYMLINK_TARGET", tmp):
            with patch.object(orch, "write_state") as mock_write:
                orch.transition_state("RUNNING", "Planner passed, moving to executor")

        assert orch.state.get("sentinel_wait_started_at") is None
        assert orch.state["pipeline_status"] == "RUNNING"
        assert mock_write.called
