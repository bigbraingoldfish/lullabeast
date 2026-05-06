"""
Polling mechanism tests.

Validates that:
  - Sentinel polling uses time.sleep(2) loops (NOT time.sleep(60) or inotify)
  - wait_for_model_stable() is called between executor and reviewer handoff
  - poll_for_sentinel detects completion before timeout (early exit)
  - Polling interval is not hardcoded to a fixed value that creates race conditions
  - Stale sentinel guard (min_sentinel_mtime) works in poll_for_sentinel

FIND-ID: FIND-POLLING
Spec Reference: PIPELINE-SPEC.md §2 "Event Loop" (polling loop, no inotify)
                PIPELINE-CONSTRAINTS.md §5.7 "Model Swap Race Condition [RESOLVED]"
                PIPELINE-CONSTRAINTS.md §5.8 "OpenClaw Native Heartbeat Disabled"
"""

import inspect
import json
import os
import sys
import time
from contextlib import ExitStack
from unittest.mock import MagicMock, call, patch

import pytest

OPENCLAW_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE_SCRIPTS_DIR = os.path.join(OPENCLAW_DIR, "autodev", "pipeline", "gate_scripts")
for _p in [GATE_SCRIPTS_DIR, OPENCLAW_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


class TestPollingMechanism:

    def test_polling_replaces_sleep_between_agent_invocations(self):
        """
        Validates: Between executor and reviewer invocations, the orchestrator calls
        wait_for_model_stable() rather than a fixed time.sleep(60).

        FIND-ID: FIND-POLLING
        Spec Reference: PIPELINE-CONSTRAINTS.md §5.7 "Model Swap Race Condition [RESOLVED]"
        """
        import orchestrator as orc_module
        from orchestrator import Orchestrator

        # wait_for_model_stable must exist on the Orchestrator
        assert hasattr(Orchestrator, "wait_for_model_stable"), (
            "Orchestrator must have wait_for_model_stable() method (OB-6 fix)."
        )

        # Inspect the executor branch: it must call wait_for_model_stable, not time.sleep(60)
        source = inspect.getsource(orc_module)
        # The 60-second fixed sleep that was removed
        assert "time.sleep(60)" not in source, (
            "orchestrator.py must not contain time.sleep(60) — this was the race-condition sleep "
            "that wait_for_model_stable() replaced."
        )
        # wait_for_model_stable must be called in the executor success path
        assert "wait_for_model_stable" in source, (
            "orchestrator.py must call wait_for_model_stable() between executor and reviewer."
        )

    def test_polling_interval_is_configurable(self):
        """
        Validates: The poll_for_sentinel function accepts a timeout_seconds parameter
        (not hardcoded), making the polling interval configurable per invocation.

        FIND-ID: FIND-POLLING
        Spec Reference: PIPELINE-SPEC.md §2 "Event Loop" (sentinel polling)
        """
        from sentinel_poller import poll_for_sentinel
        import inspect

        sig = inspect.signature(poll_for_sentinel)
        assert "timeout_seconds" in sig.parameters, (
            "poll_for_sentinel must accept a configurable timeout_seconds parameter."
        )

        param = sig.parameters["timeout_seconds"]
        assert param.default != inspect.Parameter.empty, (
            "timeout_seconds must have a default value so existing callers are not broken."
        )

    def test_polling_detects_agent_completion_before_timeout(self, tmp_workspace):
        """
        Validates: poll_for_sentinel returns True as soon as the .done file appears,
        without waiting for the full timeout to expire.

        FIND-ID: FIND-POLLING
        Spec Reference: PIPELINE-SPEC.md §2 "Event Loop" (sentinel polling)
        """
        from sentinel_poller import poll_for_sentinel

        sentinel_path = os.path.join(tmp_workspace, "test_sentinel.done")

        # Create the sentinel immediately
        open(sentinel_path, "w").close()

        start = time.monotonic()
        result = poll_for_sentinel(sentinel_path, timeout_seconds=30)
        elapsed = time.monotonic() - start

        assert result is True
        # Should return almost immediately (well under the 30s timeout)
        assert elapsed < 5.0, (
            f"poll_for_sentinel took {elapsed:.1f}s to detect an immediately-present file. "
            f"Must return on first check, not poll until timeout."
        )

    def test_polling_uses_sleep_not_inotify(self):
        """
        Validates: sentinel_poller.py uses time.sleep() not inotify/pyinotify.
        inotify has known issues with Linux symlinks/inode detachment.

        FIND-ID: FIND-POLLING
        Spec Reference: PIPELINE-SPEC.md §2 "Orchestrator > Event Loop"
                        ("do not use inotify or third-party file watchers")
        """
        import sentinel_poller
        import inspect

        import re
        source = inspect.getsource(sentinel_poller)
        # Check that inotify is not *imported or invoked* — docstring mentions are fine.
        inotify_usage = re.search(
            r'(?:import\s+inotify|from\s+inotify|inotifyx|pyinotify)', source
        )
        assert inotify_usage is None, (
            "sentinel_poller.py must not import or call inotify — spec explicitly prohibits it."
        )
        assert "time.sleep" in source, (
            "sentinel_poller.py must use time.sleep() for polling as specified."
        )

    def test_stale_sentinel_discarded_orphaned_session(self, tmp_workspace):
        """
        Validates: when min_sentinel_mtime is supplied and the sentinel file's mtime
        is OLDER than that value, the sentinel is treated as belonging to an orphaned
        prior session (whose code was already cleaned by git reset) and is discarded.
        Polling continues until a fresh sentinel appears, preserving executor_retries.

        This guard moved from poll_for_sentinel_with_idle_detect into poll_for_sentinel
        as part of the agent_end plugin integration.

        FIND-ID: FIND-POLLING
        """
        import threading
        from sentinel_poller import poll_for_sentinel

        sentinel_path = os.path.join(tmp_workspace, "executor_output.done")

        # Write a "stale" sentinel that predates the attempt-start timestamp
        with open(sentinel_path, "w") as f:
            f.write("")

        # Record attempt_start AFTER the stale sentinel was written
        time.sleep(0.05)
        attempt_start = time.time()

        # A fresh sentinel will appear 1.5s later (simulates current session completing)
        def _write_fresh_sentinel():
            time.sleep(1.5)
            with open(sentinel_path, "w") as f:
                f.write("")

        t = threading.Thread(target=_write_fresh_sentinel, daemon=True)
        t.start()

        start = time.monotonic()
        result = poll_for_sentinel(
            sentinel_path=sentinel_path,
            timeout_seconds=15,
            min_sentinel_mtime=attempt_start,
        )
        elapsed = time.monotonic() - start
        t.join(timeout=5)

        assert result is True, "Must return True when fresh sentinel written after stale one discarded"
        assert elapsed >= 1.5, "Must not return on the stale sentinel — must wait for fresh one"
        assert elapsed < 10.0, f"Should complete quickly after fresh sentinel, took {elapsed:.1f}s"
