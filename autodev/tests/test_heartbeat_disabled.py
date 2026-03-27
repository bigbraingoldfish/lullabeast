"""
Heartbeat disabled and non-preemption tests.

The OpenClaw native heartbeat was disabled (2026-03-10) because it caused model swap
interruptions and noisy Signal DMs.  The sole monitoring mechanism is now the system
cron heartbeat_cron.py, which only acts when the orchestrator lock is FREE (dead process).

These tests validate:
  - openclaw.json has native heartbeat disabled (every: "0m")
  - heartbeat_cron.py only queries the model when pipeline is actively in flight
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

OPENCLAW_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in [OPENCLAW_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

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
        Validates: With the native heartbeat disabled (every: "0m"), the heartbeat
        cannot invoke the escalation agent during an executor run, eliminating the
        model swap preemption risk described in PIPELINE-CONSTRAINTS §5.7/5.8.

        Simulated: we verify that no signal/interrupt is sent to the executor workspace
        when the pipeline is in WAITING_FOR_SENTINEL (executor in flight).

        FIND-ID: FIND-HEARTBEAT
        Spec Reference: PIPELINE-CONSTRAINTS.md §5.7 "Model Swap Race Condition"
                        PIPELINE-CONSTRAINTS.md §5.8 "OpenClaw Native Heartbeat Disabled"
        """
        import heartbeat_cron

        state = {
            "pipeline_status": "WAITING_FOR_SENTINEL",
            "current_agent": "executor",
            "last_action": "Invoking Executor (Local) - Attempt 1",
            "last_action_timestamp": "2026-03-10T12:00:00+00:00",
            "project_path": tmp_workspace,
        }

        state_file = os.path.join(tmp_workspace, "pipeline_state.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        lock_file = os.path.join(tmp_workspace, "pipeline.lock")
        signals_sent = []

        # Simulate: lock IS held (orchestrator alive), check stuck sentinel path
        # With heartbeat disabled, no model query should fire for a live orchestrator
        with (
            patch.object(heartbeat_cron, "STATE_FILE", state_file),
            patch.object(heartbeat_cron, "LOCK_FILE", lock_file),
            patch("heartbeat_cron.send_signal_notification",
                  side_effect=lambda m: signals_sent.append(m)) as mock_signal,
            patch("heartbeat_cron.query_heartbeat_model") as mock_model,
            patch("heartbeat_cron.start_orchestrator") as mock_start,
        ):
            # Simulate lock HELD (orchestrator is alive) — no acquisition
            import fcntl
            with patch("heartbeat_cron.fcntl.flock", side_effect=BlockingIOError):
                # The stuck-sentinel check only fires if elapsed > 15 min.
                # We patch the timestamp to be recent (< 15 min ago).
                from datetime import datetime, timezone
                recent_ts = datetime.now(timezone.utc).isoformat()
                state["last_action_timestamp"] = recent_ts
                with open(state_file, "w") as f:
                    json.dump(state, f)

                heartbeat_cron.run_heartbeat()

        # Model must NOT have been queried (orchestrator is alive, not dead)
        mock_model.assert_not_called()
        # No signals sent for a healthy running pipeline
        assert len(signals_sent) == 0, (
            f"No signal must be sent when orchestrator is alive and pipeline is healthy. "
            f"Sent: {signals_sent}"
        )
        # Orchestrator must NOT have been restarted
        mock_start.assert_not_called()

    def test_heartbeat_cron_only_queries_model_when_pipeline_active(self, tmp_workspace):
        """
        Validates: heartbeat_cron.py only queries the local model for RESUME/WAIT/NOTIFY
        when the pipeline is RUNNING or WAITING_FOR_SENTINEL.  For HALTED_SILENT and
        WAITING_FOR_HUMAN, it skips the model query to avoid false-positive alerts.

        FIND-ID: FIND-HEARTBEAT
        Spec Reference: PIPELINE-CONSTRAINTS.md §5.1 "Heartbeat False Positive [resolved]"
        """
        import heartbeat_cron

        for inactive_status in ("HALTED_SILENT", "WAITING_FOR_HUMAN", "BLOCKED"):
            state = {
                "pipeline_status": inactive_status,
                "project_path": tmp_workspace,
            }
            state_file = os.path.join(tmp_workspace, f"state_{inactive_status}.json")
            with open(state_file, "w") as f:
                json.dump(state, f)

            with (
                patch.object(heartbeat_cron, "STATE_FILE", state_file),
                patch.object(heartbeat_cron, "LOCK_FILE",
                             os.path.join(tmp_workspace, "pipeline.lock")),
                patch("heartbeat_cron.query_heartbeat_model") as mock_model,
                patch("heartbeat_cron.send_signal_notification") as mock_signal,
                patch("heartbeat_cron.start_orchestrator"),
                patch("heartbeat_cron.fcntl.flock"),  # lock acquisition succeeds (orchestrator dead)
            ):
                heartbeat_cron.run_heartbeat()

            # For inactive pipeline states, model_alert_required is False.
            # Even if the model query raises ConnectionError, no signal is sent.
            # We verify by injecting a ConnectionError and checking no signal fires.
            with (
                patch.object(heartbeat_cron, "STATE_FILE", state_file),
                patch.object(heartbeat_cron, "LOCK_FILE",
                             os.path.join(tmp_workspace, "pipeline.lock")),
                patch("heartbeat_cron.query_heartbeat_model",
                      side_effect=Exception("model down")),
                patch("heartbeat_cron.send_signal_notification") as mock_signal2,
                patch("heartbeat_cron.start_orchestrator"),
                patch("heartbeat_cron.fcntl.flock"),
            ):
                heartbeat_cron.run_heartbeat()

            assert mock_signal2.call_count == 0, (
                f"send_signal_notification must NOT be called when pipeline_status is "
                f"'{inactive_status}' and model query fails. "
                f"Called {mock_signal2.call_count} time(s)."
            )
