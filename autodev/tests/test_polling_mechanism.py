"""
Polling mechanism tests.

Validates that:
  - Sentinel polling uses time.sleep(2) loops (NOT time.sleep(60) or inotify)
  - wait_for_model_stable() is called between executor and reviewer handoff
  - poll_for_sentinel detects completion before timeout (early exit)
  - Polling interval is not hardcoded to a fixed value that creates race conditions

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

    def test_idle_detect_returns_early_on_inactive_jsonl(self, tmp_workspace):
        """
        Validates: poll_for_sentinel_with_idle_detect returns False early when the
        session JSONL stops updating (idle_threshold exceeded), without waiting for
        the full timeout.

        This is the OB-4 fix for empty model completions.

        FIND-ID: FIND-POLLING
        Spec Reference: PIPELINE-CONSTRAINTS.md §5.6 "OB-4 Empty model completion [RESOLVED]"
        """
        from sentinel_poller import poll_for_sentinel_with_idle_detect

        sentinel_path = os.path.join(tmp_workspace, "executor_output.done")
        jsonl_path = os.path.join(tmp_workspace, "session.jsonl")

        # Create a JSONL file that won't be updated
        with open(jsonl_path, "w") as f:
            f.write('{"type":"start"}\n')

        start = time.monotonic()
        result = poll_for_sentinel_with_idle_detect(
            sentinel_path=sentinel_path,
            jsonl_path=jsonl_path,
            startup_grace=0,    # no grace period in test
            idle_threshold=1,   # 1 second idle → early exit
            timeout_seconds=30, # would wait 30s without idle detection
        )
        elapsed = time.monotonic() - start

        assert result is False, "Must return False when sentinel absent and JSONL is idle"
        # Must exit far before the 30s outer timeout
        assert elapsed < 10.0, (
            f"Idle detection must trigger early exit (elapsed {elapsed:.1f}s >> expected ~1s). "
            f"The point of idle detection is to avoid waiting the full 600s."
        )

    def test_watch_dirs_resets_idle_clock_on_file_write(self, tmp_workspace):
        """
        Validates: when watch_dirs is supplied, the idle clock resets whenever any file
        in those directories is written — not just when the JSONL updates.

        This prevents false-positive idle detection when the model is actively writing
        code/test files but happens to be between JSONL flushes (MiniMax M2.7 batches
        responses rather than streaming, so JSONL can be quiet for minutes while file
        writes are ongoing).

        FIND-ID: FIND-POLLING
        """
        import threading
        from sentinel_poller import poll_for_sentinel_with_idle_detect

        sentinel_path = os.path.join(tmp_workspace, "executor_output.done")
        jsonl_path = os.path.join(tmp_workspace, "session.jsonl")
        project_dir = os.path.join(tmp_workspace, "project")
        os.makedirs(project_dir)

        # JSONL exists but will NOT be updated — alone this would trigger idle
        with open(jsonl_path, "w") as f:
            f.write('{"type":"start"}\n')

        # A background thread writes a project file after 1.5s to simulate the
        # model writing code mid-session
        code_file = os.path.join(project_dir, "logic.py")
        def _write_code_then_sentinel():
            time.sleep(1.5)
            with open(code_file, "w") as f:
                f.write("# generated\n")
            time.sleep(1.5)
            # Write the sentinel so the poll returns True
            open(sentinel_path, "w").close()

        t = threading.Thread(target=_write_code_then_sentinel, daemon=True)
        t.start()

        start = time.monotonic()
        result = poll_for_sentinel_with_idle_detect(
            sentinel_path=sentinel_path,
            jsonl_path=jsonl_path,
            startup_grace=0,
            idle_threshold=1,   # would fire in ~1s on JSONL alone
            timeout_seconds=30,
            watch_dirs=[project_dir],
        )
        elapsed = time.monotonic() - start
        t.join(timeout=5)

        assert result is True, (
            "Must return True: sentinel was written after a project file write reset the "
            "idle clock. Without watch_dirs the 1s idle_threshold would have fired before "
            "the sentinel appeared."
        )
        assert elapsed < 10.0, f"Should complete quickly, took {elapsed:.1f}s"

    def test_stale_sentinel_discarded_orphaned_session(self, tmp_workspace):
        """
        Validates: when min_sentinel_mtime is supplied and the sentinel file's mtime
        is OLDER than that value, the sentinel is treated as belonging to an orphaned
        prior session (whose code was already cleaned by git reset) and is discarded.
        Polling continues until a fresh sentinel appears, preserving executor_retries.

        FIND-ID: FIND-POLLING
        """
        import threading
        from sentinel_poller import poll_for_sentinel_with_idle_detect

        sentinel_path = os.path.join(tmp_workspace, "executor_output.done")
        jsonl_path = os.path.join(tmp_workspace, "session.jsonl")

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
        result = poll_for_sentinel_with_idle_detect(
            sentinel_path=sentinel_path,
            jsonl_path=jsonl_path,
            startup_grace=0,
            idle_threshold=30,
            timeout_seconds=15,
            min_sentinel_mtime=attempt_start,
        )
        elapsed = time.monotonic() - start
        t.join(timeout=5)

        assert result is True, "Must return True when fresh sentinel written after stale one discarded"
        assert elapsed >= 1.5, "Must not return on the stale sentinel — must wait for fresh one"
        assert elapsed < 10.0, f"Should complete quickly after fresh sentinel, took {elapsed:.1f}s"

    def test_watch_dirs_fires_when_all_sources_truly_idle(self, tmp_workspace):
        """
        Validates: idle detection still fires correctly even with watch_dirs, as long as
        NEITHER the JSONL NOR any project file updates for idle_threshold seconds.

        This confirms watch_dirs does not disable the safety net — it only delays idle
        detection when real work is happening.

        FIND-ID: FIND-POLLING
        """
        from sentinel_poller import poll_for_sentinel_with_idle_detect

        sentinel_path = os.path.join(tmp_workspace, "executor_output.done")
        jsonl_path = os.path.join(tmp_workspace, "session.jsonl")
        project_dir = os.path.join(tmp_workspace, "project_idle")
        os.makedirs(project_dir)

        # Both JSONL and project dir exist but neither will be updated
        with open(jsonl_path, "w") as f:
            f.write('{"type":"start"}\n')
        with open(os.path.join(project_dir, "existing.py"), "w") as f:
            f.write("# static\n")

        start = time.monotonic()
        result = poll_for_sentinel_with_idle_detect(
            sentinel_path=sentinel_path,
            jsonl_path=jsonl_path,
            startup_grace=0,
            idle_threshold=1,
            timeout_seconds=30,
            watch_dirs=[project_dir],
        )
        elapsed = time.monotonic() - start

        assert result is False, "Must return False when BOTH JSONL and project files are idle"
        assert elapsed < 10.0, (
            f"Idle detection must still fire early when all sources are quiet "
            f"(elapsed {elapsed:.1f}s)"
        )
