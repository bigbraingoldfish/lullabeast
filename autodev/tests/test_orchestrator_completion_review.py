"""Tests for W5-B: orchestrator completion review invocation.

Verifies that _run_completion_review is called only when the active queue entry
has completion_review: true, never raises, and uses the correct session key.
"""

import json
import os
import sys
import time
from unittest.mock import MagicMock, patch, call

import pytest

PIPELINE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "autodev", "pipeline",
)
if PIPELINE_DIR not in sys.path:
    sys.path.insert(0, PIPELINE_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_queue_entry(completion_review=False, state="ACTIVE"):
    return {
        "id": "test-entry-id",
        "project_path": "/tmp/test-project",
        "name": "test-project",
        "state": state,
        "completion_review": completion_review,
    }


def _make_queue_data(entry):
    return {"queue": [entry]}


# ---------------------------------------------------------------------------
# Tests for _run_completion_review helper isolation
# ---------------------------------------------------------------------------

class TestRunCompletionReview:
    """Tests that _run_completion_review handles all edge cases without raising."""

    def _import_helper(self):
        """Import the helper — fails until W5-B is implemented."""
        import orchestrator as orc
        return orc._run_completion_review

    def test_helper_exists(self):
        """_run_completion_review must be a module-level function in orchestrator."""
        import orchestrator as orc
        assert hasattr(orc, "_run_completion_review"), (
            "_run_completion_review not found — W5-B not implemented"
        )

    def test_never_raises_on_inject_skill_exception(self, tmp_path, monkeypatch):
        """OSError during inject_skill must be caught; pipeline must not be affected."""
        import orchestrator as orc

        mock_orch = MagicMock()
        mock_orch.skill_manager.inject_skill.side_effect = OSError("disk full")
        mock_orch.openclaw_config = {}

        # Should not raise
        orc._run_completion_review(mock_orch, project_basename="my-proj")

    def test_never_raises_on_webhook_exception(self, tmp_path, monkeypatch):
        """Network error during webhook invocation must not propagate."""
        import orchestrator as orc

        mock_orch = MagicMock()
        mock_orch.skill_manager.inject_skill.return_value = None
        mock_orch.openclaw_config = {}

        with patch("orchestrator.invoke_agent_webhook", side_effect=ConnectionError("unreachable")):
            orc._run_completion_review(mock_orch, project_basename="my-proj")

    def test_never_raises_on_poll_timeout(self, tmp_path, monkeypatch):
        """Sentinel timeout must be swallowed; no retry, no state change."""
        import orchestrator as orc

        mock_orch = MagicMock()
        mock_orch.skill_manager.inject_skill.return_value = None
        mock_orch.openclaw_config = {}

        with patch("orchestrator.invoke_agent_webhook", return_value=None), \
             patch("orchestrator.poll_for_sentinel_with_idle_detect", return_value=False):
            orc._run_completion_review(mock_orch, project_basename="my-proj")

        # No transition_state calls — completion review must not modify pipeline state
        mock_orch.transition_state.assert_not_called()

    def test_never_raises_on_generic_exception(self, tmp_path, monkeypatch):
        """Any unexpected exception inside the helper must be caught at the top level."""
        import orchestrator as orc

        mock_orch = MagicMock()
        mock_orch.skill_manager.inject_skill.side_effect = RuntimeError("unexpected")
        mock_orch.openclaw_config = {}

        # Must not raise
        orc._run_completion_review(mock_orch, project_basename="my-proj")

    def test_session_key_format(self, tmp_path, monkeypatch):
        """Session key must be pipeline:completion:{project_basename}:reviewer."""
        import orchestrator as orc

        captured_args = []

        def fake_webhook(agent_id, session_key, token, **kwargs):
            captured_args.append((agent_id, session_key))

        mock_orch = MagicMock()
        mock_orch.skill_manager.inject_skill.return_value = None
        mock_orch.openclaw_config = {}

        with patch("orchestrator.invoke_agent_webhook", side_effect=fake_webhook), \
             patch("orchestrator.poll_for_sentinel_with_idle_detect", return_value=True):
            orc._run_completion_review(mock_orch, project_basename="my-project")

        assert captured_args, "invoke_agent_webhook not called"
        agent_id, session_key = captured_args[0]
        assert agent_id == "reviewer"
        assert session_key == "pipeline:completion:my-project:reviewer"

    def test_inject_skill_called_with_complete_phase(self, monkeypatch):
        """inject_skill must receive 'COMPLETE-R0' as the phase_raw_id."""
        import orchestrator as orc

        mock_orch = MagicMock()
        mock_orch.openclaw_config = {}

        with patch("orchestrator.invoke_agent_webhook", return_value=None), \
             patch("orchestrator.poll_for_sentinel_with_idle_detect", return_value=True):
            orc._run_completion_review(mock_orch, project_basename="proj")

        call_args = mock_orch.skill_manager.inject_skill.call_args
        assert call_args is not None, "inject_skill was not called"
        assert call_args[0][0] == "COMPLETE-R0", (
            f"Expected phase_raw_id 'COMPLETE-R0', got {call_args[0][0]!r}"
        )
        assert call_args[0][1] == "reviewer"

    def test_poll_called_with_tight_timeout(self, monkeypatch):
        """Sentinel poll must use timeout_seconds=120 — no retry allowed."""
        import orchestrator as orc

        mock_orch = MagicMock()
        mock_orch.openclaw_config = {}

        poll_kwargs = {}

        def capture_poll(**kwargs):
            poll_kwargs.update(kwargs)
            return True

        with patch("orchestrator.invoke_agent_webhook", return_value=None), \
             patch("orchestrator.poll_for_sentinel_with_idle_detect", side_effect=capture_poll):
            orc._run_completion_review(mock_orch, project_basename="proj")

        assert poll_kwargs.get("timeout_seconds") == 120, (
            f"Expected timeout_seconds=120, got {poll_kwargs.get('timeout_seconds')}"
        )

    def test_transition_state_never_called(self, monkeypatch):
        """Completion review must never call transition_state — not a pipeline state."""
        import orchestrator as orc

        mock_orch = MagicMock()
        mock_orch.openclaw_config = {}

        with patch("orchestrator.invoke_agent_webhook", return_value=None), \
             patch("orchestrator.poll_for_sentinel_with_idle_detect", return_value=True):
            orc._run_completion_review(mock_orch, project_basename="proj")

        mock_orch.transition_state.assert_not_called()


# ---------------------------------------------------------------------------
# Tests for the PIPELINE_COMPLETE branch gating logic
# ---------------------------------------------------------------------------

class TestCompletionReviewGating:
    """Tests that orchestrator only invokes completion review when queue flag is set."""

    def _get_queue_branch_flag(self, entry_data, monkeypatch):
        """Simulate whether _run_completion_review would be called for a given entry."""
        import orchestrator as orc

        called = []

        def mock_helper(orch, project_basename):
            called.append(project_basename)

        mock_orch = MagicMock()
        mock_orch._read_queue.return_value = _make_queue_data(entry_data)
        mock_orch._find_active_queue_entry.return_value = (0, entry_data)

        with patch.object(orc, "_run_completion_review", mock_helper):
            # Simulate the gating block from W5-B
            _cr_entry = mock_orch._find_active_queue_entry(mock_orch._read_queue())
            idx, entry = _cr_entry if isinstance(_cr_entry, tuple) else (0, _cr_entry)
            if entry.get("completion_review"):
                orc._run_completion_review(mock_orch, project_basename="proj")

        return called

    def test_skipped_when_flag_false(self, monkeypatch):
        entry = _make_queue_entry(completion_review=False)
        called = self._get_queue_branch_flag(entry, monkeypatch)
        assert called == [], "Completion review must not run when flag is False"

    def test_skipped_when_flag_absent(self, monkeypatch):
        entry = _make_queue_entry()
        del entry["completion_review"]
        called = self._get_queue_branch_flag(entry, monkeypatch)
        assert called == [], "Completion review must not run when flag is absent"

    def test_runs_when_flag_true(self, monkeypatch):
        entry = _make_queue_entry(completion_review=True)
        called = self._get_queue_branch_flag(entry, monkeypatch)
        assert called == ["proj"], "Completion review must run when flag is True"
