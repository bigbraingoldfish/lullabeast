import os
import sys
import fcntl
import json
import subprocess
from datetime import datetime, timezone, timedelta

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from env_resolvers import resolve_openclaw_root, resolve_pipeline_root  # noqa: E402

OPENCLAW_ROOT = resolve_openclaw_root()
AUTODEV_REPO_PATH = os.environ.get(
    "AUTODEV_REPO_PATH",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


AUTODEV_PIPELINE_ROOT = resolve_pipeline_root(AUTODEV_REPO_PATH)

LOCK_FILE = os.path.join(AUTODEV_PIPELINE_ROOT, "pipeline.lock")
STATE_FILE = os.path.join(AUTODEV_PIPELINE_ROOT, "pipeline_state.json")
ORCHESTRATOR_SCRIPT = os.path.join(AUTODEV_REPO_PATH, "autodev", "pipeline", "orchestrator.py")
LOG_FILE = os.path.join(AUTODEV_PIPELINE_ROOT, "orchestrator.log")

# Stale mid-flight: lock is free (orchestrator dead) but state still claims active work.
# With agent_end writing the .done sentinel immediately when the session closes, the
# orchestrator can resume from that sentinel on restart without re-running the agent.
# 3 minutes is enough to confirm the orchestrator process is genuinely gone and not
# just slow to start. The old 15-minute value was calibrated against the planner's
# 10-minute poll_for_sentinel cap — that cap is now an infrastructure-failure backstop
# (hours), not a heuristic bound on agent runtime.
STALE_FLIGHT_THRESHOLD_MINUTES = 3

# States where no orchestrator process is expected and no action is needed.
# The pipeline has reached a resting state intentionally.
_IDLE_STATES = frozenset({
    "IDLE",
    "PIPELINE_COMPLETE",
    "STOPPED",
    "QUEUE_HALTED",
    "HALTED_SILENT",
    "BLOCKED",
    "WAITING_FOR_HUMAN",
})


def _parse_last_action_utc(last_action_str: str) -> datetime | None:
    """Parse pipeline_state last_action_timestamp to timezone-aware UTC, or None."""
    if not last_action_str or not str(last_action_str).strip():
        return None
    try:
        s = str(last_action_str).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _is_stale_orphaned_midflight(state: dict) -> bool:
    """True when state claims active work but last_action is older than the threshold.

    Only applies to RUNNING and WAITING_FOR_SENTINEL — the two states where an
    orchestrator process is expected to be running and holding the lock.
    """
    ps = state.get("pipeline_status", "")
    if ps not in ("RUNNING", "WAITING_FOR_SENTINEL"):
        return False
    last_action_time = _parse_last_action_utc(state.get("last_action_timestamp") or "")
    if last_action_time is None:
        return False
    return datetime.now(timezone.utc) - last_action_time > timedelta(minutes=STALE_FLIGHT_THRESHOLD_MINUTES)


def start_orchestrator(project_path: str) -> None:
    print("[INFO] Starting orchestrator process...")
    os.makedirs(AUTODEV_PIPELINE_ROOT, exist_ok=True)
    env = os.environ.copy()
    env["OPENCLAW_ROOT"] = OPENCLAW_ROOT
    env["AUTODEV_REPO_PATH"] = AUTODEV_REPO_PATH
    env["AUTODEV_PIPELINE_ROOT"] = AUTODEV_PIPELINE_ROOT
    with open(LOG_FILE, "a") as log:
        subprocess.Popen(
            [sys.executable, ORCHESTRATOR_SCRIPT, "--project-path", project_path],
            cwd=AUTODEV_REPO_PATH,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )


def run_heartbeat() -> None:
    """Self-healing watchdog. Never sends notifications — the escalation agent owns
    all human communication.

    Decision tree (fully deterministic, no model query, no SIGTERM):

    Lock HELD (orchestrator alive):
      → Log status and exit. No intervention.

      Rationale: with the agent_end plugin, poll_for_sentinel unblocks the moment an
      agent session closes.  poll_for_sentinel also has its own hard timeouts (1200 s
      executor, 600 s reviewer) as a backstop for truly hung agents.  There is no
      scenario where the orchestrator can be permanently stuck — it will always either
      receive the .done sentinel or time out and handle the retry itself.

      The old SIGTERM-on-15-min-WAITING_FOR_SENTINEL check is removed because
      last_action_timestamp is written once when the orchestrator transitions to
      WAITING_FOR_SENTINEL (before the webhook fires) and is never updated until the
      agent finishes.  A complex phase legitimately running for 20+ minutes would
      trigger the SIGTERM mid-work, burning all retry attempts and forcing manual
      escalation for work that was completing successfully.

    Lock FREE (orchestrator dead):
      - No state file → log, exit
      - State is an idle/terminal state → log "not active, no action", exit
      - State claims active work AND stale (> 15 min) → restart orchestrator
      - State claims active work AND fresh (< 15 min) → log "monitoring", exit
        (will restart on the next cycle once the threshold is reached)
    """
    lock_fd = None
    try:
        lock_fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o666)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # ── LOCK HELD ─ orchestrator is alive, nothing to do ─────────────
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r") as f:
                    state = json.load(f)
                print(
                    f"[INFO] Orchestrator alive. "
                    f"status={state.get('pipeline_status')!r}"
                )
            else:
                print("[INFO] Orchestrator alive.")
            return

        # ── LOCK ACQUIRED ─ orchestrator is dead/missing ──────────────────────
        if not os.path.exists(STATE_FILE):
            print("[INFO] No state file — pipeline never started or was cleaned up. No action.")
            return

        with open(STATE_FILE, "r") as f:
            state = json.load(f)

        pipeline_status = state.get("pipeline_status", "")

        # Pipeline is in a known resting state — no orchestrator expected, nothing to do.
        if pipeline_status in _IDLE_STATES:
            print(f"[INFO] Pipeline at rest (status={pipeline_status!r}). No action needed.")
            return

        # Pipeline claims active work (RUNNING / WAITING_FOR_SENTINEL).
        if _is_stale_orphaned_midflight(state):
            project_path = state.get("project_path", "")
            if not project_path:
                print(
                    "[ERROR] Stale mid-flight state but project_path missing from "
                    "pipeline_state.json. Cannot auto-restart — check state file manually."
                )
                return
            age_min = int(
                (
                    datetime.now(timezone.utc)
                    - _parse_last_action_utc(state.get("last_action_timestamp") or "")
                ).total_seconds()
                // 60
            )
            print(
                f"[INFO] Stale mid-flight: orchestrator dead, status={pipeline_status!r}, "
                f"last_action {age_min}m ago (>{STALE_FLIGHT_THRESHOLD_MINUTES}m). Restarting."
            )
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
            lock_fd = None
            start_orchestrator(project_path)
            return

        # Active state but fresh — orchestrator may have just crashed.
        # Wait for the next cycle; it will be stale by then and auto-restart will fire.
        last_action_time = _parse_last_action_utc(state.get("last_action_timestamp") or "")
        if last_action_time:
            age_min = int(
                (datetime.now(timezone.utc) - last_action_time).total_seconds() // 60
            )
            print(
                f"[INFO] Possible recent crash: status={pipeline_status!r}, "
                f"last_action {age_min}m ago (< {STALE_FLIGHT_THRESHOLD_MINUTES}m threshold). "
                "Monitoring — will auto-restart next cycle if still stale."
            )
        else:
            print(
                f"[INFO] Orchestrator absent, status={pipeline_status!r}, "
                "no last_action_timestamp. Monitoring."
            )

    except Exception as e:
        print(f"[ERROR] Heartbeat encountered error: {e}")
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            except OSError:
                pass


if __name__ == "__main__":
    print(
        f"[STARTUP] OPENCLAW_ROOT={OPENCLAW_ROOT} "
        f"AUTODEV_PIPELINE_ROOT={AUTODEV_PIPELINE_ROOT} "
        f"STATE_FILE={STATE_FILE}",
        flush=True,
    )
    run_heartbeat()
