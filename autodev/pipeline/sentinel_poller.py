import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from env_resolvers import resolve_openclaw_root  # noqa: E402

OPENCLAW_ROOT = resolve_openclaw_root()


# Recognised PollResult.reason values.  Kept as a module-level set rather
# than a Literal so we can validate at construction time on older Pythons
# without sacrificing readability.
POLL_REASONS = frozenset(
    {"succeeded", "stalled", "no_first_activity", "stopped", "timeout"}
)


@dataclass(frozen=True)
class PollResult:
    """Structured outcome of :func:`poll_for_sentinel`.

    The orchestrator branches on ``reason`` to decide whether to log a
    stall, escalate to ``HALTED_SILENT`` on a verified-active session, or
    fall through to its existing infrastructure-timeout retry path.

    ``__bool__`` delegates to ``success`` so existing call sites that
    treat the return as a plain bool (``if sentinel_found:``) keep
    working unchanged.

    Fields
    ------
    success:
        ``True`` only when the ``.done`` sentinel was observed before any
        timeout/stall/grace condition fired.
    reason:
        One of:

        * ``"succeeded"``       — ``.done`` observed.
        * ``"stalled"``         — activity stamp went silent after first hook.
        * ``"no_first_activity"`` — startup grace exceeded without ever
          seeing the activity stamp advance.
        * ``"stopped"``         — operator wrote the stop sentinel.
        * ``"timeout"``         — infrastructure backstop ``timeout_seconds``
          elapsed (gateway unreachable etc.).
    stamp_mtime:
        Last observed activity-stamp mtime when the result was produced,
        or ``None`` if no activity was ever recorded.  Used by the
        orchestrator for diagnostic logging only.
    """

    success: bool
    reason: str
    stamp_mtime: Optional[float] = None

    def __post_init__(self) -> None:
        if self.reason not in POLL_REASONS:
            # Frozen dataclass — use object.__setattr__ equivalent indirectly
            # by raising loudly instead of silently accepting bad data.
            raise ValueError(
                f"PollResult.reason must be one of {sorted(POLL_REASONS)}; "
                f"got {self.reason!r}"
            )

    def __bool__(self) -> bool:  # noqa: D401 — trivial delegation
        return self.success


def cleanup_output_files(workspace_dir: str, agent_prefix: str):
    """Deletes agent output JSON, .done sentinel, and activity stamp before a run."""
    base_path = Path(workspace_dir)
    json_path = base_path / f"{agent_prefix}_output.json"
    done_path = base_path / f"{agent_prefix}_output.done"
    stamp_path = base_path / f"{agent_prefix}_activity.stamp"

    for p in [json_path, done_path, stamp_path]:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


def initialize_activity_stamp(workspace_dir: str, agent_prefix: str) -> bool:
    """Seed ``{agent}_activity.stamp`` so missing first hook events can still stall.

    The OpenClaw plugin refreshes this file on model/tool activity.  Creating it
    at attempt start gives ``poll_for_sentinel`` a clock even when a provider,
    browser bridge, or plugin edge case prevents the first hook from firing.
    """
    base_path = Path(workspace_dir)
    if not base_path.exists():
        return False

    stamp_path = base_path / f"{agent_prefix}_activity.stamp"
    tmp_path = None
    try:
        fd, tmp = tempfile.mkstemp(
            dir=str(base_path),
            prefix=f"{agent_prefix}_activity_",
            suffix=".stamp.tmp",
        )
        tmp_path = tmp
        os.close(fd)
        os.replace(tmp_path, stamp_path)
        return True
    except OSError:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return False


def poll_for_sentinel(
    sentinel_path: str,
    timeout_seconds: int = 600,
    stop_sentinel_path: str | None = None,
    min_sentinel_mtime: float | None = None,
    stall_detection_path: str | None = None,
    stall_threshold_seconds: int | None = None,
    startup_grace_seconds: int | None = None,
) -> PollResult:
    """Poll for a sentinel file using a time.sleep loop, strictly avoiding inotify.

    **Normal path:** ``agent_end`` (autodev-pipeline-signals plugin) writes the
    ``.done`` sentinel synchronously the moment the OpenClaw session closes, so
    this function returns ``PollResult(True, "succeeded")`` within the next
    2-second sleep tick. ``timeout_seconds`` is never reached under normal
    operation.

    **Infrastructure-failure backstop:** ``timeout_seconds`` exists solely
    for the scenario where the OpenClaw Gateway crashes between the webhook call
    and session completion, preventing ``agent_end`` from firing.  Returns
    ``PollResult(False, "timeout")`` at the backstop.

    **Two-phase stall detection.**  The original design used one knob
    (``stall_threshold_seconds``) to govern both startup waits and
    mid-turn-silence waits.  That forced a single number to tolerate
    legitimate slow OpenClaw boots (3-10 min) while also being short
    enough to catch a model going quiet mid-response (3-5 min).  The
    knob is now split:

    * ``startup_grace_seconds`` bounds **pre-first-hook** waits.  Until
      the plugin advances the activity stamp at least once, polling
      tolerates silence for at most this long.  Exceeded → returns
      ``PollResult(False, "no_first_activity")``.  Catches OpenClaw
      session-creation hangs and provider auth failures.
    * ``stall_threshold_seconds`` bounds **post-first-hook** silence.
      Once the plugin has touched the stamp, any subsequent gap longer
      than this triggers ``PollResult(False, "stalled")``.  Catches
      model mid-turn deaths.

    Both are optional.  Callers that pass neither get the pre-existing
    infrastructure-only behaviour (backstop ``timeout_seconds``).

    Parameters
    ----------
    sentinel_path:
        Path to the ``.done`` file to watch for.
    timeout_seconds:
        Infrastructure-failure backstop.  Default 600 s is intentionally
        conservative; callers should pass an explicit value appropriate
        to the agent.
    stop_sentinel_path:
        If provided, the loop checks for a pipeline stop request on every
        iteration and returns ``PollResult(False, "stopped")`` if found.
        The sentinel itself is consumed by the orchestrator's
        ``_check_stop_requested``, not here.
    min_sentinel_mtime:
        Wall-clock timestamp (``time.time()``) captured **before** calling
        ``cleanup_output_files()`` for the current attempt.  When set, a
        ``.done`` file whose mtime predates this value is treated as an
        orphaned sentinel from a prior session that was cleaned up but whose
        process wrote the file after the reset completed.  The stale sentinel
        is deleted and polling continues, preserving the retry budget for a
        genuine fresh completion.

        Capture ``time.time()`` immediately before ``cleanup_output_files()``::

            _attempt_start = time.time()  # BEFORE cleanup
            cleanup_output_files(...)
            ...
            poll_for_sentinel(..., min_sentinel_mtime=_attempt_start)
    stall_detection_path:
        Optional path to ``{agent}_activity.stamp`` updated by the plugin on
        model/tool activity. When omitted, neither stall nor grace runs.
    stall_threshold_seconds:
        Seconds of silence (no stamp mtime update) **after first activity**
        before treating the attempt as stalled.  Must be passed together
        with ``stall_detection_path``.
    startup_grace_seconds:
        Seconds to wait for the first stamp advance before declaring
        ``no_first_activity``.  Independent of ``stall_threshold_seconds``.
        When ``None`` the pre-existing bootstrap-guard behaviour is used
        (stall stays dormant until first advance; pre-first-hook silence
        only times out at ``timeout_seconds``).

    Returns
    -------
    PollResult
        Truthy on success (``__bool__`` delegates to ``.success``) for
        backwards compatibility with existing ``if sentinel_found:``
        callers.  Inspect ``.reason`` to distinguish stall, startup
        timeout, infrastructure timeout, and operator stop.
    """
    # Resolve symlink components once at call time.  If _run_preflight_checks (or
    # any other caller) repoints the pipeline-project symlink mid-poll, we keep
    # watching the original real directory rather than silently following the new
    # target and losing the .done file.
    sentinel_path = os.path.realpath(sentinel_path)
    if stall_detection_path is not None:
        stall_detection_path = os.path.realpath(stall_detection_path)
    if stop_sentinel_path is not None:
        stop_sentinel_path = os.path.realpath(stop_sentinel_path)

    start_time = time.monotonic()
    _bootstrap_stamp_mtime: float | None = None
    _agent_has_checked_in = False
    _last_stamp_mtime: float | None = None

    while time.monotonic() - start_time < timeout_seconds:
        if stop_sentinel_path and os.path.exists(stop_sentinel_path):
            return PollResult(False, "stopped", _last_stamp_mtime)
        if stall_detection_path is not None and stall_threshold_seconds is not None:
            if os.path.exists(stall_detection_path):
                try:
                    stamp_mtime = os.path.getmtime(stall_detection_path)
                    _last_stamp_mtime = stamp_mtime
                    if _bootstrap_stamp_mtime is None:
                        _bootstrap_stamp_mtime = stamp_mtime
                    if not _agent_has_checked_in and stamp_mtime > _bootstrap_stamp_mtime:
                        _agent_has_checked_in = True
                    if _agent_has_checked_in:
                        if time.time() - stamp_mtime > stall_threshold_seconds:
                            print(
                                f"[POLL][STALLED] No Tier A activity for >{stall_threshold_seconds}s "
                                f"({stall_detection_path}). Treating as stalled attempt."
                            )
                            return PollResult(False, "stalled", stamp_mtime)
                except OSError:
                    pass

        # Startup-grace branch — runs *after* the stall check so a stamp
        # that has advanced (agent already checked in) cannot be reclassified
        # as a startup timeout.  Independent of ``stall_detection_path``
        # existence so a silently failed ``initialize_activity_stamp`` still
        # produces a timely diagnosis rather than waiting the full
        # infrastructure backstop.
        if (
            startup_grace_seconds is not None
            and not _agent_has_checked_in
            and time.monotonic() - start_time > startup_grace_seconds
        ):
            print(
                f"[POLL][NO_FIRST_ACTIVITY] No first hook within "
                f"{startup_grace_seconds}s ({stall_detection_path}). "
                f"OpenClaw session likely never started or first model_call "
                f"never reached the plugin."
            )
            return PollResult(False, "no_first_activity", _last_stamp_mtime)

        if os.path.exists(sentinel_path):
            if min_sentinel_mtime is not None:
                try:
                    sentinel_mtime = os.path.getmtime(sentinel_path)
                    if sentinel_mtime < min_sentinel_mtime:
                        print(
                            f"[WARN] Stale sentinel discarded "
                            f"(sentinel mtime {sentinel_mtime:.3f} < attempt start "
                            f"{min_sentinel_mtime:.3f}) — orphaned prior session output."
                        )
                        try:
                            os.unlink(sentinel_path)
                        except OSError:
                            pass
                        time.sleep(2)
                        continue
                except OSError:
                    pass
            return PollResult(True, "succeeded", _last_stamp_mtime)
        time.sleep(2)

    return PollResult(False, "timeout", _last_stamp_mtime)
