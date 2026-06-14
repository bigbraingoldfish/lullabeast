"""
Heartbeat disabled and non-preemption tests.

The OpenClaw native heartbeat was disabled (2026-03-10) because it caused model swap
interruptions and noisy Signal DMs.  The sole monitoring mechanism is now the system
cron heartbeat_cron.py, which only acts when the orchestrator lock is FREE (dead process).

The model-query path (query_heartbeat_model / send_raw_notification) was removed
from heartbeat_cron.py because the model returned NOTIFY for normal idle/terminal states
(IDLE, PIPELINE_COMPLETE, STOPPED, etc.), causing false-positive Signal notifications.
All decisions are now fully deterministic — no model query, no outbound notifications.

These tests validate:
  - openclaw.json has native heartbeat disabled (every: "0m")
  - heartbeat_cron.py never calls start_orchestrator for idle/terminal states
  - Executor cannot be preempted by the native heartbeat (it is off)

FIND-ID: FIND-POLLING / FIND-HEARTBEAT
Spec Reference: PIPELINE-CONSTRAINTS.md §5.8 "OpenClaw Native Heartbeat Disabled"
                PIPELINE-SPEC.md §12 "Infrastructure > Heartbeat Cron"
"""

import json
import os
import sys
import tempfile
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PIPELINE_DIR = os.path.join(_REPO_DIR, "autodev", "pipeline")
for _p in [_REPO_DIR, _PIPELINE_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from env_resolvers import resolve_openclaw_root  # noqa: E402

OPENCLAW_DIR = resolve_openclaw_root()
OPENCLAW_JSON = os.path.join(OPENCLAW_DIR, "openclaw.json")


class TestHeartbeatDisabled:

    def test_heartbeat_cron_not_running(self):
        """
        Validates: The openclaw.json configuration has agents.defaults.heartbeat.every
        set to "0m", which disables the OpenClaw native heartbeat.

        FIND-ID: FIND-POLLING
        Spec Reference: PIPELINE-CONSTRAINTS.md §5.8 "OpenClaw Native Heartbeat Disabled"
        """
        if not os.path.exists(OPENCLAW_JSON):
            pytest.skip("openclaw.json not present in environment — cannot validate config")

        with open(OPENCLAW_JSON) as f:
            config = json.load(f)

        heartbeat_cfg = (
            config.get("agents", {})
                  .get("defaults", {})
                  .get("heartbeat", {})
        )
        heartbeat_every = heartbeat_cfg.get("every", "NOT_SET")

        assert heartbeat_every == "0m", (
            f"agents.defaults.heartbeat.every must be '0m' to disable the native heartbeat. "
            f"Got: {heartbeat_every!r}.  "
            f"The native heartbeat causes model swap interruptions (PIPELINE-CONSTRAINTS §5.8)."
        )

    def test_heartbeat_cannot_preempt_executor(self, tmp_workspace):
        """
        Validates: The heartbeat cron never kills or restarts the orchestrator when
        the lock is held (orchestrator alive), regardless of how long the pipeline has
        been in WAITING_FOR_SENTINEL.

        Previously a 15-minute stuck-sentinel SIGTERM existed in this path. It was
        removed because last_action_timestamp is written once when the orchestrator
        transitions to WAITING_FOR_SENTINEL (before the webhook fires) and is never
        updated until the agent finishes. A complex phase legitimately running > 15 min
        would trigger the kill, burning retry attempts on work that was completing fine.

        With the agent_end plugin, poll_for_sentinel unblocks the moment the session
        closes. There is no scenario where the orchestrator needs an external kick while
        it is alive and actively polling.

        Tested with a stale timestamp (60 min) to confirm no SIGTERM is ever sent.

        FIND-ID: FIND-HEARTBEAT
        Spec Reference: PIPELINE-CONSTRAINTS.md §5.7 "Model Swap Race Condition"
                        PIPELINE-CONSTRAINTS.md §5.8 "OpenClaw Native Heartbeat Disabled"
        """
        import heartbeat_cron
        from datetime import datetime, timezone, timedelta

        # Deliberately use a very stale timestamp — the old code would have SIGTERMed here
        stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        state = {
            "pipeline_status": "WAITING_FOR_SENTINEL",
            "current_agent": "executor",
            "last_action": "Invoking Executor (Local) - Attempt 1",
            "last_action_timestamp": stale_ts,
            "project_path": tmp_workspace,
        }

        state_file = os.path.join(tmp_workspace, "pipeline_state.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        lock_file = os.path.join(tmp_workspace, "pipeline.lock")

        with (
            patch.object(heartbeat_cron, "STATE_FILE", state_file),
            patch.object(heartbeat_cron, "LOCK_FILE", lock_file),
            patch("heartbeat_cron.start_orchestrator") as mock_start,
            patch("heartbeat_cron.fcntl.flock", side_effect=BlockingIOError),
        ):
            heartbeat_cron.run_heartbeat()

        # Lock is held — orchestrator is alive. Never restart, never kill.
        mock_start.assert_not_called()

    def test_heartbeat_does_not_restart_for_idle_states(self, tmp_workspace):
        """
        Validates: heartbeat_cron.py never restarts the orchestrator when the pipeline
        is in an idle or terminal state, regardless of how long it has been idle.

        All of these states are expected to exist without a running orchestrator process.

        FIND-ID: FIND-HEARTBEAT
        Spec Reference: PIPELINE-CONSTRAINTS.md §5.1 "Heartbeat False Positive [resolved]"
        """
        import heartbeat_cron
        from datetime import datetime, timezone, timedelta

        idle_states = (
            "IDLE",
            "PIPELINE_COMPLETE",
            "STOPPED",
            "QUEUE_HALTED",
            "HALTED_SILENT",
            "BLOCKED",
            "WAITING_FOR_HUMAN",
        )

        for inactive_status in idle_states:
            state = {
                "pipeline_status": inactive_status,
                "project_path": tmp_workspace,
                # Stale timestamp — would trigger restart if incorrectly classified as active
                "last_action_timestamp": (
                    datetime.now(timezone.utc) - timedelta(hours=2)
                ).isoformat(),
            }
            state_file = os.path.join(tmp_workspace, f"state_{inactive_status}.json")
            with open(state_file, "w") as f:
                json.dump(state, f)

            with (
                patch.object(heartbeat_cron, "STATE_FILE", state_file),
                patch.object(heartbeat_cron, "LOCK_FILE",
                             os.path.join(tmp_workspace, "pipeline.lock")),
                patch("heartbeat_cron.start_orchestrator") as mock_start,
                patch("heartbeat_cron.fcntl.flock"),  # lock acquisition succeeds (orchestrator dead)
            ):
                heartbeat_cron.run_heartbeat()

            assert mock_start.call_count == 0, (
                f"start_orchestrator must NOT be called when pipeline_status is "
                f"'{inactive_status}'. Called {mock_start.call_count} time(s)."
            )
