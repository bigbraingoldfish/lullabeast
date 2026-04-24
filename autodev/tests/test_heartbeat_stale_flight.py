"""Deterministic stale mid-flight recovery when lock is free (orchestrator dead)."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PIPELINE_DIR = os.path.join(_REPO_DIR, "autodev", "pipeline")
for _p in [_PIPELINE_DIR, _REPO_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _write_state(path: str, state: dict) -> None:
    with open(path, "w") as f:
        json.dump(state, f)


class TestHeartbeatStaleFlight:
    """Orphaned RUNNING / WAITING_FOR_SENTINEL with stale last_action_timestamp restarts without LLM."""

    def test_stale_running_state_restarts_orchestrator(self, tmp_workspace):
        import heartbeat_cron

        now = datetime.now(timezone.utc)
        stale_ts = (now - timedelta(minutes=16)).isoformat()
        state = {
            "pipeline_status": "RUNNING",
            "current_agent": "planner",
            "last_action_timestamp": stale_ts,
            "project_path": tmp_workspace,
            "last_action": "test",
        }
        state_file = os.path.join(tmp_workspace, "pipeline_state.json")
        lock_file = os.path.join(tmp_workspace, "pipeline.lock")
        _write_state(state_file, state)

        with (
            patch.object(heartbeat_cron, "STATE_FILE", state_file),
            patch.object(heartbeat_cron, "LOCK_FILE", lock_file),
            patch("heartbeat_cron.query_heartbeat_model") as mock_model,
            patch("heartbeat_cron.start_orchestrator") as mock_start,
            patch("heartbeat_cron.fcntl.flock"),
        ):
            heartbeat_cron.run_heartbeat()

        mock_model.assert_not_called()
        mock_start.assert_called_once_with(tmp_workspace)

    def test_stale_waiting_for_sentinel_restarts_orchestrator(self, tmp_workspace):
        import heartbeat_cron

        now = datetime.now(timezone.utc)
        stale_ts = (now - timedelta(minutes=16)).isoformat()
        state = {
            "pipeline_status": "WAITING_FOR_SENTINEL",
            "current_agent": "planner",
            "last_action_timestamp": stale_ts,
            "project_path": tmp_workspace,
        }
        state_file = os.path.join(tmp_workspace, "pipeline_state.json")
        lock_file = os.path.join(tmp_workspace, "pipeline.lock")
        _write_state(state_file, state)

        with (
            patch.object(heartbeat_cron, "STATE_FILE", state_file),
            patch.object(heartbeat_cron, "LOCK_FILE", lock_file),
            patch("heartbeat_cron.query_heartbeat_model") as mock_model,
            patch("heartbeat_cron.start_orchestrator") as mock_start,
            patch("heartbeat_cron.fcntl.flock"),
        ):
            heartbeat_cron.run_heartbeat()

        mock_model.assert_not_called()
        mock_start.assert_called_once_with(tmp_workspace)

    def test_recent_running_state_falls_through_to_model(self, tmp_workspace):
        import heartbeat_cron

        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(minutes=5)).isoformat()
        state = {
            "pipeline_status": "RUNNING",
            "project_path": tmp_workspace,
            "last_action_timestamp": recent_ts,
        }
        state_file = os.path.join(tmp_workspace, "pipeline_state.json")
        lock_file = os.path.join(tmp_workspace, "pipeline.lock")
        _write_state(state_file, state)

        with (
            patch.object(heartbeat_cron, "STATE_FILE", state_file),
            patch.object(heartbeat_cron, "LOCK_FILE", lock_file),
            patch("heartbeat_cron.query_heartbeat_model", return_value="WAIT") as mock_model,
            patch("heartbeat_cron.start_orchestrator") as mock_start,
            patch("heartbeat_cron.fcntl.flock"),
        ):
            heartbeat_cron.run_heartbeat()

        mock_model.assert_called_once()
        mock_start.assert_not_called()

    def test_stale_waiting_for_human_does_not_restart(self, tmp_workspace):
        import heartbeat_cron

        now = datetime.now(timezone.utc)
        stale_ts = (now - timedelta(minutes=60)).isoformat()
        state = {
            "pipeline_status": "WAITING_FOR_HUMAN",
            "project_path": tmp_workspace,
            "last_action_timestamp": stale_ts,
        }
        state_file = os.path.join(tmp_workspace, "pipeline_state.json")
        lock_file = os.path.join(tmp_workspace, "pipeline.lock")
        _write_state(state_file, state)

        with (
            patch.object(heartbeat_cron, "STATE_FILE", state_file),
            patch.object(heartbeat_cron, "LOCK_FILE", lock_file),
            patch("heartbeat_cron.query_heartbeat_model", return_value="WAIT") as mock_model,
            patch("heartbeat_cron.start_orchestrator") as mock_start,
            patch("heartbeat_cron.fcntl.flock"),
        ):
            heartbeat_cron.run_heartbeat()

        mock_start.assert_not_called()

    def test_stale_threshold_boundary(self, tmp_workspace):
        """Stale uses strict >15min; allow clock skew between writing ts and heartbeat."""
        import heartbeat_cron

        now = datetime.now(timezone.utc)
        # Safely under 15m even after a few hundred ms of test overhead
        under_threshold = (now - timedelta(minutes=14, seconds=50)).isoformat()
        # Clearly over 15m
        over_threshold = (now - timedelta(minutes=15, seconds=30)).isoformat()
        lock_file = os.path.join(tmp_workspace, "pipeline.lock")

        state_under = {
            "pipeline_status": "RUNNING",
            "project_path": tmp_workspace,
            "last_action_timestamp": under_threshold,
        }
        state_file_under = os.path.join(tmp_workspace, "state_under.json")
        _write_state(state_file_under, state_under)

        with (
            patch.object(heartbeat_cron, "STATE_FILE", state_file_under),
            patch.object(heartbeat_cron, "LOCK_FILE", lock_file),
            patch("heartbeat_cron.query_heartbeat_model", return_value="WAIT") as mock_model_e,
            patch("heartbeat_cron.start_orchestrator") as mock_start_e,
            patch("heartbeat_cron.fcntl.flock"),
        ):
            heartbeat_cron.run_heartbeat()

        mock_start_e.assert_not_called()
        mock_model_e.assert_called_once()

        state_over = {
            "pipeline_status": "RUNNING",
            "project_path": tmp_workspace,
            "last_action_timestamp": over_threshold,
        }
        state_file_over = os.path.join(tmp_workspace, "state_over.json")
        _write_state(state_file_over, state_over)

        with (
            patch.object(heartbeat_cron, "STATE_FILE", state_file_over),
            patch.object(heartbeat_cron, "LOCK_FILE", lock_file),
            patch("heartbeat_cron.query_heartbeat_model") as mock_model_o,
            patch("heartbeat_cron.start_orchestrator") as mock_start_o,
            patch("heartbeat_cron.fcntl.flock"),
        ):
            heartbeat_cron.run_heartbeat()

        mock_model_o.assert_not_called()
        mock_start_o.assert_called_once_with(tmp_workspace)
