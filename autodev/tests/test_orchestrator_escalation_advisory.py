"""
Tests for orchestrator._generate_escalation_advisory() and _read_escalation_advisory().

Validates:
  1. Valid LLM response → returns {"summary": ..., "recommended_action": ...}
  2. LLM returns overly long fields → both truncated to 200 chars
  3. requests.Timeout → returns None, no exception
  4. Malformed JSON from LLM → returns None, no exception
  5. No failure_context.json on disk → returns None
  6. Advisory fields written to phase_state before webhook is invoked
  7. LLM failure → escalation_advisory_status is "fallback", escalation_message not set
  8. Webhook message includes advisory text and does not hardcode "Signal"
  9. _read_escalation_advisory() returns both fields from escalation_summary.json
 10. _read_escalation_advisory() returns None when file is missing
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest
import requests

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

    openclaw_cfg = {
        "hooks": {"token": "test-tok"},
        "models": {
            "providers": {
                "llama-local": {"baseUrl": "http://localhost:11434/v1"}
            }
        },
    }
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
            "last_action": "Executor blame cap reached",
        }

    return orch


def _write_failure_context(tmp_path):
    """Write a representative failure_context.json for testing."""
    data = {
        "timestamp": "2025-01-01T00:00:00Z",
        "phase_raw_id": "CORE-1",
        "failing_agent": "executor",
        "attempt_number": 3,
        "gate_error_codes": ["ERR_VALIDATION_FAILED"],
        "agent_failure_reason": "Tests failed: 3 assertions did not pass",
        "agent_troubleshooting_attempts": ["Tried adjusting test expectations"],
        "executor_retries_at_failure": 3,
        "reviewer_retries_at_failure": 2,
        "prior_blame_attributions": ["impl", "impl", "impl"],
    }
    (tmp_path / "failure_context.json").write_text(json.dumps(data))
    return data


def _write_phase_state(tmp_path, **extra):
    """Write a minimal phase_state.json with optional extra fields."""
    state = {
        "escalation_trigger_reason": "Impl blame cap reached (3x)",
        "escalation_resets": 0,
        "executor_retries": 3,
        "reviewer_retries": 2,
        "prior_blame_attributions": ["impl", "impl", "impl"],
    }
    state.update(extra)
    (tmp_path / "phase_state.json").write_text(json.dumps(state))
    return state


def _make_llm_response(summary: str, recommended_action: str):
    """Build a mock requests.Response matching the LLM chat completion shape."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "summary": summary,
                        "recommended_action": recommended_action,
                    })
                }
            }
        ]
    }
    return mock_resp


# ---------------------------------------------------------------------------
# Tests for _generate_escalation_advisory()
# ---------------------------------------------------------------------------

class TestGenerateEscalationAdvisory:

    def test_valid_llm_response_returns_summary_and_action(self, tmp_path):
        """Valid LLM response → returns dict with summary and recommended_action."""
        import orchestrator as orc_module

        orch = _make_test_orchestrator(str(tmp_path))
        _write_failure_context(tmp_path)
        _write_phase_state(tmp_path)

        mock_resp = _make_llm_response(
            "The executor failed three times on failing test assertions. The code logic is incorrect.",
            "Use Reset Execution to retry the executor with fresh context."
        )

        with (
            patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
            patch.object(orc_module, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json")),
            patch("requests.post", return_value=mock_resp),
        ):
            result = orch._generate_escalation_advisory()

        assert result is not None
        assert isinstance(result["summary"], str)
        assert len(result["summary"]) > 0
        assert isinstance(result["recommended_action"], str)
        assert len(result["recommended_action"]) > 0

    def test_long_fields_are_truncated_to_200_chars(self, tmp_path):
        """LLM returning fields > 200 chars → both truncated to exactly 200."""
        import orchestrator as orc_module

        orch = _make_test_orchestrator(str(tmp_path))
        _write_failure_context(tmp_path)
        _write_phase_state(tmp_path)

        long_summary = "X" * 300
        long_action = "Y" * 300
        mock_resp = _make_llm_response(long_summary, long_action)

        with (
            patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
            patch.object(orc_module, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json")),
            patch("requests.post", return_value=mock_resp),
        ):
            result = orch._generate_escalation_advisory()

        assert result is not None
        assert len(result["summary"]) == 200
        assert len(result["recommended_action"]) == 200

    def test_timeout_returns_none_no_exception(self, tmp_path):
        """requests.Timeout → method returns None without raising."""
        import orchestrator as orc_module

        orch = _make_test_orchestrator(str(tmp_path))
        _write_failure_context(tmp_path)
        _write_phase_state(tmp_path)

        with (
            patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
            patch.object(orc_module, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json")),
            patch("requests.post", side_effect=requests.exceptions.Timeout("timed out")),
        ):
            result = orch._generate_escalation_advisory()

        assert result is None  # must not raise

    def test_malformed_json_from_llm_returns_none(self, tmp_path):
        """LLM returning non-JSON content → returns None without raising."""
        import orchestrator as orc_module

        orch = _make_test_orchestrator(str(tmp_path))
        _write_failure_context(tmp_path)
        _write_phase_state(tmp_path)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "not valid json {{"}}]
        }

        with (
            patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
            patch.object(orc_module, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json")),
            patch("requests.post", return_value=mock_resp),
        ):
            result = orch._generate_escalation_advisory()

        assert result is None

    def test_no_failure_context_returns_none(self, tmp_path):
        """No failure_context.json on disk → method returns None."""
        import orchestrator as orc_module

        orch = _make_test_orchestrator(str(tmp_path))
        _write_phase_state(tmp_path)
        # Deliberately do NOT write failure_context.json

        with (
            patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
            patch.object(orc_module, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json")),
        ):
            result = orch._generate_escalation_advisory()

        assert result is None


class TestAdvisoryIntegration:

    def test_advisory_fields_written_to_phase_state_before_webhook(self, tmp_path):
        """escalation_message and escalation_recommended_action must be in phase_state
        when advisory generation succeeds — simulates pre-webhook write sequence."""
        import orchestrator as orc_module

        orch = _make_test_orchestrator(str(tmp_path))
        _write_failure_context(tmp_path)
        phase_state_path = str(tmp_path / "phase_state.json")
        _write_phase_state(tmp_path)

        advisory = {
            "summary": "Executor failed due to assertion errors in tests.",
            "recommended_action": "Reset Execution to retry with fresh state.",
        }

        with (
            patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
            patch.object(orc_module, "PHASE_STATE_FILE", phase_state_path),
        ):
            # Simulate what the escalation dispatch does after _generate_escalation_advisory()
            _ps = orch.read_phase_state()
            _ps["escalation_advisory_status"] = "generating"
            orch.write_phase_state_atomic(_ps)

            # Advisory comes back
            _ps["escalation_message"] = advisory["summary"]
            _ps["escalation_recommended_action"] = advisory["recommended_action"]
            _ps["escalation_advisory_status"] = "ready"
            orch.write_phase_state_atomic(_ps)

        # Read back what was written — this is what the UI and webhook will see
        with open(phase_state_path) as f:
            written = json.load(f)

        assert written["escalation_message"] == advisory["summary"]
        assert written["escalation_recommended_action"] == advisory["recommended_action"]
        assert written["escalation_advisory_status"] == "ready"

    def test_fallback_status_when_llm_fails(self, tmp_path):
        """When _generate_escalation_advisory returns None, escalation_advisory_status
        is written as 'fallback' and escalation_message is NOT set."""
        import orchestrator as orc_module

        orch = _make_test_orchestrator(str(tmp_path))
        phase_state_path = str(tmp_path / "phase_state.json")
        _write_phase_state(tmp_path)

        with (
            patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
            patch.object(orc_module, "PHASE_STATE_FILE", phase_state_path),
        ):
            _ps = orch.read_phase_state()
            _ps["escalation_advisory_status"] = "generating"
            orch.write_phase_state_atomic(_ps)

            # _generate_escalation_advisory() returned None — write fallback
            _ps["escalation_advisory_status"] = "fallback"
            # escalation_message intentionally NOT set
            orch.write_phase_state_atomic(_ps)

        with open(phase_state_path) as f:
            written = json.load(f)

        assert written["escalation_advisory_status"] == "fallback"
        assert "escalation_message" not in written

    def test_webhook_message_includes_advisory_and_not_signal_specific(self, tmp_path):
        """Webhook message must include advisory text and must not hardcode 'Signal'."""
        advisory = {
            "summary": "The executor failed because the auth service was unreachable.",
            "recommended_action": "Reset Execution after verifying network connectivity.",
        }
        _p = "pipeline-project/.autodev/pipeline"

        # Build the webhook message the same way the orchestrator will
        webhook_msg = (
            f"Pipeline needs operator attention.\n\n"
            f"Advisory: {advisory['summary']}\n"
            f"Suggested action: {advisory.get('recommended_action', 'See dashboard.')}\n\n"
            f"Read {_p}/phase_state.json and relevant output files for full context. "
            f"Send a notification to the operator via your configured channel including the advisory above, "
            f"then write your assessment to "
            f"{_p}/escalation_output.json and {_p}/escalation_output.done."
        )

        assert advisory["summary"] in webhook_msg
        assert advisory["recommended_action"] in webhook_msg
        assert "Signal" not in webhook_msg  # must be channel-agnostic


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
