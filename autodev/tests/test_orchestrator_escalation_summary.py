"""
Tests for orchestrator._read_escalation_summary() and escalation_message write path.

Spec reference: plans/Active/ESC/Escalation redesign.md § Step 3.2

Validates:
  1. Valid escalation_summary.json → returns summary string
  2. Valid summary → orchestrator writes escalation_message to phase_state.json
  3. Summary exceeds 200 chars → truncated to 200
  4. Missing escalation_summary.json → returns None (no exception)
  5. Malformed JSON → returns None, no exception raised
  6. recommended_action exceeds 200 chars → summary still returned correctly
"""

import json
import os
import sys
from unittest.mock import patch

import pytest

OPENCLAW_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE_SCRIPTS_DIR = os.path.join(OPENCLAW_DIR, "autodev", "pipeline", "gate_scripts")
PIPELINE_DIR = os.path.join(OPENCLAW_DIR, "autodev", "pipeline")

for _p in [GATE_SCRIPTS_DIR, PIPELINE_DIR, OPENCLAW_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _make_test_orchestrator(tmp_dir: str):
    """Return a minimal Orchestrator instance with state files wired to tmp_dir."""
    import orchestrator as orc_module

    state_file = os.path.join(tmp_dir, "pipeline_state.json")
    lock_file = os.path.join(tmp_dir, "pipeline.lock")
    config_file = os.path.join(tmp_dir, "openclaw.json")
    phase_state_file = os.path.join(tmp_dir, "phase_state.json")

    with open(config_file, "w") as f:
        json.dump({"hooks": {"token": "test-tok"}}, f)

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
        orch.openclaw_config = {"hooks": {"token": "test-tok"}}
        orch.state = {
            "current_phase": 1,
            "current_phase_raw_id": "CORE-1",
            "current_agent": "escalation",
            "pipeline_status": "WAITING_FOR_HUMAN",
        }

    return orch


class TestReadEscalationSummary:
    """Direct tests for _read_escalation_summary() helper."""

    def test_valid_summary_returns_string(self, tmp_path):
        """Valid escalation_summary.json → helper returns the summary field."""
        orch = _make_test_orchestrator(str(tmp_path))
        summary_data = {
            "summary": "Executor failed due to missing env var.",
            "recommended_action": "Set AUTH_TOKEN in .env and use Reset Execution.",
        }
        (tmp_path / "escalation_summary.json").write_text(json.dumps(summary_data))

        import orchestrator as orc_module

        with patch.object(orc_module, "SYMLINK_TARGET", str(tmp_path)):
            result = orch._read_escalation_summary()

        assert result == "Executor failed due to missing env var."

    def test_valid_summary_writes_escalation_message_to_phase_state(self, tmp_path):
        """Non-None summary → write_phase_state_atomic stores escalation_message."""
        orch = _make_test_orchestrator(str(tmp_path))
        summary_data = {
            "summary": "Planner produced invalid output. Plan is missing required fields.",
            "recommended_action": "Reset Phase to retry planning.",
        }
        (tmp_path / "escalation_summary.json").write_text(json.dumps(summary_data))

        phase_state_file = str(tmp_path / "phase_state.json")
        import orchestrator as orc_module

        with (
            patch.object(orc_module, "SYMLINK_TARGET", str(tmp_path)),
            patch.object(orc_module, "PHASE_STATE_FILE", phase_state_file),
        ):
            result = orch._read_escalation_summary()
            assert result is not None
            _ps = {}
            _ps["escalation_message"] = result
            orch.write_phase_state_atomic(_ps)

        with open(phase_state_file) as f:
            written = json.load(f)

        assert written["escalation_message"] == (
            "Planner produced invalid output. Plan is missing required fields."
        )

    def test_summary_over_200_chars_truncated(self, tmp_path):
        """summary field > 200 chars → return value is exactly 200 chars."""
        orch = _make_test_orchestrator(str(tmp_path))
        long_summary = "A" * 250
        summary_data = {
            "summary": long_summary,
            "recommended_action": "Take action.",
        }
        (tmp_path / "escalation_summary.json").write_text(json.dumps(summary_data))

        import orchestrator as orc_module

        with patch.object(orc_module, "SYMLINK_TARGET", str(tmp_path)):
            result = orch._read_escalation_summary()

        assert result is not None
        assert len(result) == 200
        assert result == "A" * 200

    def test_missing_file_returns_none(self, tmp_path):
        """No escalation_summary.json → helper returns None (no exception)."""
        orch = _make_test_orchestrator(str(tmp_path))

        import orchestrator as orc_module

        with patch.object(orc_module, "SYMLINK_TARGET", str(tmp_path)):
            result = orch._read_escalation_summary()

        assert result is None

    def test_malformed_json_returns_none_no_exception(self, tmp_path):
        """Malformed JSON → helper returns None and never raises."""
        orch = _make_test_orchestrator(str(tmp_path))
        (tmp_path / "escalation_summary.json").write_text("this is not valid json {{{{")

        import orchestrator as orc_module

        with patch.object(orc_module, "SYMLINK_TARGET", str(tmp_path)):
            result = orch._read_escalation_summary()

        assert result is None

    def test_recommended_action_over_200_summary_still_returned(self, tmp_path):
        """Long recommended_action does not break summary extraction."""
        orch = _make_test_orchestrator(str(tmp_path))
        long_action = "B" * 300
        summary_data = {
            "summary": "Concise summary of the failure.",
            "recommended_action": long_action,
        }
        (tmp_path / "escalation_summary.json").write_text(json.dumps(summary_data))

        import orchestrator as orc_module

        with patch.object(orc_module, "SYMLINK_TARGET", str(tmp_path)):
            result = orch._read_escalation_summary()

        assert result == "Concise summary of the failure."
