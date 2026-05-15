"""Section 6.2 — in-poll heartbeat inside ``poll_for_sentinel``.

The user's live evidence: pipeline sits at ``WAITING_FOR_SENTINEL`` for
hours, the orchestrator log is silent during the wait, and an operator
cannot tell from a glance whether the orchestrator is mid-poll-on-a-
slow-agent or genuinely hung.

The fix: ``poll_for_sentinel`` emits a coarse heartbeat line every ~60 s
during a long wait so operators see proof of life plus the current
stamp age in one glance.  The cadence is intentionally coarse — the
poll already sleeps 2 s between iterations; the heartbeat just prints
on the iteration after enough wall time has elapsed.

These tests pin:

HB1 — heartbeat fires within ~1 tick after the interval elapses.
HB2 — heartbeat line carries ``elapsed=Ns`` and ``stamp_age=Ns`` so
       operators can distinguish "alive, agent making progress" from
       "alive, agent stopped".
HB3 — no heartbeat fires *before* the interval (no premature noise).
"""

import os
import sys
import threading
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sentinel_poller as sp  # noqa: E402


def _touch(path):
    if os.path.exists(path):
        os.utime(path, None)
    else:
        with open(path, "w"):
            pass


# ---------------------------------------------------------------------------
# HB1 — heartbeat fires on the iteration after the interval elapses
# ---------------------------------------------------------------------------


def test_heartbeat_fires_after_interval_elapses(tmp_path, monkeypatch, capsys):
    """With ``heartbeat_interval_seconds=0.4`` and a sentinel that
    arrives after 1 s, at least one ``[POLL][HEARTBEAT]`` line must
    appear in stdout.

    The poll loop's internal ``time.sleep(2)`` is too coarse for a fast
    unit test — patch it down so the loop can iterate during the test
    window.
    """
    _real_sleep = time.sleep
    monkeypatch.setattr(sp.time, "sleep", lambda _s: _real_sleep(0.05))

    sentinel = str(tmp_path / "executor_output.done")
    stamp = str(tmp_path / "executor_activity.stamp")
    _touch(stamp)

    # Drop the sentinel file after 1 s so the poll exits naturally.
    threading.Timer(1.0, lambda: open(sentinel, "w").close()).start()

    result = sp.poll_for_sentinel(
        sentinel,
        timeout_seconds=10,
        stall_detection_path=stamp,
        stall_threshold_seconds=100,
        startup_grace_seconds=100,
        heartbeat_interval_seconds=0.4,
        min_sentinel_mtime=time.time() - 1,
    )
    assert bool(result), f"Sentinel never observed: {result}"

    out = capsys.readouterr().out
    assert "[POLL][HEARTBEAT]" in out, (
        f"Expected at least one [POLL][HEARTBEAT] line in stdout; got "
        f"out={out!r}"
    )


# ---------------------------------------------------------------------------
# HB2 — heartbeat line carries elapsed + stamp_age fields
# ---------------------------------------------------------------------------


def test_heartbeat_line_includes_elapsed_and_stamp_age(tmp_path, monkeypatch, capsys):
    """The heartbeat must carry the two diagnostic fields operators
    actually use: ``elapsed`` (wall time the poll has been running) and
    ``stamp_age`` (seconds since the activity stamp was last touched —
    or ``never`` if no first hook has fired yet)."""
    _real_sleep = time.sleep
    monkeypatch.setattr(sp.time, "sleep", lambda _s: _real_sleep(0.05))
    sentinel = str(tmp_path / "executor_output.done")
    stamp = str(tmp_path / "executor_activity.stamp")
    _touch(stamp)

    threading.Timer(1.2, lambda: open(sentinel, "w").close()).start()

    sp.poll_for_sentinel(
        sentinel,
        timeout_seconds=10,
        stall_detection_path=stamp,
        stall_threshold_seconds=100,
        startup_grace_seconds=100,
        heartbeat_interval_seconds=0.4,
        min_sentinel_mtime=time.time() - 1,
    )
    out = capsys.readouterr().out
    hb_lines = [ln for ln in out.splitlines() if "[POLL][HEARTBEAT]" in ln]
    assert hb_lines, f"No heartbeat lines: {out!r}"
    sample = hb_lines[0]
    assert "elapsed=" in sample, (
        f"Heartbeat line must include 'elapsed=': {sample!r}"
    )
    assert "stamp_age=" in sample, (
        f"Heartbeat line must include 'stamp_age=': {sample!r}"
    )


# ---------------------------------------------------------------------------
# HB3 — no heartbeat fires before the interval elapses
# ---------------------------------------------------------------------------


def test_no_heartbeat_before_interval(tmp_path, monkeypatch, capsys):
    """Sentinel arrives within 0.2 s, interval is 5 s — no heartbeat
    should fire."""
    _real_sleep = time.sleep
    monkeypatch.setattr(sp.time, "sleep", lambda _s: _real_sleep(0.05))
    sentinel = str(tmp_path / "executor_output.done")
    stamp = str(tmp_path / "executor_activity.stamp")
    _touch(stamp)
    # Sentinel arrives immediately.
    open(sentinel, "w").close()

    sp.poll_for_sentinel(
        sentinel,
        timeout_seconds=5,
        stall_detection_path=stamp,
        stall_threshold_seconds=100,
        startup_grace_seconds=100,
        heartbeat_interval_seconds=5.0,
        min_sentinel_mtime=time.time() - 1,
    )
    out = capsys.readouterr().out
    assert "[POLL][HEARTBEAT]" not in out, (
        f"Heartbeat fired before interval elapsed: {out!r}"
    )


# ---------------------------------------------------------------------------
# HB4 — back-compat: omitting the interval keeps existing behaviour
# ---------------------------------------------------------------------------


def test_heartbeat_optional_back_compat(tmp_path, monkeypatch, capsys):
    """Callers that do not pass ``heartbeat_interval_seconds`` see the
    pre-Section-6.2 behaviour: no heartbeats.  This protects existing
    tests and call sites that don't yet pass the parameter."""
    _real_sleep = time.sleep
    monkeypatch.setattr(sp.time, "sleep", lambda _s: _real_sleep(0.05))
    sentinel = str(tmp_path / "executor_output.done")
    open(sentinel, "w").close()
    sp.poll_for_sentinel(sentinel, timeout_seconds=3)
    out = capsys.readouterr().out
    assert "[POLL][HEARTBEAT]" not in out
