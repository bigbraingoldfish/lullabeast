"""Unit tests for stamp-based ``_poll_sentinel_with_idle_detect`` (Ideas workflow).

The poller watches ``prd_creator_activity.stamp`` mtime (the Tier A activity
signal — touched by the OpenClaw plugin on every ``model_call_started`` /
``model_call_ended`` / ``after_tool_call``) and the ``.done`` turn sentinel.
Returns a :class:`PollResult` with one of the four reasons defined in
``autodev/pipeline/sentinel_poller.py``: ``succeeded`` / ``stalled`` /
``no_first_activity`` / ``timeout``.  Mirrors the pipeline's
:func:`poll_for_sentinel` so the Ideas chat and the pipeline orchestrator
share the same liveness vocabulary and two-knob (startup_grace vs
stall_threshold) semantics.
"""

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from autodev.pipeline.sentinel_poller import PollResult


def _get_poll():
    from ui.server import _poll_sentinel_with_idle_detect
    return _poll_sentinel_with_idle_detect


def _touch_stamp(stamp_path: Path, mtime: float | None = None) -> None:
    """Create or refresh the activity stamp at ``stamp_path``.

    If ``mtime`` is given, set both atime and mtime to that wall-clock value;
    otherwise leave the just-written file at the current wall-clock time.  Used
    to simulate the plugin's atomic stamp writes from inside tests without
    touching OpenClaw.
    """
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    stamp_path.write_text("")
    if mtime is not None:
        os.utime(stamp_path, (mtime, mtime))


class TestPollSentinelStampDetect:
    """Async ``_poll_sentinel_with_idle_detect`` — stamp Tier A + ``.done``."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_returns_succeeded_when_sentinel_found_immediately(self, tmp_path):
        """``.done`` already on disk → short-circuit returns ``PollResult(True, "succeeded")``.

        Pre-existing sentinel is the cheapest happy path; if this regresses,
        every Ideas turn pays an unnecessary poll cycle on the first iteration.
        """
        poll = _get_poll()
        done = tmp_path / "1.done"
        done.write_text("done")
        stamp = tmp_path / "prd_creator_activity.stamp"

        result = self._run(
            poll(
                done_path=done,
                stamp_path=stamp,
                attempt_start_wall=time.time(),
                poll_timeout=5.0,
                poll_interval=0.05,
                stall_threshold=120.0,
                startup_grace=2.0,
            )
        )
        assert isinstance(result, PollResult)
        assert result.success is True
        assert result.reason == "succeeded"
        assert bool(result) is True

    def test_returns_no_first_activity_when_stamp_never_appears(self, tmp_path):
        """No stamp + no ``.done`` → startup grace expires → ``no_first_activity``.

        Covers cold OpenClaw boot failures: gateway hasn't accepted the webhook,
        plugin never fires.  Pre-fix this would hang for the full 180 s
        ``poll_timeout`` because JSONL path resolution had its own retry loop;
        post-fix this returns within ``startup_grace`` (default 30 s).
        """
        poll = _get_poll()
        done = tmp_path / "1.done"
        stamp = tmp_path / "prd_creator_activity.stamp"

        # Fake monotonic that immediately crosses startup_grace.  Same pattern
        # the pre-fix idle test used (lines 99-113 of the prior file).
        mono_seq = iter([0.0, 0.05, 0.5, 1.0])

        def fake_monotonic():
            try:
                return next(mono_seq)
            except StopIteration:
                return 10.0

        async def fast_sleep(_interval):
            return None

        with patch("ui.server.time.monotonic", fake_monotonic):
            with patch("ui.server.asyncio.sleep", fast_sleep):
                result = asyncio.run(
                    poll(
                        done_path=done,
                        stamp_path=stamp,
                        attempt_start_wall=time.time(),
                        poll_timeout=900.0,
                        poll_interval=0.01,
                        stall_threshold=120.0,
                        startup_grace=0.2,
                    )
                )
        assert isinstance(result, PollResult)
        assert result.success is False
        assert result.reason == "no_first_activity"

    def test_returns_stalled_when_stamp_goes_silent_after_first_advance(self, tmp_path):
        """Stamp seen once with fresh mtime then never advances → ``stalled``.

        This is the CORE-E6 pattern as it would manifest in Ideas: the agent
        was active for a few seconds, then went quiet.  The poller must
        notice within ``stall_threshold`` rather than waiting the full
        ``poll_timeout``.

        Trick: the stamp's mtime is set to ``attempt_start_wall + 0.1`` (so
        it counts as fresh for *this* attempt), but ``stall_threshold`` is
        small (1.0 s) and real ``time.time()`` is current — so the gap
        ``time.time() - stamp_mtime`` immediately exceeds ``stall_threshold``
        on the next poll iteration.
        """
        poll = _get_poll()
        done = tmp_path / "1.done"
        stamp = tmp_path / "prd_creator_activity.stamp"

        attempt_start = time.time() - 10.0  # well in the past
        _touch_stamp(stamp, mtime=attempt_start + 0.1)

        result = self._run(
            poll(
                done_path=done,
                stamp_path=stamp,
                attempt_start_wall=attempt_start,
                poll_timeout=900.0,
                poll_interval=0.01,
                stall_threshold=1.0,
                startup_grace=600.0,  # large so it cannot fire here
            )
        )
        assert isinstance(result, PollResult)
        assert result.success is False
        assert result.reason == "stalled"
        # stamp_mtime is reported back so the orchestrator (and UI logs) can
        # show how old the last observed activity was.
        assert result.stamp_mtime is not None

    def test_overlapping_readiness_stamp_does_not_mask_chat_turn_stall(self, tmp_path):
        """A fresh readiness stamp must not rescue a silent chat-turn stamp.

        Consumer-side guard for the readiness/chat stamp-isolation fix (producer
        side lives in ``autodev/plugin``: the background readiness session now
        warms ``prd_creator_readiness_activity.stamp`` while the chat poll keeps
        watching ``prd_creator_activity.stamp``).  Readiness is auto-fired
        fire-and-forget after every chat turn (``_trigger_readiness_assessment``)
        and can still be running when the next turn starts; before the split it
        warmed the SAME stamp this poller watches, so an overlapping readiness
        run kept the stamp fresh and masked a genuinely-stalled foreground turn.

        Here the chat stamp went silent (~10 s old) while the *readiness* stamp
        is brand new — the poll must still report ``stalled`` because it only
        watches the chat stamp.  If a future change re-points the chat poll at a
        shared/merged stamp, this turns into ``succeeded`` (or hangs) and the
        guard fires.
        """
        poll = _get_poll()
        done = tmp_path / "1.done"
        chat_stamp = tmp_path / "prd_creator_activity.stamp"
        readiness_stamp = tmp_path / "prd_creator_readiness_activity.stamp"

        attempt_start = time.time() - 10.0  # well in the past
        # Chat turn produced one early activity, then went silent.
        _touch_stamp(chat_stamp, mtime=attempt_start + 0.1)
        # Overlapping readiness keeps ITS own stamp piping-hot right now.
        _touch_stamp(readiness_stamp, mtime=time.time())

        result = self._run(
            poll(
                done_path=done,
                stamp_path=chat_stamp,
                attempt_start_wall=attempt_start,
                poll_timeout=900.0,
                poll_interval=0.01,
                stall_threshold=1.0,
                startup_grace=600.0,  # large so it cannot fire here
            )
        )
        assert isinstance(result, PollResult)
        assert result.reason == "stalled", (
            "a hot readiness stamp must not keep the chat-turn poll alive — the "
            "two liveness signals are deliberately on separate stamp files"
        )

    def test_returns_succeeded_when_stamp_advances_then_done_written(self, tmp_path):
        """Stamp ticks then ``.done`` appears → ``succeeded``.

        The realistic happy path: agent starts running, refreshes the stamp a
        few times via Tier A hooks, then writes the turn sentinel.  Uses real
        ``asyncio.sleep`` with small intervals so the writer task and the
        poller cooperate naturally.
        """
        poll = _get_poll()
        done = tmp_path / "1.done"
        stamp = tmp_path / "prd_creator_activity.stamp"

        async def run_with_writer():
            attempt_start = time.time()

            async def bump_then_done():
                await asyncio.sleep(0.05)
                _touch_stamp(stamp)
                await asyncio.sleep(0.05)
                _touch_stamp(stamp)
                await asyncio.sleep(0.05)
                done.write_text("done")

            t = asyncio.create_task(bump_then_done())
            r = await poll(
                done_path=done,
                stamp_path=stamp,
                attempt_start_wall=attempt_start,
                poll_timeout=10.0,
                poll_interval=0.02,
                stall_threshold=5.0,
                startup_grace=2.0,
            )
            await t
            return r

        result = asyncio.run(run_with_writer())
        assert isinstance(result, PollResult)
        assert bool(result) is True
        assert result.reason == "succeeded"

    def test_first_stamp_appearance_resets_startup_grace(self, tmp_path):
        """First fresh stamp → ``stall_threshold`` governs, not ``startup_grace``.

        Boundary check for the two-knob split.  Stamp appears *inside* the
        startup grace window (so ``no_first_activity`` should NOT fire), and
        immediately after that the silence exceeds ``stall_threshold`` — so
        the reason must be ``"stalled"``.  If a future refactor inverts the
        knobs and reports ``"no_first_activity"`` here, mid-turn deaths would
        be misreported as cold-boot failures.
        """
        poll = _get_poll()
        done = tmp_path / "1.done"
        stamp = tmp_path / "prd_creator_activity.stamp"

        attempt_start = time.time() - 5.0  # well in the past

        async def run_with_writer():
            async def write_stamp_then_silence():
                # Wait so the stamp appears late but within startup_grace.
                await asyncio.sleep(0.05)
                _touch_stamp(stamp, mtime=attempt_start + 0.1)

            t = asyncio.create_task(write_stamp_then_silence())
            r = await poll(
                done_path=done,
                stamp_path=stamp,
                attempt_start_wall=attempt_start,
                poll_timeout=10.0,
                poll_interval=0.02,
                stall_threshold=1.0,
                startup_grace=60.0,  # generous, would NOT have expired
            )
            await t
            return r

        result = asyncio.run(run_with_writer())
        assert isinstance(result, PollResult)
        assert result.success is False
        assert result.reason == "stalled", (
            "Once first stamp activity is observed, post-first stall_threshold "
            "must govern — not pre-first startup_grace."
        )

    def test_returns_timeout_when_poll_deadline_hit(self, tmp_path):
        """Stamp keeps ticking, no ``.done``, ``poll_timeout`` elapses → ``timeout``.

        Proves the infrastructure backstop is still wired even when the agent
        is *genuinely active* but the gateway never produces a sentinel.  Use
        fake monotonic so the elapsed clock crosses the deadline on the first
        loop iteration.
        """
        poll = _get_poll()
        done = tmp_path / "1.done"
        stamp = tmp_path / "prd_creator_activity.stamp"
        _touch_stamp(stamp)  # mtime = now, fresh

        mono_seq = iter([0.0, 1.0])

        def fake_monotonic():
            try:
                return next(mono_seq)
            except StopIteration:
                return 100.0

        async def fast_sleep(_interval):
            return None

        with patch("ui.server.time.monotonic", fake_monotonic):
            with patch("ui.server.asyncio.sleep", fast_sleep):
                result = asyncio.run(
                    poll(
                        done_path=done,
                        stamp_path=stamp,
                        attempt_start_wall=time.time() - 1.0,
                        poll_timeout=0.5,
                        poll_interval=0.05,
                        stall_threshold=600.0,
                        startup_grace=600.0,
                    )
                )
        assert isinstance(result, PollResult)
        assert result.success is False
        assert result.reason == "timeout"

    def test_startup_grace_none_runs_to_backstop_not_no_first_activity(self, tmp_path):
        """``startup_grace=None`` disables the pre-first-activity early-fail.

        The Ideas chat *send* opts out of the 30 s ``no_first_activity`` check
        (it passes ``startup_grace=None``) so a slow cold start is never
        declared a premature timeout. With no stamp and no ``.done``, the poll
        must keep waiting and ultimately report the **definitive** ``timeout``
        backstop signal — NOT ``no_first_activity``.

        Drives a fake monotonic clock so the loop reaches the pre-first-activity
        branch (where ``no_first_activity`` would fire under a numeric grace)
        BEFORE the ``poll_timeout`` backstop — proving the ``None`` guard, not
        merely that the backstop wins a race.
        """
        poll = _get_poll()
        done = tmp_path / "1.done"
        stamp = tmp_path / "prd_creator_activity.stamp"  # never created

        # start=0.0; iters at elapsed 0.1, 0.2 (both < poll_timeout 0.4, so the
        # pre-first-activity branch is exercised), then 0.5 >= 0.4 -> timeout.
        mono_seq = iter([0.0, 0.1, 0.2, 0.5])

        def fake_monotonic():
            try:
                return next(mono_seq)
            except StopIteration:
                return 10.0

        async def fast_sleep(_interval):
            return None

        with patch("ui.server.time.monotonic", fake_monotonic):
            with patch("ui.server.asyncio.sleep", fast_sleep):
                result = asyncio.run(
                    poll(
                        done_path=done,
                        stamp_path=stamp,
                        attempt_start_wall=time.time(),
                        poll_timeout=0.4,
                        poll_interval=0.01,
                        stall_threshold=600.0,
                        startup_grace=None,
                    )
                )
        assert isinstance(result, PollResult)
        assert result.success is False
        assert result.reason == "timeout", (
            "startup_grace=None must fall through to the poll_timeout backstop "
            "(the definitive signal), not fire the premature no_first_activity check"
        )


def test_ideas_poll_defaults_accommodate_thorough_prd_drafts():
    """Production defaults must exceed the longest legitimate single model call.

    Live measurement (Issue X investigation, 2026-05-28): a *successful*
    PRD-draft turn contained a single model call with **118.3 seconds of total
    stamp silence** — OpenClaw delivers model calls opaquely (started → silence
    → ended) with no reliable intermediate event for this provider, so the
    stamp cannot move during a long draft.  The prior defaults (120 s stall /
    180 s poll) came within 1.7 s of false-killing that healthy run.

    These values give ~2.5x headroom over the observed draft and mirror the
    pipeline's stall philosophy (stall_threshold=300 s + large infra backstop).
    Do NOT tighten without re-measuring real PRD-draft durations — a too-tight
    threshold silently kills thorough drafts mid-stream.
    """
    from ui.server import POLL_TIMEOUT, DEFAULTS

    assert POLL_TIMEOUT >= 900, (
        f"poll_timeout backstop too tight for thorough PRD turns: {POLL_TIMEOUT}"
    )
    assert DEFAULTS["ideas_idle_threshold"] >= 300, (
        f"stall threshold too tight for a single long model call: "
        f"{DEFAULTS['ideas_idle_threshold']}"
    )


def test_150s_silent_gap_stalls_under_old_default_but_survives_under_new(tmp_path):
    """A 150 s silent stamp gap is the danger zone the old default fell into.

    Drives the poll with fully-mocked clocks so a 150 s gap is simulated
    deterministically (no real waiting).  Under the OLD stall_threshold (120 s)
    the poll returns ``stalled``; under the NEW default (300 s) the same gap
    survives and the turn succeeds once ``.done`` appears.  This pins *why* the
    default was raised — if someone reverts ``ideas_idle_threshold`` to 120,
    the ``succeeded`` arm fails.
    """
    poll = _get_poll()
    attempt_start = 1000.0
    _call_idx = [0]

    def _run(threshold: float):
        _call_idx[0] += 1
        sub = tmp_path / f"run{_call_idx[0]}"
        sub.mkdir()
        done = sub / "1.done"
        stamp = sub / "prd_creator_activity.stamp"
        _touch_stamp(stamp, mtime=attempt_start + 0.5)  # fresh for this attempt

        # Wall clock: silence climbs to 150 s (1150.5 - 1000.5) by iter 3.
        time_seq = iter([1000.5, 1050.0, 1150.5, 1150.6, 1150.7])

        def fake_time():
            try:
                return next(time_seq)
            except StopIteration:
                return 1150.7

        # Monotonic: start consumes first value; elapsed stays far below 900.
        mono_seq = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])

        def fake_monotonic():
            try:
                return next(mono_seq)
            except StopIteration:
                return 6.0

        ticks = [0]

        async def fake_sleep(_interval):
            ticks[0] += 1
            if ticks[0] >= 3:  # agent finishes the draft after the 3rd silent tick
                done.write_text("done")

        with patch("ui.server.time.time", fake_time), \
             patch("ui.server.time.monotonic", fake_monotonic), \
             patch("ui.server.asyncio.sleep", fake_sleep):
            return asyncio.run(
                poll(
                    done_path=done,
                    stamp_path=stamp,
                    attempt_start_wall=attempt_start,
                    poll_timeout=900.0,
                    poll_interval=0.01,
                    stall_threshold=threshold,
                    startup_grace=30.0,
                )
            )

    old_result = _run(120.0)
    assert old_result.reason == "stalled", (
        f"a 150s gap must trip the OLD 120s threshold; got {old_result.reason}"
    )

    from ui.server import DEFAULTS
    new_result = _run(float(DEFAULTS["ideas_idle_threshold"]))
    assert new_result.reason == "succeeded", (
        f"a 150s gap must survive the NEW {DEFAULTS['ideas_idle_threshold']}s "
        f"threshold; got {new_result.reason}"
    )
    assert bool(new_result) is True


def test_jsonl_and_artifact_helpers_are_deleted():
    """Regression guard — the deleted Tier-B helpers must not be re-introduced.

    ``_resolve_prd_creator_jsonl`` and ``_idea_workspace_activity_mtime`` were
    removed when the Ideas chat poller migrated to stamp-only Tier A.  Any
    future drift that re-adds a stub for either is a sign the migration was
    quietly walked back — fail loudly here rather than letting two parallel
    liveness mechanisms accumulate.
    """
    import ui.server as srv
    assert not hasattr(srv, "_resolve_prd_creator_jsonl"), (
        "JSONL fallback helper should have been deleted with the stamp migration"
    )
    assert not hasattr(srv, "_idea_workspace_activity_mtime"), (
        "Artifact-mtime fallback helper should have been deleted with the stamp migration"
    )


def test_load_config_ideas_env_overrides(monkeypatch, tmp_path):
    """AUTODEV_IDEAS_IDLE_THRESHOLD env var overrides merged config (same pattern as hooks token).

    (The chat send no longer fast-fails on startup grace — it waits for the
    definitive stall/backstop verdict — so there is no ``ideas_startup_grace``
    knob to override.)
    """
    monkeypatch.setenv("AUTODEV_IDEAS_IDLE_THRESHOLD", "999")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        '{"ideas_idle_threshold": 1, "port": 18790}'
    )
    from ui.server import load_config

    cfg = load_config(config_path=str(cfg_path))
    assert cfg["ideas_idle_threshold"] == 999.0


def test_late_done_valid_accepts_sentinel_within_mtime_slack(tmp_path):
    """``.done`` mtime may trail ``attempt_start_wall`` by a second on coarse FS."""
    from ui.server import IDEAS_LATE_DONE_MTIME_SLACK_SEC, _late_done_valid_for_attempt

    done = tmp_path / "3.done"
    done.write_text("done")
    attempt_wall = time.time()
    back = attempt_wall - (IDEAS_LATE_DONE_MTIME_SLACK_SEC - 1.0)
    os.utime(done, (back, back))
    assert _late_done_valid_for_attempt(done, attempt_wall) is True


def test_late_done_valid_rejects_stale_sentinel(tmp_path):
    from ui.server import IDEAS_LATE_DONE_MTIME_SLACK_SEC, _late_done_valid_for_attempt

    done = tmp_path / "3.done"
    done.write_text("done")
    attempt_wall = time.time()
    old = attempt_wall - IDEAS_LATE_DONE_MTIME_SLACK_SEC - 50.0
    os.utime(done, (old, old))
    assert _late_done_valid_for_attempt(done, attempt_wall) is False
