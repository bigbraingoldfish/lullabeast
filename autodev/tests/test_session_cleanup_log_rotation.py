"""Regression tests for ``session_cleanup.rotate_pipeline_logs`` log-root resolution.

The bug under test: ``rotate_pipeline_logs`` resolved ``heartbeat.log`` and
``orchestrator.log`` against ``OPENCLAW_ROOT`` (``~/.openclaw``), but both logs are
written under ``AUTODEV_PIPELINE_ROOT`` (the ``.autodev`` pipeline-state dir — see
``heartbeat_cron.py`` ``LOG_FILE`` and ``ui/server._spawn_orchestrator``). The size
check therefore ran against non-existent paths and silently no-opped, so the real
logs were never trimmed and grew unbounded (an SD-card-exhaustion hazard on the Pi).

These tests point ``OPENCLAW_ROOT`` and ``AUTODEV_PIPELINE_ROOT`` at two *different*
temp dirs so each assertion proves which root rotation actually uses. ``sys.path`` is
wired by ``autodev/tests/conftest.py`` (``autodev/pipeline`` is importable), and its
autouse ``_scrub_autodev_env`` fixture clears both roots before each test, so the
per-test ``monkeypatch.setenv`` values are authoritative.
"""

import os
import sys

import pytest

FIVE_MB = 5 * 1024 * 1024


def _reload_session_cleanup(monkeypatch, openclaw_root, pipeline_root):
    """Import ``session_cleanup`` fresh under the two given roots.

    The module reads both roots into module-level constants at import time, so we set
    the env vars *first*, drop any cached copy, then import — guaranteeing the
    constants re-evaluate against these temp dirs. The import is done here (inside the
    test call), never at this test module's top level, so it always runs after the env
    is set rather than at pytest collection time.
    """
    monkeypatch.setenv("OPENCLAW_ROOT", str(openclaw_root))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(pipeline_root))
    sys.modules.pop("session_cleanup", None)
    import session_cleanup  # noqa: E402 - sys.path wired by conftest

    return session_cleanup


def _write_big_log(path, target_bytes=6 * 1024 * 1024):
    """Write a >5 MB log of fixed-width 100-byte lines. Returns the line count."""
    line = ("x" * 99) + "\n"  # 99 + newline = 100 bytes/line
    n_lines = (target_bytes // len(line)) + 1
    with open(path, "w") as f:
        f.writelines(line for _ in range(n_lines))
    return n_lines


def _write_small_log(path, n_lines):
    with open(path, "w") as f:
        f.writelines(f"line {i}\n" for i in range(n_lines))
    return n_lines


def _line_count(path):
    with open(path) as f:
        return sum(1 for _ in f)


def _make_roots(tmp_path):
    openclaw_root = tmp_path / "openclaw"
    pipeline_root = tmp_path / "pipeline"
    openclaw_root.mkdir()
    pipeline_root.mkdir()
    return openclaw_root, pipeline_root


def test_rotate_targets_pipeline_root_for_orchestrator_log(tmp_path, monkeypatch):
    """An oversized ``orchestrator.log`` under AUTODEV_PIPELINE_ROOT gets trimmed.

    This is the headline behaviour: ``orchestrator.log`` lives in the pipeline-state
    dir, so rotation must find and truncate it there. Catches the original bug — the
    pre-fix code looks under OPENCLAW_ROOT, never sees this file, and leaves it >5 MB.
    """
    openclaw_root, pipeline_root = _make_roots(tmp_path)
    sc = _reload_session_cleanup(monkeypatch, openclaw_root, pipeline_root)

    log_path = pipeline_root / "orchestrator.log"
    _write_big_log(log_path)
    assert log_path.stat().st_size > FIVE_MB

    sc.rotate_pipeline_logs()

    # Rotation keeps the last 1000 lines, so the file shrinks well below the cap.
    assert log_path.stat().st_size < FIVE_MB
    assert _line_count(log_path) == 1000


def test_rotate_targets_pipeline_root_for_heartbeat_log(tmp_path, monkeypatch):
    """An oversized ``heartbeat.log`` under AUTODEV_PIPELINE_ROOT gets trimmed.

    Locks in that *both* pipeline logs — not just ``orchestrator.log`` — resolve
    against the pipeline root, matching the documented rotation targets in
    PIPELINE-CONSTRAINTS.md §1.
    """
    openclaw_root, pipeline_root = _make_roots(tmp_path)
    sc = _reload_session_cleanup(monkeypatch, openclaw_root, pipeline_root)

    log_path = pipeline_root / "heartbeat.log"
    _write_big_log(log_path)
    assert log_path.stat().st_size > FIVE_MB

    sc.rotate_pipeline_logs()

    assert log_path.stat().st_size < FIVE_MB
    assert _line_count(log_path) == 1000


def test_rotate_ignores_openclaw_root_orchestrator_log(tmp_path, monkeypatch):
    """A stray ``orchestrator.log`` under OPENCLAW_ROOT is left untouched.

    The regression guard from the other direction: rotation must target *only* the
    pipeline root. If a future change reverted to OPENCLAW_ROOT — or broadened to
    rotate both roots — this stray file would be trimmed and the assertion would fail.
    Pre-fix, the buggy code rotates exactly this file, so this test is RED today.
    """
    openclaw_root, pipeline_root = _make_roots(tmp_path)
    sc = _reload_session_cleanup(monkeypatch, openclaw_root, pipeline_root)

    stray = openclaw_root / "orchestrator.log"
    n_lines = _write_big_log(stray)
    size_before = stray.stat().st_size

    sc.rotate_pipeline_logs()

    assert stray.stat().st_size == size_before
    assert _line_count(stray) == n_lines


def test_rotate_skips_small_logs(tmp_path, monkeypatch):
    """A sub-5 MB log under the pipeline root is left untouched.

    Pins the preserved 5 MB threshold so the root fix doesn't accidentally turn
    rotation into unconditional truncation. Passes both before and after the fix.
    """
    openclaw_root, pipeline_root = _make_roots(tmp_path)
    sc = _reload_session_cleanup(monkeypatch, openclaw_root, pipeline_root)

    log_path = pipeline_root / "orchestrator.log"
    _write_small_log(log_path, 50)

    sc.rotate_pipeline_logs()

    assert _line_count(log_path) == 50


def test_session_cleanup_log_stays_under_openclaw_root(tmp_path, monkeypatch):
    """The cron's own ``session_cleanup.log`` stays under OPENCLAW_ROOT.

    Two guarantees in one: (1) the module now exposes ``AUTODEV_PIPELINE_ROOT`` —
    accessing it AttributeErrors against the pre-fix module, so this test is RED today;
    (2) ``LOG_FILE`` (the cron's own log) is deliberately NOT moved to the pipeline
    root — it belongs with OpenClaw session state.
    """
    openclaw_root, pipeline_root = _make_roots(tmp_path)
    sc = _reload_session_cleanup(monkeypatch, openclaw_root, pipeline_root)

    assert sc.OPENCLAW_ROOT == str(openclaw_root)
    assert sc.AUTODEV_PIPELINE_ROOT == str(pipeline_root)
    assert sc.LOG_FILE == os.path.join(str(openclaw_root), "session_cleanup.log")
