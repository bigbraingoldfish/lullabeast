import os
import sys
import tempfile
import time
from pathlib import Path

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from env_resolvers import resolve_openclaw_root  # noqa: E402

OPENCLAW_ROOT = resolve_openclaw_root()


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
) -> bool:
    """Poll for a sentinel file using a time.sleep loop, strictly avoiding inotify.

    **Normal path:** ``agent_end`` (autodev-pipeline-signals plugin) writes the
    ``.done`` sentinel synchronously the moment the OpenClaw session closes, so
    this function returns ``True`` within the next 2-second sleep tick.
    ``timeout_seconds`` is never reached under normal operation.

    **Infrastructure-failure backstop only:** ``timeout_seconds`` exists solely
    for the scenario where the OpenClaw Gateway crashes between the webhook call
    and session completion, preventing ``agent_end`` from firing.  These values
    are calibrated for "how long before we declare the gateway permanently down"
    (hours), not "how long we expect the agent to run" (which is irrelevant now
    that we have an authoritative completion signal).

    **In-session stall detection:** When ``stall_detection_path`` and
    ``stall_threshold_seconds`` are both set, the loop checks on every tick
    whether ``stall_detection_path`` exists and its mtime is older than
    ``stall_threshold_seconds`` wall seconds. If so, returns ``False`` immediately
    (same contract as stop sentinel / timeout) so the orchestrator's existing
    retry path runs. The plugin touches this file on ``model_call_started``,
    ``model_call_ended``, and ``after_tool_call`` for pipeline sessions.

    Parameters
    ----------
    sentinel_path:
        Path to the ``.done`` file to watch for.
    timeout_seconds:
        Infrastructure-failure backstop.  Returns False only when the Gateway
        is completely unavailable.  Default 600 s is intentionally conservative;
        callers should pass an explicit value appropriate to the agent.
    stop_sentinel_path:
        If provided, the loop checks for a pipeline stop request on every
        iteration and returns False immediately if found.  The sentinel itself
        is consumed by the orchestrator's ``_check_stop_requested``, not here.
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
        model/tool activity. When omitted, no stall check runs.
    stall_threshold_seconds:
        Seconds of silence (no stamp mtime update) before treating the attempt
        as stalled. Must be passed together with ``stall_detection_path``.
    """
    start_time = time.monotonic()
    _bootstrap_stamp_mtime: float | None = None
    _agent_has_checked_in = False

    while time.monotonic() - start_time < timeout_seconds:
        if stop_sentinel_path and os.path.exists(stop_sentinel_path):
            return False
        if stall_detection_path is not None and stall_threshold_seconds is not None:
            if os.path.exists(stall_detection_path):
                try:
                    stamp_mtime = os.path.getmtime(stall_detection_path)
                    if _bootstrap_stamp_mtime is None:
                        _bootstrap_stamp_mtime = stamp_mtime
                    if not _agent_has_checked_in and stamp_mtime > _bootstrap_stamp_mtime:
                        _agent_has_checked_in = True
                    if _agent_has_checked_in:
                        if time.time() - stamp_mtime > stall_threshold_seconds:
                            print(
                                f"[STALL] No Tier A activity for >{stall_threshold_seconds}s "
                                f"({stall_detection_path}). Treating as stalled attempt."
                            )
                            return False
                except OSError:
                    pass
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
            return True
        time.sleep(2)

    return False
