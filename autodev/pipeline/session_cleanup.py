import os
import json
import logging
import logging.handlers
from datetime import datetime, timezone, timedelta

AUTODEV_ROOT = os.environ.get("AUTODEV_ROOT", os.path.expanduser("~/.openclaw"))
LOG_FILE = os.path.join(AUTODEV_ROOT, "session_cleanup.log")

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
    for log_name in ["heartbeat.log", "orchestrator.log"]:
        log_path = os.path.join(AUTODEV_ROOT, log_name)
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
            
        sessions_json_path = os.path.join(AUTODEV_ROOT, f"workspace-{agent}", "sessions", "sessions.json")
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
