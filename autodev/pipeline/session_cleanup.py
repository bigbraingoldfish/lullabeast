import os
import sys
import json
import logging
import logging.handlers
from datetime import datetime, timezone, timedelta

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from env_resolvers import resolve_openclaw_root, resolve_pipeline_root  # noqa: E402

OPENCLAW_ROOT = resolve_openclaw_root()
AUTODEV_REPO_PATH = os.environ.get(
    "AUTODEV_REPO_PATH",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
AUTODEV_PIPELINE_ROOT = resolve_pipeline_root(AUTODEV_REPO_PATH)

# This cron's OWN log intentionally stays under OPENCLAW_ROOT, co-located with the
# OpenClaw session state it prunes — unlike the pipeline runtime logs rotated below.
LOG_FILE = os.path.join(OPENCLAW_ROOT, "session_cleanup.log")

# Setup logging with simple log rotation (keep size small)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=1),
        logging.StreamHandler()
    ]
)


AGENTS = ["planner", "executor", "reviewer", "escalation"]
TTL_DAYS = 30

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

def cleanup_sessions():
    rotate_pipeline_logs()
    
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=TTL_DAYS)
    
    for agent in AGENTS:
        # DO NOT touch escalation agent sessions (audit trail preservation)
        if agent == "escalation":
            continue
            
        sessions_json_path = os.path.join(OPENCLAW_ROOT, f"workspace-{agent}", "sessions", "sessions.json")
        sessions_dir = os.path.dirname(sessions_json_path)
        
        if not os.path.exists(sessions_json_path):
            continue
            
        try:
            with open(sessions_json_path, 'r') as f:
                store = json.load(f)
                
            sessions = store.get("sessions", [])
            new_sessions = []
            deleted_count = 0
            
            for session in sessions:
                updated_at_ms = session.get("updatedAt", 0)
                try:
                    updated_date = datetime.fromtimestamp(updated_at_ms / 1000.0, tz=timezone.utc)
                except Exception:
                    # Fallback to keep if parse fails
                    new_sessions.append(session)
                    continue
                    
                if updated_date < cutoff_date:
                    session_id = session.get("sessionId")
                    # Delete jsonl
                    if session_id:
                        jsonl_path = os.path.join(sessions_dir, f"{session_id}.jsonl")
                        if os.path.exists(jsonl_path):
                            os.remove(jsonl_path)
                    deleted_count += 1
                else:
                    new_sessions.append(session)
                    
            if deleted_count > 0:
                store["sessions"] = new_sessions
                store["count"] = len(new_sessions)
                with open(sessions_json_path, 'w') as f:
                    json.dump(store, f, indent=2)
                logging.info(f"Deleted {deleted_count} stale sessions for {agent} agent.")
                
        except Exception as e:
            logging.error(f"Failed to cleanup sessions for {agent}: {e}")

if __name__ == "__main__":
    cleanup_sessions()
