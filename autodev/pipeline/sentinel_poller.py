import os
import sys
import time
from pathlib import Path

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from env_resolvers import resolve_openclaw_root  # noqa: E402

OPENCLAW_ROOT = resolve_openclaw_root()

def cleanup_output_files(workspace_dir: str, agent_prefix: str):
    """Deletes the specific .done and .json output files before a run."""
    base_path = Path(workspace_dir)
    json_path = base_path / f"{agent_prefix}_output.json"
    done_path = base_path / f"{agent_prefix}_output.done"

    for p in [json_path, done_path]:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass

def poll_for_sentinel(
    symlink_path: str,
    timeout_seconds: int = 600,
    stop_sentinel_path: str | None = None,
) -> bool:
    """Explicitly uses a time.sleep loop, strictly avoiding inotify.

    If stop_sentinel_path is provided, the loop also checks for a stop sentinel
    on every iteration. Returns False immediately if the stop sentinel is detected,
    allowing the caller to handle the halt (the sentinel itself is consumed by the
    orchestrator's _check_stop_requested, not here).
    """
    start_time = time.monotonic()

    while time.monotonic() - start_time < timeout_seconds:
        if stop_sentinel_path and os.path.exists(stop_sentinel_path):
            return False
        if os.path.exists(symlink_path):
            return True
        time.sleep(2)

    return False


def _latest_activity_mtime(jsonl_path: str | None, watch_dirs: list[str]) -> float:
    """Return the most-recent mtime seen across the JSONL file and all watch directories.

    Scans watch_dirs shallowly (skips .git and __pycache__ trees) so the check stays
    cheap even on large projects.  Returns 0.0 if nothing is readable yet.
    """
    latest = 0.0

    # JSONL file
    if jsonl_path:
        try:
            latest = max(latest, os.path.getmtime(jsonl_path))
        except OSError:
            pass

    # Project directories (src/, tests/, top-level files)
    _SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
    for watch_dir in watch_dirs:
        try:
            for root, dirs, files in os.walk(watch_dir):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                for fname in files:
                    try:
                        latest = max(latest, os.path.getmtime(os.path.join(root, fname)))
                    except OSError:
                        pass
        except OSError:
            pass

    return latest


def poll_for_sentinel_with_idle_detect(
    sentinel_path: str,
    jsonl_path: str | None,
    startup_grace: int = 90,
    idle_threshold: int = 120,
    timeout_seconds: int = 600,
    watch_dirs: list[str] | None = None,
    min_sentinel_mtime: float | None = None,
    stop_sentinel_path: str | None = None,
) -> bool:
    """Like poll_for_sentinel, but exits early when all activity sources go quiet.

    Activity is tracked across TWO signal sources:
      1. Session JSONL mtime  — updated on every tool call and model response chunk.
      2. watch_dirs file mtimes — updated whenever the executor writes/modifies a project
         file (new .py files, test files, executor_output.json, lessons.md, etc.).

    The idle clock resets whenever EITHER source shows a newer mtime.  This eliminates
    false-positive idle detection when the model is actively writing code but happens to
    be between JSONL flushes (e.g. MiniMax M2.7 batches responses rather than streaming
    token-by-token, so JSONL can be quiet for minutes while file writes are ongoing).

    idle_threshold history:
      60s  (original)  — JSONL-only; MiniMax M2.7 startup latency alone exceeds this.
      120s (2026-03-13) — JSONL-only; still too short for complex CORE phases where
           inter-tool-call silence exceeds 2 min.  Caused cascading retry/orphan-session
           race conditions.
      360s (2026-03-13) — Blunt fix; worked but meant genuinely-hung sessions weren't
           detected until 6 min of silence, and the threshold gave no advantage over
           the plain 600s hard timeout on truly-stuck sessions.
      120s (2026-03-13) — Restored with watch_dirs + min_sentinel_mtime support.  Now
           the clock resets on any project file write (not just JSONL flushes), and
           stale sentinels from orphaned prior sessions are discarded rather than
           burning executor_retries budget.

    watch_dirs — optional list of directories to scan for file-mtime changes.  Pass the
    project root (SYMLINK_TARGET) so that any code the executor writes resets the clock.
    Directories named .git / __pycache__ / .pytest_cache / .ruff_cache are skipped.

    min_sentinel_mtime — wall-clock timestamp (time.time()) recorded just before
    cleanup_output_files() runs for this attempt.  If the sentinel file's mtime is
    older than this value it was written by an orphaned session from a prior attempt
    (the reset cleaned the code files so the gate would fail anyway).  The stale
    sentinel is deleted and polling continues, preserving the retry budget for a
    genuine fresh completion.  Pass time.time() captured immediately before calling
    cleanup_output_files().

    If jsonl_path is None and watch_dirs is empty/None and no files appear within
    startup_grace seconds, falls back to the standard timeout-only behaviour (safe default).
    """
    start_time = time.monotonic()
    last_activity: float = 0.0
    activity_stable_since: float | None = None
    _watch_dirs = watch_dirs or []
    _jsonl_appeared = jsonl_path is None  # treat as appeared if not watching

    while time.monotonic() - start_time < timeout_seconds:
        if stop_sentinel_path and os.path.exists(stop_sentinel_path):
            return False
        if os.path.exists(sentinel_path):
            # Stale-sentinel guard: if the .done file predates this attempt's cleanup,
            # it belongs to an orphaned prior session whose code was already reset.
            # Discard it and keep waiting rather than burning an executor_retry.
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

        elapsed = time.monotonic() - start_time

        # Determine whether we have any watch targets yet.
        if not _jsonl_appeared and jsonl_path and os.path.exists(jsonl_path):
            _jsonl_appeared = True

        if _jsonl_appeared or _watch_dirs:
            current_activity = _latest_activity_mtime(
                jsonl_path if _jsonl_appeared else None,
                _watch_dirs,
            )

            if current_activity > last_activity:
                last_activity = current_activity
                activity_stable_since = time.monotonic()
            elif activity_stable_since is not None:
                idle_for = time.monotonic() - activity_stable_since
                if idle_for >= idle_threshold:
                    print(
                        f"[WARN] All activity sources idle {idle_for:.0f}s with no sentinel — "
                        f"treating as early timeout. "
                        f"(JSONL: {jsonl_path}, watch_dirs: {_watch_dirs})"
                    )
                    return False

        elif elapsed > startup_grace:
            # Neither JSONL nor watch_dirs showed any activity — disable idle check
            # and fall through to the hard timeout.
            _jsonl_appeared = True  # suppress further startup-grace checks
            jsonl_path = None
            _watch_dirs = []

        time.sleep(2)

    return False
