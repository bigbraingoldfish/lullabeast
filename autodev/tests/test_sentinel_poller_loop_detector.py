"""poll_for_sentinel ``loop_detector`` predicate — deterministic in-turn tool-loop catch.

Validates the new optional ``loop_detector`` hook on
:func:`sentinel_poller.poll_for_sentinel`. Unlike ``sentinel_acceptor`` (consulted
only when ``.done`` exists), ``loop_detector`` is consulted **every poll cycle** —
because a tool-loop never writes ``.done``; the agent spins while the poll waits.

Contract under test:
  * a truthy verdict short-circuits the poll to ``PollResult(False, "tool_loop")``;
  * a falsy verdict leaves the normal ``.done`` → ``succeeded`` path untouched;
  * a raising detector must NEVER break the poll — it fails safe (no loop), so a
    buggy detector can never false-abort a healthy agent;
  * ``loop_detector=None`` (default) is byte-identical to today's behaviour;
  * ``"tool_loop"`` is a valid ``PollResult.reason``.

Companion to the orchestrator-side detector/closure tested in
``test_tool_loop_detection.py``.
"""
import os
import sys
import time

OPENCLAW_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(OPENCLAW_DIR, "autodev", "pipeline")
for _p in (PIPELINE_DIR, OPENCLAW_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sentinel_poller import POLL_REASONS, PollResult, poll_for_sentinel  # noqa: E402


def _touch(path):
    with open(path, "w") as f:
        f.write("")


def test_tool_loop_is_a_valid_pollresult_reason():
    """``"tool_loop"`` must be in POLL_REASONS so PollResult construction never
    raises in __post_init__. Fails today — the reason is not yet registered."""
    assert "tool_loop" in POLL_REASONS
    # Must not raise:
    assert PollResult(False, "tool_loop").reason == "tool_loop"


def test_loop_detector_none_default_accepts_done(tmp_path):
    """Back-compat: with no loop_detector, a fresh .done returns succeeded at once."""
    sentinel = os.path.join(str(tmp_path), "reviewer_output.done")
    _touch(sentinel)
    result = poll_for_sentinel(sentinel_path=sentinel, timeout_seconds=5)
    assert bool(result) is True
    assert getattr(result, "reason", None) == "succeeded"


def test_loop_detector_true_returns_tool_loop(tmp_path):
    """A detector that reports a loop short-circuits the poll to reason "tool_loop"
    even with NO .done on disk (the loop never writes one). Fails today (kwarg
    does not exist) → catches the new per-cycle hook + reason."""
    sentinel = os.path.join(str(tmp_path), "reviewer_output.done")  # never created
    start = time.monotonic()
    result = poll_for_sentinel(
        sentinel_path=sentinel, timeout_seconds=30, loop_detector=lambda: True
    )
    elapsed = time.monotonic() - start
    assert bool(result) is False
    assert getattr(result, "reason", None) == "tool_loop"
    assert elapsed < 8, "must fire on an early cycle, not wait the full backstop"


def test_loop_detector_false_does_not_block_success(tmp_path):
    """An always-False detector must not interfere: a fresh .done still succeeds."""
    sentinel = os.path.join(str(tmp_path), "reviewer_output.done")
    _touch(sentinel)
    result = poll_for_sentinel(
        sentinel_path=sentinel, timeout_seconds=5, loop_detector=lambda: False
    )
    assert bool(result) is True
    assert getattr(result, "reason", None) == "succeeded"


def test_loop_detector_exception_is_failsafe(tmp_path):
    """A raising detector must never break the poll nor false-trip a loop: it is
    treated as "no loop", so a present .done still returns succeeded."""
    sentinel = os.path.join(str(tmp_path), "reviewer_output.done")
    _touch(sentinel)

    def boom():
        raise ValueError("detector bug")

    result = poll_for_sentinel(
        sentinel_path=sentinel, timeout_seconds=5, loop_detector=boom
    )
    assert bool(result) is True
    assert getattr(result, "reason", None) == "succeeded"
