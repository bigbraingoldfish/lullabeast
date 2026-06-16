import os
import sys
import json
import logging
import logging.handlers
from datetime import datetime, timezone, timedelta

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from env_resolvers import (  # noqa: E402
    load_repo_env_file,
    resolve_openclaw_root,
    resolve_pipeline_root,
)
from atomic_io import write_json_atomic  # noqa: E402

# Cron self-load: under system cron `.env` is not sourced, so populate any unset
# canonical vars from <repo>/.env before resolving the roots (setdefault — a
# properly sourced or explicitly-exported env still wins).
load_repo_env_file()

OPENCLAW_ROOT = resolve_openclaw_root()
AUTODEV_REPO_PATH = os.environ.get(
    "AUTODEV_REPO_PATH",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
AUTODEV_PIPELINE_ROOT = resolve_pipeline_root(AUTODEV_REPO_PATH)

# This cron's OWN log intentionally stays under OPENCLAW_ROOT, co-located with the
# OpenClaw session state it prunes — unlike the pipeline runtime logs rotated below.
LOG_FILE = os.path.join(OPENCLAW_ROOT, "session_cleanup.log")

# Setup logging with simple log rotation (keep size small). delay=True opens the
# log file lazily on the first record, so a bad OPENCLAW_ROOT can't crash at import
# (the file lives under it) before main()'s fail-loud guard gets to report cleanly.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5*1024*1024, backupCount=1, delay=True),
        logging.StreamHandler()
    ]
)


AGENTS = ["planner", "executor", "reviewer", "escalation"]
TTL_DAYS = 30

# A plausible millisecond epoch is ~1.7e12 (13 digits). Anything below this — a
# seconds-magnitude value, 0, or garbage — is treated as "unknown age, keep".
_MIN_PLAUSIBLE_MS = 1_000_000_000_000

# Per-session on-disk artefacts, all keyed off the {sessionId} filename stem.
_TRANSCRIPT_SUFFIXES = (".jsonl", ".trajectory.jsonl", ".trajectory-path.json")


def rotate_pipeline_logs():
    """Truncate the pipeline runtime logs to their last ~1000 lines past 5 MB.

    ``heartbeat.log`` (heartbeat-cron stdout, operator-provisioned) and
    ``orchestrator.log`` (orchestrator stdout, written by both
    ``heartbeat_cron.start_orchestrator`` and the UI's ``_spawn_orchestrator``) both
    live under ``AUTODEV_PIPELINE_ROOT`` — the ``.autodev`` pipeline-state directory —
    not under ``OPENCLAW_ROOT``. Resolving them against the wrong root makes the size
    check silently no-op, so the real logs grow unbounded (an SD-card-exhaustion risk
    on the Pi). The ``os.path.exists`` guard provides the ``missingok`` tolerance
    documented in PIPELINE-CONSTRAINTS.md §1: either file may be absent in a given
    deployment. (``session_cleanup.log`` is this cron's own log and stays under
    ``OPENCLAW_ROOT`` — see the ``LOG_FILE`` constant.)
    """
    for log_name in ["heartbeat.log", "orchestrator.log"]:
        log_path = os.path.join(AUTODEV_PIPELINE_ROOT, log_name)
        if os.path.exists(log_path):
            try:
                # We use a simple strategy: if it exceeds 5MB, keep newest 1MB
                if os.path.getsize(log_path) > 5 * 1024 * 1024:
                    with open(log_path, "r") as f:
                        lines = f.readlines()
                    # Keep last 1000 lines approx
                    with open(log_path, "w") as f:
                        f.writelines(lines[-1000:])
                    logging.info(f"Rotated {log_name}")
            except Exception as e:
                logging.error(f"Failed to rotate {log_name}: {e}")


def _atomic_write_json(path, data):
    """Write *data* as JSON to *path* atomically.

    Thin wrapper over the shared :func:`atomic_io.write_json_atomic` (LAUNCH-5):
    unique ``mkstemp`` temp in the target dir, ``os.replace`` commit, temp removed
    on failure. Re-raises (``raise_on_error`` default) so the caller learns of a
    failed prune-index write *before* it deletes the transcripts.
    """
    write_json_atomic(path, data, indent=2)


def _is_valid_ms_timestamp(value):
    """True only for a plausible millisecond epoch.

    Rejects ``bool`` (an ``int`` subclass), non-numeric values, and magnitudes too
    small to be milliseconds. A rejected value is the signal to KEEP the session
    (fail-safe) rather than treat a missing/garbage ``updatedAt`` as epoch-old and
    delete it — the original cron's unsafe default.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value >= _MIN_PLAUSIBLE_MS


def _transcript_stem(entry):
    """Return the ``{sessionId}`` filename stem for a session entry, or ``None``.

    Prefers the ``sessionFile`` basename (the authoritative on-disk name), falling
    back to ``sessionId``. ``os.path.basename`` strips any directory component, so a
    ``sessionFile`` pointing elsewhere collapses to a bare filename that is only ever
    joined back inside the agent's own sessions dir.
    """
    source = entry.get("sessionFile")
    if not (isinstance(source, str) and source.strip()):
        sid = entry.get("sessionId")
        if not (isinstance(sid, str) and sid.strip()):
            return None
        source = f"{sid}.jsonl"
    base = os.path.basename(source)
    if base.endswith(".jsonl"):
        base = base[: -len(".jsonl")]
    return base or None


def _delete_session_transcripts(sessions_dir, stem):
    """Delete ``{stem}.jsonl`` and its trajectory siblings inside *sessions_dir*.

    Each candidate is realpath-checked to fall inside *sessions_dir* before removal
    (defence in depth against a crafted stem); a missing file is ignored. The
    trajectory siblings are removed alongside the transcript — leaving them orphaned
    would defeat the SD-card reclamation this cron exists for.
    """
    real_dir = os.path.realpath(sessions_dir)
    for suffix in _TRANSCRIPT_SUFFIXES:
        candidate = os.path.join(sessions_dir, stem + suffix)
        if os.path.commonpath([real_dir, os.path.realpath(candidate)]) != real_dir:
            logging.warning("Refusing to delete out-of-bounds transcript: %s", candidate)
            continue
        try:
            os.remove(candidate)
        except FileNotFoundError:
            pass


def cleanup_sessions(dry_run=False):
    """Prune OpenClaw sessions older than ``TTL_DAYS`` from each agent's store.

    Reads the real store at ``OPENCLAW_ROOT/agents/{agent}/sessions/sessions.json``
    — a **flat dict keyed by sessionKey** — and removes entries whose ``updatedAt``
    (epoch milliseconds) is older than the cutoff, deleting each pruned session's
    transcript artefacts. The pruned index is persisted atomically **before** any
    transcript is deleted (an interrupted run then orphans transcript files rather
    than corrupting the index). The escalation agent is exempt (audit trail), and a
    missing/invalid ``updatedAt`` is kept-and-warned, never deleted.

    When *dry_run* is True the would-be-pruned counts are logged per agent but the
    index is NOT rewritten and NO transcript is deleted — a safety valve for the
    first enable, when a long-unpruned backlog would be pruned in one pass.
    """
    rotate_pipeline_logs()

    cutoff_ms = (datetime.now(timezone.utc) - timedelta(days=TTL_DAYS)).timestamp() * 1000.0
    mode = "dry-run" if dry_run else "live"
    agents_scanned = total_pruned = total_kept = 0

    for agent in AGENTS:
        # DO NOT touch escalation agent sessions (audit trail preservation)
        if agent == "escalation":
            continue

        sessions_json_path = os.path.join(
            OPENCLAW_ROOT, "agents", agent, "sessions", "sessions.json")
        sessions_dir = os.path.dirname(sessions_json_path)

        if not os.path.exists(sessions_json_path):
            continue

        try:
            with open(sessions_json_path, "r") as f:
                store = json.load(f)

            if not isinstance(store, dict):
                logging.warning(
                    "Sessions store for %s is not a dict (%s); skipping.",
                    agent, type(store).__name__)
                continue

            survivors = {}
            to_prune = []  # (session_key, entry) pairs older than the TTL

            for key, entry in store.items():
                if not isinstance(entry, dict):
                    logging.warning(
                        "Keeping non-dict session entry %r for %s.", key, agent)
                    survivors[key] = entry
                    continue
                updated = entry.get("updatedAt")
                if not _is_valid_ms_timestamp(updated):
                    logging.warning(
                        "Keeping session %r for %s: missing/invalid updatedAt (%r).",
                        key, agent, updated)
                    survivors[key] = entry
                    continue
                if updated < cutoff_ms:
                    to_prune.append((key, entry))
                else:
                    survivors[key] = entry

            if dry_run:
                # Always report per agent — even 0 prunable — so a preview run is
                # never silent (the "looks broken" case when nothing is old enough).
                logging.info(
                    "[DRY-RUN] %s: would delete %d of %d session(s) (keep %d) — "
                    "no changes written.",
                    agent, len(to_prune), len(store), len(survivors))
                agents_scanned += 1
                total_pruned += len(to_prune)
                total_kept += len(survivors)
                continue

            if not to_prune:
                logging.info(
                    "%s: nothing older than %d days (%d session(s) kept).",
                    agent, TTL_DAYS, len(survivors))
                agents_scanned += 1
                total_kept += len(survivors)
                continue

            # Persist the pruned index FIRST (atomic), THEN delete the now-unreferenced
            # transcripts. A crash between the two leaves orphaned transcript files
            # (harmless) rather than an index referencing missing transcripts.
            _atomic_write_json(sessions_json_path, survivors)
            for _key, entry in to_prune:
                stem = _transcript_stem(entry)
                if stem:
                    _delete_session_transcripts(sessions_dir, stem)

            logging.info(
                "Deleted %d stale session(s) for %s agent (kept %d).",
                len(to_prune), agent, len(survivors))
            agents_scanned += 1
            total_pruned += len(to_prune)
            total_kept += len(survivors)

        except Exception as e:
            logging.error("Failed to cleanup sessions for %s: %s", agent, e)

    logging.info(
        "Session cleanup complete (%s): scanned %d agent(s); %s %d session(s), kept %d.",
        mode, agents_scanned,
        "would prune" if dry_run else "pruned", total_pruned, total_kept)


def main():
    """Cron entry point: log the resolved roots, refuse to run on a broken
    ``OPENCLAW_ROOT`` (the session store lives under it), then prune.

    A bare cron environment is the usual cause of a bad root;
    ``load_repo_env_file`` at import already tried ``<repo>/.env``, so reaching the
    guard means the root is genuinely absent.

    Pass ``--dry-run`` (or set ``SESSION_CLEANUP_DRY_RUN=1``) to log what WOULD be
    pruned per agent without writing the index or deleting any transcript — run it
    once this way before the first real enable to preview the backlog.
    """
    dry_run = ("--dry-run" in sys.argv[1:]) or (
        os.environ.get("SESSION_CLEANUP_DRY_RUN", "").strip().lower()
        in ("1", "true", "yes"))
    print(
        f"[STARTUP] OPENCLAW_ROOT={OPENCLAW_ROOT} "
        f"AUTODEV_PIPELINE_ROOT={AUTODEV_PIPELINE_ROOT} dry_run={dry_run}",
        flush=True,
    )
    if not os.path.isdir(OPENCLAW_ROOT):
        print(
            f"[CRITICAL] OPENCLAW_ROOT is not a directory (resolved={OPENCLAW_ROOT!r}) "
            "— refusing to run session cleanup; the session store lives under it. "
            "Check the cron environment / .env (is HOME set, is .env present?).",
            flush=True,
        )
        sys.exit(1)
    cleanup_sessions(dry_run=dry_run)


if __name__ == "__main__":
    main()
