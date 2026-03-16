"""UI server module."""
import fcntl
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


# Canonical default values
DEFAULTS = {
    "port": 18790,
    "pipeline_state_path": "~/.openclaw/pipeline_state.json",
    "phase_state_path": "~/.openclaw/pipeline-project/phase_state.json",
    "lock_path": "~/.openclaw/pipeline.lock",
    "events_path": "~/.openclaw/pipeline_events.jsonl",
    "roadmap_path": "~/.openclaw/pipeline-project/roadmap.md",
    "project_dir_path": "~/.openclaw/pipeline-project",
}


def load_config(config_path=None):
    """Load configuration from file, with defaults.
    
    Args:
        config_path: Path to JSON config file. If None, uses config.json next to this file.
    
    Returns:
        Dict with configuration keys and values (with ~ expanded to absolute paths).
    """
    if config_path is None:
        config_path = Path(__file__).parent / "config.json"
    
    # Start with defaults
    config = DEFAULTS.copy()
    
    # Merge user config if exists
    if Path(config_path).exists():
        with open(config_path, 'r') as f:
            user_config = json.load(f)
            config.update(user_config)
    
    # Expand ~ on all string values (skip port which is int)
    for key, value in config.items():
        if isinstance(value, str):
            config[key] = os.path.expanduser(value)
    
    return config


# FastAPI app
app = FastAPI()


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"ok": True}


@app.get("/")
def root():
    """Serve index.html if present, otherwise 404."""
    index_path = Path(__file__).parent / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(index_path, media_type="text/html")


def _read_json_file(path):
    """Read a JSON file and return its contents.
    
    Args:
        path: Path to the JSON file.
    
    Returns:
        Parsed JSON dict, or None on error.
    """
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _check_orchestrator_liveness(lock_path):
    """Check if the orchestrator is alive by attempting to acquire a lock.
    
    Args:
        lock_path: Path to the lock file.
    
    Returns:
        True if lock is held by another process (orchestrator alive),
        False if lock is acquirable (orchestrator not alive).
    Raises:
        BlockingIOError: If lock cannot be acquired (another process holds it).
    """
    lock_file = open(lock_path, 'w')
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Lock acquired successfully - release immediately
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
        return False
    except BlockingIOError:
        # Lock is held by another process
        lock_file.close()
        return True


def _determine_event_source(events_path):
    """Determine the event source based on whether the events file exists.
    
    Args:
        events_path: Path to the events file.
    
    Returns:
        'file' if events file exists, 'synthetic' otherwise.
    """
    return 'file' if Path(events_path).exists() else 'synthetic'


@app.get("/api/state")
def get_state():
    """Get the current pipeline state.
    
    Merges pipeline_state.json and phase_state.json, adds server-derived
    fields (orchestrator_alive, event_source), and handles missing files gracefully.
    """
    config = load_config()
    
    pipeline_state_path = config.get('pipeline_state_path')
    phase_state_path = config.get('phase_state_path')
    lock_path = config.get('lock_path')
    events_path = config.get('events_path')
    
    # Expand paths if not already expanded
    pipeline_state_path = os.path.expanduser(pipeline_state_path) if pipeline_state_path else None
    phase_state_path = os.path.expanduser(phase_state_path) if phase_state_path else None
    lock_path = os.path.expanduser(lock_path) if lock_path else None
    events_path = os.path.expanduser(events_path) if events_path else None
    
    # Read pipeline state
    pipeline_state = _read_json_file(pipeline_state_path) if pipeline_state_path else None
    
    # Build response with defaults
    if pipeline_state:
        response = {
            "pipeline_status": pipeline_state.get("pipeline_status", "UNKNOWN"),
            "current_phase": pipeline_state.get("current_phase"),
            "counters": pipeline_state.get("counters", {"success": 0, "failure": 0, "retry": 0}),
        }
    else:
        response = {
            "pipeline_status": "UNKNOWN",
            "current_phase": None,
            "counters": {"success": 0, "failure": 0, "retry": 0},
        }
    
    # Read phase state and conditionally add fields
    if phase_state_path:
        phase_state = _read_json_file(phase_state_path)
        if phase_state:
            if "last_error_code" in phase_state:
                response["last_error_code"] = phase_state["last_error_code"]
            if "escalation_resets" in phase_state:
                response["escalation_resets"] = phase_state["escalation_resets"]
    
    # Add server-derived fields
    # Orchestrator liveness
    if lock_path:
        try:
            response["orchestrator_alive"] = _check_orchestrator_liveness(lock_path)
        except Exception:
            response["orchestrator_alive"] = False
    else:
        response["orchestrator_alive"] = False
    
    # Event source
    response["event_source"] = _determine_event_source(events_path) if events_path else "synthetic"
    
    return response