"""Section 1 — sentinel_poller startup grace + structured PollResult.

The original ``poll_for_sentinel`` used one knob (``stall_threshold_seconds``)
to govern two unrelated waits:

1. **Session startup** — OpenClaw provisioning + first hook (3–10 min legit)
2. **Mid-turn silence** — model alive then stopped (should fail in 3–5 min)

The bootstrap guard kept stall detection dormant until the stamp advanced
once, which suppressed startup false-alarms but forced the single threshold
to tolerate slow boots — meaning mid-turn stalls took 6× longer to catch
than operators expected.

These tests pin the new behaviour:

* A separate ``startup_grace_seconds`` knob bounds the pre-first-hook wait
  independently of the stall threshold.
* ``poll_for_sentinel`` returns a structured ``PollResult`` (truthy on
  success for back-compat with existing ``if result:`` callers) carrying
  a ``reason`` field so the orchestrator can distinguish stall from
  startup timeout from infrastructure timeout in logs.
* Existing call sites that pass no ``startup_grace_seconds`` see the
  pre-existing bootstrap-guard behaviour unchanged.
"""

import os
import sys
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sentinel_poller as sp  # noqa: E402


def _touch(path):
    """Update mtime on ``path`` (creates file if missing) — used to simulate
    the plugin's stamp refresh."""
    if os.path.exists(path):
        os.utime(path, None)
    else:
        with open(path, "w"):
            pass


def _delayed(fn, delay_s):
    """Schedule ``fn()`` on a daemon thread after ``delay_s``."""
    t = threading.Timer(delay_s, fn)
    t.daemon = True
    t.start()
    return t


# ---------------------------------------------------------------------------
# Test 1 — startup grace fires when first hook never arrives
# ---------------------------------------------------------------------------


def test_returns_no_first_activity_when_grace_exceeds_without_advance(tmp_path):
    """A stamp that is created but never advances (plugin never fires its
    first hook) must produce ``no_first_activity`` once ``startup_grace_seconds``
    elapses — not wait the full infrastructure backstop.

    This is the regression test for the slow-OpenClaw-boot case where the
    agent session is provisioned but the first model_call_started event
    never reaches the stall-detector plugin.
    """
    sentinel = str(tmp_path / "executor_output.done")
    stamp = str(tmp_path / "executor_activity.stamp")
    # Stamp exists but will never be touched again.
    with open(stamp, "w"):
        pass

    start = time.monotonic()
    result = sp.poll_for_sentinel(
        sentinel,
        timeout_seconds=30,  # well above grace
        stall_detection_path=stamp,
        stall_threshold_seconds=300,
        startup_grace_seconds=2,
    )
    elapsed = time.monotonic() - start

    assert not bool(result), "Expected falsy result (no first hook)"
    assert getattr(result, "reason", None) == "no_first_activity", (
        f"Expected reason='no_first_activity', got reason="
        f"{getattr(result, 'reason', None)!r}"
    )
    # Should fire close to grace + poll-tick (2s sleep), not the full
    # infrastructure backstop (30s).
    assert elapsed < 8, (
        f"Expected stall within grace window (~2-4s), took {elapsed:.1f}s"
    )


# ---------------------------------------------------------------------------
# Test 2 — first hook within grace then completion → succeeded
# ---------------------------------------------------------------------------


def test_succeeds_when_first_hook_arrives_within_grace_then_done(tmp_path):
    """First activity within grace, then ``.done`` appears — must return
    ``PollResult(success=True, reason='succeeded')``."""
    sentinel = str(tmp_path / "executor_output.done")
    stamp = str(tmp_path / "executor_activity.stamp")
    with open(stamp, "w"):
        pass

    # Simulate plugin first hook at t+1s
    _delayed(lambda: _touch(stamp), 1.0)
    # Simulate completion at t+3s
    _delayed(lambda: open(sentinel, "w").close(), 3.0)

    result = sp.poll_for_sentinel(
        sentinel,
        timeout_seconds=20,
        stall_detection_path=stamp,
        stall_threshold_seconds=20,
        startup_grace_seconds=10,
        min_sentinel_mtime=time.time() - 1,  # accept the freshly-written .done
    )

    assert bool(result), f"Expected success, got {result}"
    assert getattr(result, "reason", None) == "succeeded"


# ---------------------------------------------------------------------------
# Test 3 — mid-turn stall after activity → stalled
# ---------------------------------------------------------------------------


def test_stalls_after_activity_then_silence(tmp_path):
    """Stamp advances once (proves agent alive), then goes silent past
    ``stall_threshold_seconds`` — must return ``reason='stalled'``.

    This is the existing bootstrap-guard behaviour preserved under the
    new structured return.
    """
    sentinel = str(tmp_path / "executor_output.done")
    stamp = str(tmp_path / "executor_activity.stamp")
    with open(stamp, "w"):
        pass
    # Backdate the initial bootstrap so the threshold compares against an
    # advance that has already occurred when polling starts.
    past = time.time() - 10
    os.utime(stamp, (past, past))

    # Advance the stamp once at t+0.5s (plugin first hook).
    _delayed(lambda: _touch(stamp), 0.5)

    start = time.monotonic()
    result = sp.poll_for_sentinel(
        sentinel,
        timeout_seconds=30,
        stall_detection_path=stamp,
        stall_threshold_seconds=2,
        startup_grace_seconds=20,  # long enough not to trip
    )
    elapsed = time.monotonic() - start

    assert not bool(result)
    assert getattr(result, "reason", None) == "stalled", (
        f"Expected reason='stalled', got {getattr(result, 'reason', None)!r}"
    )
    # Stall fires ~stall_threshold after the last advance, plus poll-tick.
    assert elapsed < 10, f"Stall took too long: {elapsed:.1f}s"


# ---------------------------------------------------------------------------
# Test 4 — grace fires even when stamp file is missing
# ---------------------------------------------------------------------------


def test_grace_independent_of_stamp_presence(tmp_path):
    """If ``initialize_activity_stamp`` silently failed (workspace dir
    unwritable), the stamp file does not exist.  Previously the stall
    branch was skipped entirely and polling waited the full infra
    backstop.  With explicit grace, a missing stamp must still produce a
    timely ``no_first_activity`` — the orchestrator's strict-init guard
    catches the root cause, and this catches the symptom regardless.
    """
    sentinel = str(tmp_path / "executor_output.done")
    missing_stamp = str(tmp_path / "nonexistent_activity.stamp")

    start = time.monotonic()
    result = sp.poll_for_sentinel(
        sentinel,
        timeout_seconds=30,
        stall_detection_path=missing_stamp,
        stall_threshold_seconds=300,
        startup_grace_seconds=2,
    )
    elapsed = time.monotonic() - start

    assert not bool(result)
    assert getattr(result, "reason", None) == "no_first_activity"
    assert elapsed < 8, (
        f"Grace must fire even when stamp is missing; took {elapsed:.1f}s"
    )


# ---------------------------------------------------------------------------
# Test 5 — stop sentinel returns reason='stopped'
# ---------------------------------------------------------------------------


def test_stop_sentinel_returns_stopped(tmp_path):
    """Pre-existing stop sentinel handling, with new ``reason`` field."""
    sentinel = str(tmp_path / "executor_output.done")
    stop = str(tmp_path / "pipeline_stop_requested")
    open(stop, "w").close()

    result = sp.poll_for_sentinel(
        sentinel,
        timeout_seconds=5,
        stop_sentinel_path=stop,
    )
    assert not bool(result)
    assert getattr(result, "reason", None) == "stopped"


# ---------------------------------------------------------------------------
# Test 6 — infra backstop returns reason='timeout'
# ---------------------------------------------------------------------------


def test_infra_timeout_returns_timeout(tmp_path):
    """No stall/grace configured, no .done — natural loop exit returns
    ``reason='timeout'``."""
    sentinel = str(tmp_path / "executor_output.done")
    start = time.monotonic()
    result = sp.poll_for_sentinel(sentinel, timeout_seconds=2)
    elapsed = time.monotonic() - start
    assert not bool(result)
    assert getattr(result, "reason", None) == "timeout"
    # Should be close to timeout_seconds, allowing for the 2-s poll-tick.
    assert 1.5 < elapsed < 6, f"Unexpected timeout duration: {elapsed:.1f}s"


# ---------------------------------------------------------------------------
# Test 7 — back-compat: no grace passed → bootstrap-guard behaviour preserved
# ---------------------------------------------------------------------------


def test_backcompat_no_startup_grace_keeps_bootstrap_guard_behaviour(tmp_path):
    """When callers omit ``startup_grace_seconds`` (existing callers prior
    to the wire-up in Section 4), polling must behave exactly as before:
    stall detection stays dormant until first activity, no early exit.

    Specifically: stamp present but never advances, no grace passed →
    poll waits the full ``timeout_seconds`` (returns ``reason='timeout'``).
    """
    sentinel = str(tmp_path / "executor_output.done")
    stamp = str(tmp_path / "executor_activity.stamp")
    with open(stamp, "w"):
        pass

    start = time.monotonic()
    result = sp.poll_for_sentinel(
        sentinel,
        timeout_seconds=3,
        stall_detection_path=stamp,
        stall_threshold_seconds=1,
        # startup_grace_seconds intentionally omitted
    )
    elapsed = time.monotonic() - start

    assert not bool(result)
    # Bootstrap guard kept stall dormant; we hit the infra timeout.
    assert getattr(result, "reason", None) == "timeout"
    assert 2.5 < elapsed < 7, (
        f"Without grace, must wait full timeout_seconds, took {elapsed:.1f}s"
    )


# ---------------------------------------------------------------------------
# Test 8 — PollResult exposed at module level, truthy ergonomics
# ---------------------------------------------------------------------------


def test_poll_result_is_truthy_on_success_and_falsy_on_failure(tmp_path):
    """``PollResult`` must be a public type with sensible ``__bool__`` so
    existing ``if sentinel_found:`` callers continue to work unchanged."""
    assert hasattr(sp, "PollResult"), "sentinel_poller must expose PollResult"
    ok = sp.PollResult(success=True, reason="succeeded")
    nope = sp.PollResult(success=False, reason="stalled")
    assert bool(ok) is True
    assert bool(nope) is False
    # Frozen / hashable: protects callers from accidental mutation.
    try:
        ok.success = False  # type: ignore[misc]
    except (AttributeError, Exception):
        pass
    else:
        # If assignment succeeded, the field isn't frozen — that's a defect.
        # We tolerate either fully frozen or a mutable dataclass, but flag.
        assert False, "PollResult should be frozen to prevent accidental mutation"
