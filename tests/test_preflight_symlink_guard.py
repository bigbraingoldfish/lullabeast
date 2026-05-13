"""Preflight symlink liveness guard.

_run_preflight_checks() must NOT rewrite the pipeline-project symlink while
the orchestrator holds the pipeline lock.  Doing so redirects the running
orchestrator's sentinel poll to a different directory and silently breaks
completion detection.

Bug reference: pipeline-project symlink corrupted by browser test running
_run_preflight_checks() against live server while orchestrator was mid-poll.
"""

import fcntl
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_config(tmp_path, symlink_path, lock_path):
    return {
        "openclaw_root": str(tmp_path / "openclaw"),
        "project_dir_path": str(symlink_path),
        "lock_path": str(lock_path),
        "pipeline_state_path": str(tmp_path / "pipeline_state.json"),
        "pipeline_artifacts_path": "",
    }


class _HeldLock:
    """Context manager: holds an exclusive flock on a file from a background thread."""

    def __init__(self, path):
        self._path = path
        self._fd = None
        self._ready = threading.Event()
        self._release = threading.Event()
        self._thread = threading.Thread(target=self._hold, daemon=True)

    def _hold(self):
        self._fd = open(self._path, "a")
        fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX)
        self._ready.set()
        self._release.wait()
        fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
        self._fd.close()

    def __enter__(self):
        self._thread.start()
        self._ready.wait(timeout=5)
        return self

    def __exit__(self, *_):
        self._release.set()
        self._thread.join(timeout=5)


class TestPreflightSymlinkGuard:

    def test_symlink_not_repointed_when_orchestrator_running(self, tmp_path):
        """Guard: preflight emits warn and leaves symlink intact when lock is held."""
        import json
        from ui.server import _run_preflight_checks

        lock_file = tmp_path / "pipeline.lock"
        lock_file.touch()

        running_project = tmp_path / "solitaire"
        running_project.mkdir()
        (running_project / ".git").mkdir()
        (running_project / "roadmap.md").write_text(
            "- [ ] `T-E1` | LOW | Task\n  > Test.\n"
        )

        symlink = tmp_path / "pipeline-project"
        symlink.symlink_to(running_project)

        # Write pipeline_state.json so the guard knows which project is running
        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(json.dumps({"project_path": str(running_project)}))

        other_project = tmp_path / "other-proj"
        other_project.mkdir()
        (other_project / ".git").mkdir()
        (other_project / "roadmap.md").write_text(
            "- [ ] `T-E1` | LOW | Task\n  > Test.\n"
        )

        config = _make_config(tmp_path, symlink, lock_file)

        with _HeldLock(str(lock_file)):
            checks = _run_preflight_checks(str(other_project), config=config)

        # Symlink must still point to the running project, not other_project
        assert os.path.realpath(str(symlink)) == str(running_project), (
            "Symlink was rewritten while orchestrator held the lock"
        )

        sym_check = next((c for c in checks if c["check"] == "symlink"), None)
        assert sym_check is not None, "No symlink check in results"
        assert sym_check["status"] == "warn", (
            f"Expected symlink check status 'warn', got {sym_check['status']!r}: "
            f"{sym_check['message']}"
        )
        assert "active run" in sym_check["message"].lower(), (
            f"Warning message should mention active run: {sym_check['message']!r}"
        )

    def test_symlink_repointed_when_no_orchestrator_running(self, tmp_path):
        """Normal path: preflight still repairs symlink when no lock is held."""
        from ui.server import _run_preflight_checks

        lock_file = tmp_path / "pipeline.lock"
        lock_file.touch()
        # Do NOT hold the lock — simulates idle state

        old_project = tmp_path / "old-proj"
        old_project.mkdir()
        (old_project / ".git").mkdir()

        symlink = tmp_path / "pipeline-project"
        symlink.symlink_to(old_project)

        new_project = tmp_path / "new-proj"
        new_project.mkdir()
        (new_project / ".git").mkdir()
        (new_project / "roadmap.md").write_text(
            "- [ ] `T-E1` | LOW | Task\n  > Test.\n"
        )

        config = _make_config(tmp_path, symlink, lock_file)
        checks = _run_preflight_checks(str(new_project), config=config)

        assert os.path.realpath(str(symlink)) == str(new_project), (
            "Symlink should be repointed when no orchestrator holds the lock"
        )
        sym_check = next((c for c in checks if c["check"] == "symlink"), None)
        assert sym_check is not None
        assert sym_check["status"] in ("pass", "fixed")

    def test_symlink_repointed_when_preflight_is_for_running_project(self, tmp_path):
        """Guard must not block repair when preflight targets the SAME project."""
        from ui.server import _run_preflight_checks

        lock_file = tmp_path / "pipeline.lock"
        lock_file.touch()

        running_project = tmp_path / "solitaire"
        running_project.mkdir()
        (running_project / ".git").mkdir()
        (running_project / "roadmap.md").write_text(
            "- [ ] `T-E1` | LOW | Task\n  > Test.\n"
        )

        symlink = tmp_path / "pipeline-project"
        # Point symlink somewhere wrong initially
        other = tmp_path / "stale"
        other.mkdir()
        symlink.symlink_to(other)

        config = _make_config(tmp_path, symlink, lock_file)

        with _HeldLock(str(lock_file)):
            checks = _run_preflight_checks(str(running_project), config=config)

        # Symlink should be repaired even with lock held, because it's the SAME project
        assert os.path.realpath(str(symlink)) == str(running_project), (
            "Preflight for the running project itself should repair the symlink"
        )
        sym_check = next((c for c in checks if c["check"] == "symlink"), None)
        assert sym_check is not None
        assert sym_check["status"] in ("pass", "fixed")
