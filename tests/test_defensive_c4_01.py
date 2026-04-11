"""C4-01: _check_orchestrator_liveness must not truncate the lock file."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui.server import _check_orchestrator_liveness


def test_liveness_probe_does_not_truncate_lock_file(tmp_path):
    """Opening the lock file with 'w' truncates any PID/timestamp written by the
    orchestrator.  The probe should open read-only (or append) so existing content
    is preserved."""
    lock_path = tmp_path / "pipeline.lock"
    # Simulate orchestrator having written diagnostics into the lock file.
    lock_path.write_text("pid=12345 started=2026-04-10T00:00:00Z")

    # Probe returns False when no other process holds the lock.
    result = _check_orchestrator_liveness(str(lock_path))
    assert result is False

    # Content must survive the probe call unchanged.
    content = lock_path.read_text()
    assert content == "pid=12345 started=2026-04-10T00:00:00Z", (
        f"Lock file was truncated by liveness probe; got: {content!r}"
    )


def test_liveness_probe_does_not_create_lock_file_on_missing(tmp_path):
    """Probe against a non-existent lock file should not raise FileNotFoundError
    (the file simply doesn't exist when the orchestrator hasn't started yet)."""
    lock_path = tmp_path / "nonexistent.lock"
    assert not lock_path.exists()

    # Should return False (lock is free / file absent) without raising.
    result = _check_orchestrator_liveness(str(lock_path))
    assert result is False
