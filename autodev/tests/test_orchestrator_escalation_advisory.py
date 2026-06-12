"""
Tests for orchestrator._read_escalation_advisory().

The escalation advisory is agent-owned: the escalation agent composes
``{summary, recommended_action}`` per its escalation-summary skill and writes
``escalation_summary.json``; this reader is how the orchestrator promotes that
file into ``phase_state`` for the dashboard. (The orchestrator's former
synchronous LLM advisory call — and its tests — were removed; see
``test_escalation_advisory_agent_owned.py`` for the fold-in contract.)

Validates:
  1. _read_escalation_advisory() returns both fields from escalation_summary.json
  2. Returns None when the file is missing
  3. Fields > 200 chars are truncated (dashboard hard cap)
  4. Malformed JSON returns None without raising
"""

import json
import os
import sys
from unittest.mock import patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Shared helper — reuse pattern from test_orchestrator_escalation_summary.py
# ---------------------------------------------------------------------------

def _make_test_orchestrator(tmp_dir: str):
    """Return a minimal Orchestrator instance with state files wired to tmp_dir."""
    import orchestrator as orc_module

    state_file = os.path.join(tmp_dir, "pipeline_state.json")
    lock_file = os.path.join(tmp_dir, "pipeline.lock")
    config_file = os.path.join(tmp_dir, "openclaw.json")
    phase_state_file = os.path.join(tmp_dir, "phase_state.json")

    openclaw_cfg = {"hooks": {"token": "test-tok"}}
    with open(config_file, "w") as f:
        json.dump(openclaw_cfg, f)

    with (
        patch.object(orc_module, "STATE_FILE", state_file),
        patch.object(orc_module, "SYMLINK_TARGET", tmp_dir),
        patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", tmp_dir),
        patch.object(orc_module, "LOCK_FILE", lock_file),
        patch.object(orc_module, "CONFIG_FILE", config_file),
        patch.object(orc_module, "PHASE_STATE_FILE", phase_state_file),
    ):
        from orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch.lock_fd = None
        orch.openclaw_config = openclaw_cfg
        orch.state = {
            "current_phase": 1,
            "current_phase_raw_id": "CORE-1",
            "current_agent": "escalation",
            "pipeline_status": "WAITING_FOR_HUMAN",
            "last_action": "Executor retries exhausted",
        }

    return orch


# ---------------------------------------------------------------------------
# Tests for _read_escalation_advisory()
# ---------------------------------------------------------------------------

class TestReadEscalationAdvisory:

    def test_returns_both_fields_from_escalation_summary_json(self, tmp_path):
        """Valid escalation_summary.json → returns dict with both truncated fields."""
        import orchestrator as orc_module

        orch = _make_test_orchestrator(str(tmp_path))
        summary_data = {
            "summary": "Executor failed on authentication. The plan is valid.",
            "recommended_action": "Set AUTH_TOKEN in .env and use Reset Execution.",
        }
        (tmp_path / "escalation_summary.json").write_text(json.dumps(summary_data))

        with (
            patch.object(orc_module, "SYMLINK_TARGET", str(tmp_path)),
            patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
        ):
            result = orch._read_escalation_advisory()

        assert result is not None
        assert result["summary"] == summary_data["summary"]
        assert result["recommended_action"] == summary_data["recommended_action"]

    def test_returns_none_when_file_missing(self, tmp_path):
        """No escalation_summary.json → returns None without raising."""
        import orchestrator as orc_module

        orch = _make_test_orchestrator(str(tmp_path))

        with (
            patch.object(orc_module, "SYMLINK_TARGET", str(tmp_path)),
            patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
        ):
            result = orch._read_escalation_advisory()

        assert result is None

    def test_long_fields_truncated_to_200(self, tmp_path):
        """Fields > 200 chars in escalation_summary.json are truncated."""
        import orchestrator as orc_module

        orch = _make_test_orchestrator(str(tmp_path))
        summary_data = {
            "summary": "A" * 300,
            "recommended_action": "B" * 300,
        }
        (tmp_path / "escalation_summary.json").write_text(json.dumps(summary_data))

        with (
            patch.object(orc_module, "SYMLINK_TARGET", str(tmp_path)),
            patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
        ):
            result = orch._read_escalation_advisory()

        assert result is not None
        assert len(result["summary"]) == 200
        assert len(result["recommended_action"]) == 200

    def test_malformed_json_returns_none(self, tmp_path):
        """Malformed escalation_summary.json → returns None without raising."""
        import orchestrator as orc_module

        orch = _make_test_orchestrator(str(tmp_path))
        (tmp_path / "escalation_summary.json").write_text("not valid json <<<")

        with (
            patch.object(orc_module, "SYMLINK_TARGET", str(tmp_path)),
            patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
        ):
            result = orch._read_escalation_advisory()

        assert result is None
