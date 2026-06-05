"""
poll_for_sentinel ``sentinel_acceptor`` predicate — Layer 2 overflow-aware hold.

Validates the new optional ``sentinel_acceptor`` hook on
:func:`sentinel_poller.poll_for_sentinel`:

  * default ``None`` accepts any ``.done`` (back-compat, byte-identical behaviour);
  * a predicate that returns ``False`` HOLDS the poll (keeps waiting) even though
    ``.done`` exists — used to defer a sentinel written by a context-overflow turn
    that OpenClaw will auto-resume;
  * a holding predicate is still BOUNDED by the existing timeout/stall machinery
    (it can never hang the poll forever);
  * a predicate that raises must never break the poll — it falls back to accepting.

Companion to the orchestrator-side factory tested in
``test_overflow_aware_sentinel_hold.py``.

FIND-ID: FIND-POLLING (Layer 2 — context-overflow discarded-verdict race)
"""

import os
import sys
import time

OPENCLAW_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(OPENCLAW_DIR, "autodev", "pipeline")
for _p in (PIPELINE_DIR, OPENCLAW_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sentinel_poller import poll_for_sentinel  # noqa: E402


def _touch(path):
    with open(path, "w") as f:
        f.write("")


def test_acceptor_none_accepts_immediately(tmp_path):
    """Back-compat (T1): with no acceptor, a fresh .done returns succeeded at once.
    Passes today and must keep passing — guards the default path is unchanged."""
    sentinel = os.path.join(str(tmp_path), "reviewer_output.done")
    _touch(sentinel)
    result = poll_for_sentinel(sentinel_path=sentinel, timeout_seconds=5)
    assert bool(result) is True
    assert getattr(result, "reason", None) == "succeeded"


def test_acceptor_false_then_true_holds_then_succeeds(tmp_path):
    """T2: a predicate that returns False then True HOLDS the poll for a cycle,
    then accepts when the resumed session's verdict is ready.
    Fails today (kwarg does not exist) → catches the hold mechanism."""
    sentinel = os.path.join(str(tmp_path), "reviewer_output.done")
    _touch(sentinel)
    calls = {"n": 0}

    def acceptor():
        calls["n"] += 1
        return calls["n"] >= 2  # hold once (~one ~2s cycle), then accept

    start = time.monotonic()
    result = poll_for_sentinel(
        sentinel_path=sentinel, timeout_seconds=20, sentinel_acceptor=acceptor
    )
    elapsed = time.monotonic() - start
    assert bool(result) is True
    assert getattr(result, "reason", None) == "succeeded"
    assert calls["n"] >= 2, "acceptor must be re-evaluated until it accepts"
    assert elapsed >= 2.0, "must hold at least one ~2s poll cycle before accepting"


def test_acceptor_false_respects_timeout_bound(tmp_path):
    """T3a: an always-False acceptor must not hang — the infra backstop still fires.
    Fails today (kwarg does not exist)."""
    sentinel = os.path.join(str(tmp_path), "reviewer_output.done")
    _touch(sentinel)
    result = poll_for_sentinel(
        sentinel_path=sentinel, timeout_seconds=3, sentinel_acceptor=lambda: False
    )
    assert bool(result) is False
    assert getattr(result, "reason", None) == "timeout"


def test_acceptor_false_respects_stall_bound(tmp_path):
    """T3b: a held sentinel does not defeat startup-grace/stall detection — with no
    stamp advance the poll still returns no_first_activity. Fails today."""
    sentinel = os.path.join(str(tmp_path), "reviewer_output.done")
    stamp = os.path.join(str(tmp_path), "reviewer_activity.stamp")
    _touch(sentinel)
    _touch(stamp)  # exists but never advances → agent never "checks in"
    result = poll_for_sentinel(
        sentinel_path=sentinel,
        timeout_seconds=30,
        stall_detection_path=stamp,
        stall_threshold_seconds=300,
        startup_grace_seconds=2,
        sentinel_acceptor=lambda: False,
    )
    assert bool(result) is False
    assert getattr(result, "reason", None) == "no_first_activity"


def test_acceptor_exception_falls_back_to_accept(tmp_path):
    """T4: a raising acceptor must never break the poll — fall back to accepting.
    Fails today (kwarg does not exist)."""
    sentinel = os.path.join(str(tmp_path), "reviewer_output.done")
    _touch(sentinel)

    def boom():
        raise ValueError("acceptor bug")

    result = poll_for_sentinel(
        sentinel_path=sentinel, timeout_seconds=5, sentinel_acceptor=boom
    )
    assert bool(result) is True
    assert getattr(result, "reason", None) == "succeeded"
