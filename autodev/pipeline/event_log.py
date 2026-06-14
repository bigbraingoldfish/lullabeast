"""Single source of truth for the ``pipeline_events.jsonl`` file contract.

Both processes that write pipeline events call :func:`append_pipeline_event`, so
the line format and the size-based rotation policy live in exactly one place:

* the orchestrator — ``orchestrator._write_pipeline_event`` (high-volume
  per-phase telemetry) and the terminal ``_write_run_summary`` adjacency, and
* the UI server — ``server._write_operator_event`` (operator interventions,
  which happen while the orchestrator may be stopped, so the server must be able
  to write them itself).

Single-line ``O_APPEND`` writes are atomic on POSIX for lines under ``PIPE_BUF``
(events are well under that), so the two processes can append concurrently
without corrupting each other.

EVENT SCHEMA — one JSON object per line:

    {
      "ts":      "<ISO-8601 UTC, second precision>",   # canonical timestamp key
      "event":   "<event-type string>",                 # canonical type key
      "run_id":  "<uuid>" | null,                       # run identity (null pre-deploy)
      "project": "<active project basename>" | "",
      "phase":   "<phase raw id>" | "",
      "agent":   "<role string>",
      "detail":  { ... }                                # event-specific
    }

``ts`` and ``event`` are the **canonical** keys. Some historical readers also
tolerate the aliases ``timestamp`` / ``event_type`` (the server normalizes them
in ``_poll_pipeline_events_file`` and the frontend dual-keys ``event_type ||
event``); new writers MUST emit ``ts`` / ``event``.

Rotation: when the live file exceeds ``max_bytes`` it is renamed to a timestamped
archive ``pipeline_events.<UTC-ts>.jsonl`` and the oldest archives beyond ``keep``
are pruned, bounding disk use while preserving recent history for analytics. The
rename is via ``os.replace`` (atomic); a lost rotation race (another writer moved
the file first) is swallowed. The server's event tail self-heals across the
rename (it resets its read offset when the live file shrinks).

Thresholds default to 25 MB / keep 5 and are overridable via the
``AUTODEV_EVENTS_MAX_BYTES`` / ``AUTODEV_EVENTS_ARCHIVE_KEEP`` environment
variables (garbage / non-positive values fall back to the default).
"""
import glob
import json
import os
from datetime import datetime, timezone

DEFAULT_MAX_BYTES = 25 * 1024 * 1024  # 25 MB — months of one-line events
DEFAULT_ARCHIVE_KEEP = 5              # keep the 5 most recent archives (~a year+)


def _resolve_positive_int(env_name: str, default: int) -> int:
    """Read a positive int from ``env_name``; fall back to ``default`` on a
    missing, non-numeric, or non-positive value (mirrors load_config's numeric
    coercion discipline so a typo can never disable rotation or crash a write)."""
    raw = (os.environ.get(env_name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _rotate_if_needed(events_path: str, max_bytes: int, keep: int) -> None:
    """Rename ``events_path`` to a timestamped archive and prune old archives if
    the live file exceeds ``max_bytes``. No-op when the file is absent or small."""
    try:
        size = os.path.getsize(events_path)
    except OSError:
        return  # absent / unstatable — nothing to rotate
    if size <= max_bytes:
        return

    base = events_path[:-6] if events_path.endswith(".jsonl") else events_path
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive = f"{base}.{ts}.jsonl"
    try:
        os.replace(events_path, archive)
    except OSError:
        return  # another writer rotated first — fine, our append re-creates the live file

    # Prune oldest archives beyond ``keep``. The timestamp in the name sorts
    # lexicographically == chronologically; the live ``pipeline_events.jsonl``
    # has no middle segment so it is excluded by the glob.
    archives = sorted(glob.glob(f"{base}.*.jsonl"))
    stale = archives[:-keep] if keep > 0 else archives
    for old in stale:
        try:
            os.remove(old)
        except OSError:
            pass


def append_pipeline_event(events_path: str, entry: dict, *, max_bytes=None, keep=None) -> None:
    """Append one event ``entry`` as a JSON line to ``events_path``, rotating
    first if the file is oversized.

    Non-raising on I/O: any ``OSError`` is printed and swallowed — telemetry must
    never break the pipeline loop or an API request. ``max_bytes`` / ``keep``
    default to the env-resolved thresholds when not passed explicitly.
    """
    if max_bytes is None:
        max_bytes = _resolve_positive_int("AUTODEV_EVENTS_MAX_BYTES", DEFAULT_MAX_BYTES)
    if keep is None:
        keep = _resolve_positive_int("AUTODEV_EVENTS_ARCHIVE_KEEP", DEFAULT_ARCHIVE_KEEP)
    try:
        os.makedirs(os.path.dirname(events_path) or ".", exist_ok=True)
        _rotate_if_needed(events_path, max_bytes, keep)
        with open(events_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        print(f"[WARN] append_pipeline_event: {e}")
