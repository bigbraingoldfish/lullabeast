"""Phase 5 — T5.2: heartbeat splits ``JSONDecodeError`` out of its catch-all.

Both ``json.load`` sites used to sit inside one broad ``except Exception:
print("[ERROR] …")``. The damaging case is **orchestrator dead + corrupt
``pipeline_state.json``**: the watchdog whose only job is recovery read the
corrupt file as a vague error and silently did nothing every 30 min. Now:
  * dead + corrupt  -> ``[CRITICAL]`` + exit 1 (recovery BLOCKED, visible)
  * alive + corrupt -> ``[WARN]`` + return (benign — the live orchestrator owns
    and rewrites the file)

``fcntl.flock`` is patched to control the lock state: a no-op mock means the
lock was acquired (orchestrator dead); a first-call ``BlockingIOError`` means the
lock is held (orchestrator alive) — the same idiom the existing heartbeat tests
use.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PIPELINE_DIR = os.path.join(_REPO_DIR, "autodev", "pipeline")
for _p in (_PIPELINE_DIR, _REPO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _write(path, text):
    with open(path, "w") as handle:
        handle.write(text)


class TestHeartbeatCorruptState:
    def test_corrupt_state_while_dead_exits_loud(self, tmp_workspace, capsys):
        import heartbeat_cron

        state_file = os.path.join(tmp_workspace, "pipeline_state.json")
        lock_file = os.path.join(tmp_workspace, "pipeline.lock")
        _write(state_file, "{ this is not valid json")

        with (
            patch.object(heartbeat_cron, "STATE_FILE", state_file),
            patch.object(heartbeat_cron, "LOCK_FILE", lock_file),
            patch("heartbeat_cron.start_orchestrator") as mock_start,
            patch("heartbeat_cron.fcntl.flock"),  # no-op -> lock acquired (dead)
        ):
            with pytest.raises(SystemExit) as exc:
                heartbeat_cron.run_heartbeat()

        assert exc.value.code == 1
        mock_start.assert_not_called()
        assert "[CRITICAL]" in capsys.readouterr().out

    def test_corrupt_state_while_alive_warns_and_returns(self, tmp_workspace, capsys):
        import heartbeat_cron

        state_file = os.path.join(tmp_workspace, "pipeline_state.json")
        lock_file = os.path.join(tmp_workspace, "pipeline.lock")
        _write(state_file, "{ corrupt")

        with (
            patch.object(heartbeat_cron, "STATE_FILE", state_file),
            patch.object(heartbeat_cron, "LOCK_FILE", lock_file),
            patch("heartbeat_cron.start_orchestrator") as mock_start,
            # First flock (acquire) raises -> lock held (alive); second (unlock) no-ops.
            patch("heartbeat_cron.fcntl.flock", side_effect=[BlockingIOError(), None]),
        ):
            heartbeat_cron.run_heartbeat()  # must NOT raise

        mock_start.assert_not_called()
        assert "[WARN]" in capsys.readouterr().out

    def test_valid_state_dead_stale_still_restarts(self, tmp_workspace):
        """Regression: the JSONDecodeError split must not disturb happy recovery."""
        import heartbeat_cron

        stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat()
        state = {
            "pipeline_status": "RUNNING",
            "project_path": tmp_workspace,
            "last_action_timestamp": stale_ts,
        }
        state_file = os.path.join(tmp_workspace, "pipeline_state.json")
        lock_file = os.path.join(tmp_workspace, "pipeline.lock")
        with open(state_file, "w") as handle:
            json.dump(state, handle)

        with (
            patch.object(heartbeat_cron, "STATE_FILE", state_file),
            patch.object(heartbeat_cron, "LOCK_FILE", lock_file),
            patch("heartbeat_cron.start_orchestrator") as mock_start,
            patch("heartbeat_cron.fcntl.flock"),
        ):
            heartbeat_cron.run_heartbeat()

        mock_start.assert_called_once_with(tmp_workspace)
