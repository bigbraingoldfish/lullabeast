import os
import sys
import fcntl
import json
import time
import signal
import subprocess
import requests
from datetime import datetime, timezone, timedelta

AUTODEV_ROOT = os.environ.get("AUTODEV_ROOT", os.path.expanduser("~/.openclaw"))
AUTODEV_REPO_PATH = os.environ.get(
    "AUTODEV_REPO_PATH",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
LOCK_FILE = os.path.join(AUTODEV_ROOT, "pipeline.lock")
STATE_FILE = os.path.join(AUTODEV_ROOT, "pipeline_state.json")
CONFIG_FILE = os.path.join(AUTODEV_ROOT, "openclaw.json")
ORCHESTRATOR_SCRIPT = os.path.join(AUTODEV_REPO_PATH, "autodev", "pipeline", "orchestrator.py")
LOG_FILE = os.path.join(AUTODEV_ROOT, "orchestrator.log")

# Local llama-server endpoint (B7). Requires Main Machine Plan A Phase 4 complete.
HEARTBEAT_MODEL_URL = "http://<llama-server-host>:11434/v1/chat/completions"
HEARTBEAT_MODEL_NAME = "qwen3.5-27b"

HEARTBEAT_SYSTEM_PROMPT = """
You are a pipeline heartbeat monitor. You will be given the current state of an autonomous development pipeline.
Your only job is to classify the state and output exactly one of three tokens: RESUME, WAIT, or NOTIFY.

Rules:
- RESUME: The orchestrator appears dead (lock is free) and the state is safe to resume automatically. Only output RESUME if pipeline_status is RUNNING or WAITING_FOR_SENTINEL and the orchestrator process is confirmed dead.
- WAIT: The orchestrator is alive, or the pipeline is in WAITING_FOR_HUMAN or HALTED_SILENT state. Do not intervene.
- NOTIFY: The state does not clearly match RESUME or WAIT. Something is wrong but you cannot safely classify it. Alert the human.

Output exactly one word. No explanation. No punctuation. No other text.
""".strip()


def send_signal_notification(message):
    """Send a raw Signal notification via the local OpenClaw gateway."""
    token = ""
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        token = config.get("hooks", {}).get("token", "")
    except Exception:
        pass
    payload = {"channel": "signal", "message": message}
    try:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        r = requests.post("http://localhost:18789/hooks/agent", json=payload, headers=headers, timeout=10)
        r.raise_for_status()
        print(f"[INFO] Signal notification sent.")
    except Exception as e:
        print(f"[ERROR] Failed to send signal notification: {e}")


def query_heartbeat_model(state_json: str) -> str:
    """Query local llama-server for RESUME/WAIT/NOTIFY decision.
    Raises requests.exceptions.ConnectionError if the server is unreachable."""
    resp = requests.post(
        HEARTBEAT_MODEL_URL,
        json={
            "model": HEARTBEAT_MODEL_NAME,
            "messages": [
                {"role": "system", "content": HEARTBEAT_SYSTEM_PROMPT},
                {"role": "user", "content": f"Pipeline state:\n{state_json}"}
            ],
            "temperature": 0.1,
            "max_tokens": 5,
            "presence_penalty": 0.0
        },
        timeout=30
    )
    return resp.json()["choices"][0]["message"]["content"].strip().upper()


def start_orchestrator(project_path):
    print("[INFO] Starting orchestrator process...")
    # Use Popen to run it in background detached from cron so it keeps running
    with open(LOG_FILE, "a") as log:
        subprocess.Popen(
            [sys.executable, ORCHESTRATOR_SCRIPT, "--project-path", project_path],
            cwd=AUTODEV_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True
        )


def run_heartbeat():
    lock_fd = None
    try:
        # We need a file descriptor to attempt locking
        lock_fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o666)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # LOCK IS HELD => Orchestrator is theoretically alive.
            # Check for stuck sentinel (> 15 mins)
            if not os.path.exists(STATE_FILE):
                print("[INFO] State file missing, nothing to check.")
                return

            with open(STATE_FILE, 'r') as f:
                state = json.load(f)

            if state.get("pipeline_status") == "WAITING_FOR_SENTINEL":
                last_action_str = state.get("last_action_timestamp")
                if last_action_str:
                    last_action_time = datetime.fromisoformat(last_action_str)
                    # Use 15 minutes to stagger behind the internal 10-minute timeout
                    if datetime.now(timezone.utc) - last_action_time > timedelta(minutes=15):
                        print("[ERROR] Stuck sentinel timeout (>15 mins) exceeded. Orchestrator is deadlocked.")
                        # Parse PID from lockfile metadata
                        os.lseek(lock_fd, 0, os.SEEK_SET)
                        lock_data = os.read(lock_fd, 1024).decode('utf-8')
                        if lock_data:
                            try:
                                metadata = json.loads(lock_data)
                                pid = metadata.get("pid")
                                if pid:
                                    print(f"[INFO] Terminating stuck PID {pid}...")
                                    os.kill(pid, signal.SIGTERM)
                                    time.sleep(2) # Give OS time to drop the POSIX lock
                            except Exception as e:
                                print(f"[ERROR] Failed to read PID from lockfile: {e}")

                        # Loop back to run_heartbeat to acquire lock and restart
                        os.close(lock_fd)
                        return run_heartbeat()
            print("[INFO] Orchestrator is alive and healthy.")
            return

        # IF WE GET HERE => LOCK ACQUIRED => Orchestrator is dead/missing
        if not os.path.exists(STATE_FILE):
            print("[INFO] No state file found, cannot recover. Exiting.")
            return

        with open(STATE_FILE, 'r') as f:
            state = json.load(f)

        # Query local model for RESUME / WAIT / NOTIFY decision (B7).
        # Conservative fallback: if model is unreachable, notify human rather than guessing.
        # Exception: if the pipeline is not actively running (HALTED_SILENT, WAITING_FOR_HUMAN,
        # BLOCKED), model unreachability does not block any work in flight.  Suppress the alert
        # in those cases to avoid false-positive notifications when the model server is simply
        # restarting while the pipeline is intentionally paused or stopped.
        pipeline_status = state.get("pipeline_status", "")
        model_alert_required = pipeline_status in ("RUNNING", "WAITING_FOR_SENTINEL")
        try:
            decision = query_heartbeat_model(json.dumps(state))
        except requests.exceptions.ConnectionError:
            if model_alert_required:
                send_signal_notification(
                    "Heartbeat: local model unreachable. Cannot assess pipeline state. Manual check required."
                )
            else:
                print(f"[INFO] Heartbeat: model unreachable, pipeline is {pipeline_status!r} (not actively running). No alert sent.")
            return
        except Exception as e:
            if model_alert_required:
                send_signal_notification(
                    f"Heartbeat: model query failed ({e}). Cannot assess pipeline state. Manual check required."
                )
            else:
                print(f"[INFO] Heartbeat: model query failed ({e}), pipeline is {pipeline_status!r} (not actively running). No alert sent.")
            return

        if decision == "RESUME":
            project_path = state.get("project_path", "")
            if not project_path:
                print("[ERROR] Heartbeat: cannot restart, project_path missing from pipeline_state.json. Manual intervention required.")
                return
            print("[INFO] Stale lock detected (orchestrator dead). Model decision: RESUME. Restarting...")
            # We have the lock, release it so orchestrator can acquire it
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
            start_orchestrator(project_path)
        elif decision == "WAIT":
            print(f"[INFO] Model decision: WAIT. Pipeline healthy or correctly paused ({state.get('pipeline_status')}).")
        elif decision == "NOTIFY":
            send_signal_notification(
                f"Heartbeat: unclassified pipeline state. Manual check required.\n"
                f"Status: {state.get('pipeline_status')} | Last action: {state.get('last_action')}"
            )
        else:
            # Model returned something unexpected — conservative: treat as NOTIFY
            send_signal_notification(
                f"Heartbeat: model returned unexpected token '{decision}'. Manual check required.\n"
                f"Status: {state.get('pipeline_status')} | Last action: {state.get('last_action')}"
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
    run_heartbeat()
