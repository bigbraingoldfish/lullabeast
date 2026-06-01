"""C1-05: Corrupt pipeline_state.json and phase_state.json must be quarantined.

Continuing with in-memory defaults after a corrupt state file risks duplicate
phase work (same phase runs twice) or wrong agent routing (e.g. planner re-runs
when executor was mid-phase). The fix: quarantine the corrupt file and halt.
"""
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
def orch(tmp_path, monkeypatch):
    """Orchestrator instance with state/queue files under tmp_path."""
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))

    import orchestrator as orch_mod
    importlib.reload(orch_mod)
    from orchestrator import Orchestrator as FreshOrch

    state_file = tmp_path / "pipeline_state.json"
    phase_state_file = tmp_path / "pipeline-project" / "phase_state.json"
    phase_state_file.parent.mkdir(exist_ok=True)

    monkeypatch.setattr(orch_mod, "STATE_FILE", str(state_file))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(phase_state_file))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path))

    inst = FreshOrch.__new__(FreshOrch)
    inst.state = {
        "current_phase": 0,
        "current_phase_raw_id": "",
        "current_agent": "planner",
        "pipeline_status": "RUNNING",
        "project_path": str(tmp_path / "proj"),
        "planner_retries": 0,
        "executor_retries": 0,
        "reviewer_retries": 0,
        "last_action": "test",
        "last_action_timestamp": "2026-01-01T00:00:00Z",
    }
    inst.lock_fd = None

    return inst, state_file, phase_state_file, tmp_path, orch_mod


# ---------------------------------------------------------------------------
# pipeline_state.json (read_state)
# ---------------------------------------------------------------------------

class TestC105PipelineStateCorrupt:

    def test_corrupt_pipeline_state_causes_halt(self, orch):
        """read_state with a corrupt pipeline_state.json must not silently continue.
        The process must halt (sys.exit) so the operator can recover manually."""
        inst, state_file, _, base, mod = orch
        state_file.write_text("{ this is not valid JSON !!!}", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            inst.read_state()

        assert exc_info.value.code != 0, (
            "read_state must exit with non-zero code on corrupt pipeline_state.json"
        )

    def test_corrupt_pipeline_state_is_quarantined(self, orch):
        """Corrupt pipeline_state.json must be renamed to .corrupt.<timestamp>
        so the operator can inspect it and recover manually."""
        inst, state_file, _, base, mod = orch
        original_content = "{ this is not valid JSON !!!}"
        state_file.write_text(original_content, encoding="utf-8")

        try:
            inst.read_state()
        except SystemExit:
            pass

        # Original file should no longer exist at its original path
        assert not state_file.exists(), (
            "Corrupt pipeline_state.json was NOT quarantined; "
            "a subsequent write_state call would overwrite it with defaults."
        )

        # A quarantine file should exist
        quarantine_files = list(base.glob("pipeline_state.json.corrupt.*"))
        assert len(quarantine_files) == 1, (
            f"Expected exactly one .corrupt.* file, found: {quarantine_files}"
        )
        assert quarantine_files[0].read_text() == original_content

    def test_valid_pipeline_state_loads_normally(self, orch):
        """Sanity: valid pipeline_state.json loads without exception."""
        inst, state_file, _, base, mod = orch
        valid_state = {
            "current_phase": 2,
            "current_agent": "executor",
            "pipeline_status": "RUNNING",
            "project_path": "/some/project",
        }
        state_file.write_text(json.dumps(valid_state), encoding="utf-8")

        inst.read_state()  # Must not raise or exit
        assert inst.state["current_phase"] == 2
        assert inst.state["current_agent"] == "executor"

    def test_missing_pipeline_state_starts_fresh(self, orch):
        """If pipeline_state.json does not exist, read_state writes defaults
        and does not raise."""
        inst, state_file, _, base, mod = orch
        assert not state_file.exists()

        inst.read_state()  # Should write fresh state, not raise


# ---------------------------------------------------------------------------
# phase_state.json (read_phase_state)
# ---------------------------------------------------------------------------

class TestC105PhaseStateCorrupt:

    def test_corrupt_phase_state_raises(self, orch):
        """read_phase_state with corrupt JSON must raise (not return {})
        so the caller does not silently proceed with empty blame/retry context."""
        inst, _, phase_state_file, base, mod = orch
        phase_state_file.write_text("{ corrupt }", encoding="utf-8")

        with pytest.raises(Exception):
            inst.read_phase_state()

    def test_corrupt_phase_state_is_quarantined(self, orch):
        """Corrupt phase_state.json must be quarantined to .corrupt.<timestamp>."""
        inst, _, phase_state_file, base, mod = orch
        original_content = "{ corrupt phase state }"
        phase_state_file.write_text(original_content, encoding="utf-8")

        try:
            inst.read_phase_state()
        except Exception:
            pass

        assert not phase_state_file.exists(), (
            "Corrupt phase_state.json was NOT quarantined."
        )
        quarantine_files = list(phase_state_file.parent.glob("phase_state.json.corrupt.*"))
        assert len(quarantine_files) == 1
        assert quarantine_files[0].read_text() == original_content

    def test_valid_phase_state_returns_dict(self, orch):
        """Sanity: valid phase_state.json returns parsed dict."""
        inst, _, phase_state_file, base, mod = orch
        state = {"planner_retries": 1, "executor_retries": 0}
        phase_state_file.write_text(json.dumps(state), encoding="utf-8")

        result = inst.read_phase_state()
        assert result["planner_retries"] == 1

    def test_missing_phase_state_returns_empty(self, orch):
        """If phase_state.json does not exist, return {} without exception."""
        inst, _, phase_state_file, base, mod = orch
        assert not phase_state_file.exists()

        result = inst.read_phase_state()
        assert result == {}
