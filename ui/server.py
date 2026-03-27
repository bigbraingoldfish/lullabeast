"""UI server module."""
import aiohttp
import fcntl
import json
import logging
import os
import re
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import asyncio

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager

from ui.roadmap_parser import parse_roadmap




# Ring buffer for synthetic events (max 50 entries)
_ring_buffer = deque(maxlen=50)

# Polling state - tracks previous values to detect changes
_polling_state = {
    "pipeline_status": None,
    "current_agent": None,
    "current_phase_raw_id": None,
    "counters": {"planner_retries": 0, "executor_retries": 0, "reviewer_retries": 0}
}

# Lock for thread-safe access to polling state
_polling_lock = asyncio.Lock()

# SSE client tracking
_sse_clients = set()  # Set of asyncio.Queue objects for each connected client
_sse_clients_lock = asyncio.Lock()
_file_positions = {}  # Track file positions per client for file-based tailing
_sse_notify_event = asyncio.Event()  # Event to notify when new events are available

logger = logging.getLogger("autodev.readiness")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _fmt = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
    _stream = logging.StreamHandler()
    _stream.setFormatter(_fmt)
    logger.addHandler(_stream)
    try:
        _file = logging.FileHandler("/tmp/ui-server.log")
        _file.setFormatter(_fmt)
        logger.addHandler(_file)
    except Exception:
        pass
logger.propagate = False
_active_readiness_jobs: set[str] = set()  # idea IDs currently sending readiness webhook
_readiness_job_started_at: dict[str, float] = {}  # idea ID -> epoch seconds
READINESS_ACTIVE_WINDOW_SECONDS = 180


def _create_synthetic_event(event_type, agent=None, phase=None, detail=None):
    """Create a synthetic event dict with required fields.
    
    Args:
        event_type: Type of event (e.g., 'status_changed')
        agent: Current agent name
        phase: Current phase ID
        detail: Additional detail string
    
    Returns:
        Dict with id, ts, event_type, agent, phase, detail fields.
    """
    return {
        "id": str(uuid.uuid4()),
        "ts": datetime.utcnow().isoformat() + "Z",
        "event_type": event_type,
        "agent": agent,
        "phase": phase,
        "detail": detail
    }


async def _poll_state(prev_state):
    """Read pipeline_state.json, track previous values, and detect changes.
    
    Args:
        prev_state: Dict with previous state values (pipeline_status, current_agent, 
                   current_phase_raw_id, counters)
    
    Returns:
        Synthetic event dict if changes detected, None otherwise.
    """
    config = load_config()
    pipeline_state_path = os.path.expanduser(config.get('pipeline_state_path')) if config.get('pipeline_state_path') else None
    
    if not pipeline_state_path:
        return None
    
    current_state = _read_json_file(pipeline_state_path)
    if not current_state:
        return None
    
    # Extract current values
    current_status = current_state.get("pipeline_status")
    current_agent = current_state.get("current_agent")
    current_phase = current_state.get("current_phase_raw_id")
    current_counters = current_state.get("counters", {})
    
    # Check for changes
    changes = []
    
    if current_status != prev_state.get("pipeline_status"):
        changes.append("status")
    
    if current_agent != prev_state.get("current_agent"):
        changes.append("agent")
    
    if current_phase != prev_state.get("current_phase_raw_id"):
        changes.append("phase")
    
    # Check retry counters
    prev_counters = prev_state.get("counters", {})
    retry_keys = ["planner_retries", "executor_retries", "reviewer_retries"]
    for key in retry_keys:
        current_val = current_counters.get(key, 0)
        prev_val = prev_counters.get(key, 0)
        if current_val != prev_val:
            changes.append(f"retry:{key}")
    
    if not changes:
        return None
    
    # Build event detail
    detail = f"changes: {', '.join(changes)}"
    
    # Create synthetic event
    event = _create_synthetic_event(
        event_type="status_changed",
        agent=current_agent,
        phase=current_phase,
        detail=detail
    )
    
    # Update prev_state in place
    prev_state["pipeline_status"] = current_status
    prev_state["current_agent"] = current_agent
    prev_state["current_phase_raw_id"] = current_phase
    prev_state["counters"] = current_counters.copy()
    
    return event


async def _polling_loop():
    """Background polling loop that runs every 2.5 seconds.
    
    Calls _poll_state() and appends events to ring buffer on changes.
    """
    global _polling_state
    
    while True:
        try:
            async with _polling_lock:
                event = await _poll_state(_polling_state)
            
            if event:
                _ring_buffer.append(event)
                # Notify SSE clients about new event
                try:
                    await _notify_sse_clients(event)
                except Exception:
                    pass
            
            # Check if events file exists
            try:
                config = load_config()
                events_path = config.get('events_path')
                if events_path:
                    events_path = os.path.expanduser(events_path)
                    if Path(events_path).exists():
                        # File exists - API will serve from file
                        pass
            except Exception:
                pass
            
        except Exception as e:
            # Log error but continue polling
            print(f"Polling error: {e}")
        
        try:
            await asyncio.sleep(2.5)
        except asyncio.CancelledError:
            break


async def _notify_sse_clients(event):
    """Notify all connected SSE clients about a new event.
    
    Args:
        event: The event dict to send to clients.
    """
    async with _sse_clients_lock:
        for client_queue in _sse_clients:
            try:
                client_queue.put_nowait(event)
            except asyncio.QueueFull:
                # Client queue full - skip this client
                pass


async def _tail_events_file(events_path, client_id):
    """Async generator that yields new lines from events file as they appear.
    
    Args:
        events_path: Path to the JSONL events file.
        client_id: Unique identifier for this client (used for position tracking).
    
    Yields:
        SSE-formatted event strings for new lines in the file.
    """
    file_path = Path(events_path)
    if not file_path.exists():
        return
    
    # Initialize file position for this client
    async with _sse_clients_lock:
        if client_id not in _file_positions:
            # Start from end of file for new clients
            _file_positions[client_id] = file_path.stat().st_size
    
    while True:
        try:
            await asyncio.sleep(1)  # Poll every second
            
            if not file_path.exists():
                break
            
            current_size = file_path.stat().st_size
            
            async with _sse_clients_lock:
                last_pos = _file_positions.get(client_id, 0)
            
            if current_size > last_pos:
                # Read new content
                with open(file_path, 'r') as f:
                    f.seek(last_pos)
                    new_content = f.read()
                    _file_positions[client_id] = current_size
                
                # Yield each new line as SSE
                for line in new_content.strip().split('\n'):
                    if line.strip():
                        try:
                            event_data = json.loads(line)
                            yield f"data: {json.dumps(event_data)}\n\n"
                        except json.JSONDecodeError:
                            # Skip malformed lines
                            pass
        except asyncio.CancelledError:
            break
        except Exception:
            # Continue on error
            pass


async def _stream_events(events_path, client_id):
    """Async generator that yields SSE-formatted events.
    
    Yields heartbeat every 15 seconds and new ring buffer events when
    polling detects changes. Tracks file position for file-based tailing.
    
    Args:
        events_path: Path to events file (if file-based source).
        client_id: Unique identifier for this client.
    
    Yields:
        SSE-formatted event strings.
    """
    heartbeat_interval = 15
    use_file = events_path and Path(events_path).exists()
    
    # Track last event to avoid duplicates
    last_event = None
    
    while True:
        try:
            if use_file:
                # Stream from file
                async for event_msg in _tail_events_file(events_path, client_id):
                    yield event_msg
            else:
                # Stream from ring buffer
                # Wait for new events or heartbeat
                try:
                    # Wait for event with timeout for heartbeat
                    async with _sse_clients_lock:
                        client_queue = None
                        for q in _sse_clients:
                            # Find the queue for this client
                            pass
                    
                    # Use the notify event to wait for new data
                    _sse_notify_event.clear()
                    
                    # Wait for either a new event or heartbeat timeout
                    wait_task = asyncio.create_task(_sse_notify_event.wait())
                    heartbeat_task = asyncio.create_task(asyncio.sleep(heartbeat_interval))
                    
                    done, pending = await asyncio.wait(
                        [wait_task, heartbeat_task],
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    
                    # Cancel whichever didn't complete
                    for task in pending:
                        task.cancel()
                    
                    if wait_task in done:
                        # New event arrived - get it from ring buffer
                        async with _sse_clients_lock:
                            for q in _sse_clients:
                                try:
                                    event = q.get_nowait()
                                    if event != last_event:
                                        last_event = event
                                        yield f"data: {json.dumps(event)}\n\n"
                                except asyncio.QueueEmpty:
                                    pass
                    else:
                        # Heartbeat timeout
                        yield f"event: heartbeat\ndata: {{}}\n\n"
                        
                except asyncio.CancelledError:
                    break
                    
        except asyncio.CancelledError:
            break
        except Exception as e:
            # Log error but continue
            print(f"Stream error: {e}")
            await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager to start/stop background polling."""
    # Start polling loop
    task = asyncio.create_task(_polling_loop())
    yield
    # Cancel polling on shutdown
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

# Canonical default values
DEFAULTS = {
    "port": 18790,
    "pipeline_state_path": "~/.openclaw/pipeline_state.json",
    "phase_state_path": "~/.openclaw/pipeline-project/phase_state.json",
    "lock_path": "~/.openclaw/pipeline.lock",
    "events_path": "~/.openclaw/pipeline_events.jsonl",
    "roadmap_path": "~/.openclaw/pipeline-project/roadmap.md",
    "project_dir_path": "~/.openclaw/pipeline-project",
    "ideas_dir": "~/.openclaw/ideas",
    "hooks_url": "http://localhost:18789/hooks/agent",
    "hooks_token": "pipeline-secret-token",
    "conversion_prompt_path": "~/.openclaw/deployment-package/Updates/PRD to Roadmap (sonnet 4.5 ideal).txt",
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
app = FastAPI(lifespan=lifespan)


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


def _extract_first_h1_heading(prd_content: str) -> str:
    """Return text from the first markdown `# ` heading line, or empty string."""
    if not prd_content:
        return ""
    for line in prd_content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


_UUID_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)


def _should_resolve_idea_name(name: str) -> bool:
    """True if stored name is empty, placeholder, or a raw UUID string."""
    n = (name or "").strip()
    if not n or n == "New Idea":
        return True
    return bool(_UUID_ID_RE.match(n))


def _atomic_write_json_file(path: Path, data: dict) -> None:
    """Write JSON atomically (tmp + replace)."""
    tmp_path = str(path) + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f)
    os.replace(tmp_path, str(path))


def _resolve_display_name_for_listing(idea_dir: Path, session_data: dict) -> tuple:
    """Return (display_name, should_persist) for idea list; never expose UUID."""
    prd_text = ""
    prd_path = idea_dir / "prd_draft.md"
    if prd_path.exists():
        prd_text = prd_path.read_text()
    if not prd_text:
        prd_text = session_data.get("prd_content") or ""

    heading = _extract_first_h1_heading(prd_text)
    if heading:
        return heading, True

    first_user = next(
        (m["content"] for m in session_data.get("messages", []) if m.get("role") == "user"),
        "",
    )
    if first_user.strip():
        return first_user.strip()[:40].title(), True

    return "Untitled Idea", True


def _extract_summary(prd_content):
    """Extract the first sentence after ## Problem Statement.

    Args:
        prd_content: Raw string content of the PRD document.

    Returns:
        The first sentence (up to first '.') after ## Problem Statement,
        stripped of leading whitespace; empty string if section is absent
        or blank.
    """
    if not prd_content:
        return ""

    lines = prd_content.split('\n')
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped == "## Problem Statement":
            in_section = True
            continue
        if in_section:
            # Skip blank lines
            if not stripped:
                continue
            # Stop if we hit another section heading
            if stripped.startswith("## "):
                return ""
            # Found a non-blank, non-section line — this is the summary
            if '.' in stripped:
                return stripped[:stripped.index('.')]
            return stripped
    return ""


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


# Whitelisted filenames under project root for user-confirmed destructive repair (switch-project).
_SWITCH_DESTRUCTIVE_WHITELIST = frozenset({"current_phase.json", "phase_state.json"})


def _expand_lock_path(config: dict) -> str | None:
    lp = config.get("lock_path")
    return os.path.expanduser(lp) if lp else None


def _clean_pipeline_state_for_project(project_real: str) -> dict:
    """Match orchestrator.py reset template when switching projects."""
    return {
        "current_phase": 0,
        "current_phase_raw_id": "",
        "current_agent": "planner",
        "planner_retries": 0,
        "executor_retries": 0,
        "reviewer_retries": 0,
        "last_action": "initialized for new project",
        "last_action_timestamp": datetime.now(timezone.utc).isoformat(),
        "pipeline_status": "RUNNING",
        "project_path": project_real,
    }


def _write_json_atomic(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def _spawn_orchestrator(project_path: str, config: dict | None = None) -> dict:
    """Start orchestrator.py with --project-path. Returns {"ok": bool, "error": str|None}."""
    import subprocess

    if config is None:
        config = load_config()
    autodev_repo_path = config.get("autodev_repo_path", "/home/pi/.openclaw")
    orchestrator_script = os.path.join(autodev_repo_path, "orchestrator.py")
    if not os.path.exists(orchestrator_script):
        return {"ok": False, "error": f"orchestrator.py not found at {orchestrator_script}"}
    log_file = open("/tmp/orchestrator.log", "a")
    subprocess.Popen(
        ["python", orchestrator_script, "--project-path", project_path],
        cwd=autodev_repo_path,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    return {"ok": True, "error": None}


def _ui_recent_projects_path() -> str:
    # Join after expanding ~ only so test patches that match ".openclaw" substrings still get a file path.
    return os.path.join(os.path.expanduser("~"), ".openclaw", "ui_recent_projects.json")


def _read_recent_projects() -> list:
    path = _ui_recent_projects_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _write_recent_projects_atomic(entries: list) -> None:
    path = _ui_recent_projects_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(entries, f, indent=2)
    os.replace(tmp, path)


def append_recent_project(repo_abs: str) -> None:
    """Record repo_abs (realpath) at the front of the recent-projects list (cap 20)."""
    repo_abs = os.path.realpath(os.path.expanduser(repo_abs))
    entries = _read_recent_projects()
    now = datetime.utcnow().isoformat() + "Z"
    entries = [e for e in entries if isinstance(e, dict) and e.get("path") != repo_abs]
    entries.insert(0, {"path": repo_abs, "last_used": now})
    entries = entries[:20]
    _write_recent_projects_atomic(entries)


def _glob_project_roadmap_paths(repo_abs: str) -> list:
    import glob as glob_mod

    return sorted(glob_mod.glob(os.path.join(repo_abs, "*oadmap*.md")))


def _recommended_keep_roadmap_basename(repo_abs: str) -> str:
    paths = _glob_project_roadmap_paths(repo_abs)
    if not paths:
        return "roadmap.md"
    basenames = {os.path.basename(p) for p in paths}
    if "roadmap.md" in basenames:
        return "roadmap.md"
    best = max(paths, key=lambda p: os.path.getmtime(p))
    return os.path.basename(best)


def _archive_extra_roadmaps(repo_abs: str, keep_basename: str) -> None:
    """Move every *oadmap*.md except keep_basename into repo_abs/autodev_archive/."""
    archive_dir = os.path.join(repo_abs, "autodev_archive")
    os.makedirs(archive_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    for p in _glob_project_roadmap_paths(repo_abs):
        base = os.path.basename(p)
        if base == keep_basename:
            continue
        dest_name = f"{ts}_{base}"
        dest = os.path.join(archive_dir, dest_name)
        while os.path.exists(dest):
            dest_name = f"{ts}_{uuid.uuid4().hex[:8]}_{base}"
            dest = os.path.join(archive_dir, dest_name)
        os.replace(p, dest)


def _canonical_roadmap_path(repo_abs: str) -> str | None:
    paths = _glob_project_roadmap_paths(repo_abs)
    if not paths:
        return None
    if len(paths) == 1:
        return paths[0]
    if os.path.exists(os.path.join(repo_abs, "roadmap.md")):
        return os.path.join(repo_abs, "roadmap.md")
    return paths[0]


def _validate_project_coherence(repo_abs: str) -> dict:
    """Return {"ok": bool, "issues": list} for roadmap vs current_phase.json."""
    rpath = _canonical_roadmap_path(repo_abs)
    if not rpath or not os.path.exists(rpath):
        return {
            "ok": False,
            "issues": [
                {
                    "kind": "missing_roadmap",
                    "detail": "No readable roadmap (*oadmap*.md) in project directory.",
                    "destructive_options": [],
                }
            ],
        }
    phases = parse_roadmap(rpath)
    phase_ids = {p["id"] for p in phases}
    cp = os.path.join(repo_abs, "current_phase.json")
    if not os.path.exists(cp):
        return {"ok": True, "issues": []}
    try:
        with open(cp) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "issues": [
                {
                    "kind": "unreadable_current_phase",
                    "detail": str(exc),
                    "destructive_options": ["current_phase.json"],
                }
            ],
        }
    raw_id = data.get("raw_id") or data.get("current_phase_raw_id")
    if not raw_id:
        return {
            "ok": False,
            "issues": [
                {
                    "kind": "current_phase_missing_raw_id",
                    "detail": "current_phase.json has no raw_id.",
                    "destructive_options": ["current_phase.json"],
                }
            ],
        }
    if raw_id not in phase_ids:
        return {
            "ok": False,
            "issues": [
                {
                    "kind": "phase_not_in_roadmap",
                    "detail": f"Phase {raw_id!r} is not in the roadmap.",
                    "destructive_options": ["current_phase.json", "phase_state.json"],
                }
            ],
        }
    return {"ok": True, "issues": []}


def _apply_destructive_project_files(repo_abs: str, names: list) -> tuple[bool, str]:
    for n in names:
        if n not in _SWITCH_DESTRUCTIVE_WHITELIST:
            return False, f"Destructive action not allowed for {n!r}"
        p = os.path.join(repo_abs, n)
        if os.path.lexists(p):
            try:
                os.remove(p)
            except OSError as exc:
                return False, str(exc)
    return True, ""


def _pipeline_allows_project_switch() -> tuple[bool, str | None]:
    """True when global pipeline_state allows switching project (STOPPED/UNKNOWN/missing)."""
    config = load_config()
    psp = config.get("pipeline_state_path")
    psp = os.path.expanduser(psp) if psp else None
    if not psp or not os.path.exists(psp):
        return True, None
    st = _read_json_file(psp)
    if not st:
        return True, None
    ps = st.get("pipeline_status")
    if ps in ("STOPPED", "UNKNOWN", None):
        return True, None
    return False, ps


def _determine_event_source(events_path):
    """Determine the event source based on whether the events file exists.
    
    Args:
        events_path: Path to the events file.
    
    Returns:
        'file' if events file exists, 'synthetic' otherwise.
    """
    return 'file' if Path(events_path).exists() else 'synthetic'


def _read_events_from_buffer(limit=30, offset=0):
    """Read events from the ring buffer in reverse chronological order.
    
    Args:
        limit: Maximum number of events to return.
        offset: Number of events to skip from the start.
    
    Returns:
        Tuple of (events list, total count).
    """
    # Convert deque to list and reverse for newest-first order
    all_events = list(_ring_buffer)[::-1]
    total = len(all_events)
    
    # Apply pagination
    events = all_events[offset:offset + limit]
    return events, total


def _read_events_from_file(events_path, limit=30, offset=0):
    """Read last N lines from JSONL file in reverse order using file seek.
    
    Args:
        events_path: Path to the JSONL events file.
        limit: Maximum number of events to return.
        offset: Number of events to skip from the start.
    
    Returns:
        Tuple of (events list, total count).
    """
    if not Path(events_path).exists():
        return [], 0
    
    # Read all valid events (skip malformed lines)
    all_events = []
    with open(events_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                all_events.append(event)
            except json.JSONDecodeError:
                # Skip malformed lines
                continue
    
    total = len(all_events)
    
    # Reverse to get newest first (assuming file is oldest-first)
    all_events = all_events[::-1]
    
    # Apply pagination
    events = all_events[offset:offset + limit]
    return events, total


@app.get("/api/events")
def get_events(limit: int = 30, offset: int = 0):
    """Get pipeline events with pagination.
    
    Returns events from either the ring buffer (synthetic) or from the
    events JSONL file, depending on which source is available.
    
    Query parameters:
        limit: Maximum number of events to return (default 30).
        offset: Number of events to skip (default 0).
    
    Returns:
        JSON with 'events' array, 'source' field ('synthetic' or 'file'),
        and 'total' count field.
    """
    config = load_config()
    events_path = config.get('events_path')
    
    if events_path:
        events_path = os.path.expanduser(events_path)
    
    # Determine source and fetch events
    if events_path and Path(events_path).exists():
        source = 'file'
        events, total = _read_events_from_file(events_path, limit, offset)
    else:
        source = 'synthetic'
        events, total = _read_events_from_buffer(limit, offset)
    
    return {
        "events": events,
        "source": source,
        "total": total
    }


import uuid
from fastapi.responses import StreamingResponse


@app.get("/api/events/stream")
async def events_stream():
    """Server-Sent Events endpoint for real-time event streaming.
    
    Returns a streaming response with content-type text/event-stream.
    Yields heartbeat events every 15 seconds and new events as they
    are added to the ring buffer or appended to the events file.
    """
    config = load_config()
    events_path = config.get('events_path')
    if events_path:
        events_path = os.path.expanduser(events_path)
    
    # Create a unique client ID
    client_id = str(uuid.uuid4())
    
    # Create a queue for this client
    client_queue = asyncio.Queue(maxsize=100)
    
    # Register this client
    _sse_clients.add(client_queue)
    # Initialize file position if using file source
    if events_path and Path(events_path).exists():
        _file_positions[client_id] = Path(events_path).stat().st_size
    
    async def event_generator():
        """Async generator that yields SSE events."""
        import time
        heartbeat_interval = 15
        use_file = events_path and Path(events_path).exists()
        last_event = None
        last_heartbeat = time.time()
        
        # Send heartbeat immediately on connect
        yield f"event: heartbeat\ndata: {{}}\n\n"
        last_heartbeat = time.time()
        
        # For file-based, use the tail function directly
        if use_file:
            file_path = Path(events_path)
            last_pos = _file_positions.get(client_id, file_path.stat().st_size if file_path.exists() else 0)
            
            while True:
                current_time = time.time()
                
                # Check for new file content
                if file_path.exists():
                    try:
                        current_size = file_path.stat().st_size
                        
                        if current_size > last_pos:
                            with open(file_path, 'r') as f:
                                f.seek(last_pos)
                                new_content = f.read()
                                last_pos = current_size
                                _file_positions[client_id] = last_pos
                            
                            for line in new_content.strip().split('\n'):
                                if line.strip():
                                    try:
                                        event_data = json.loads(line)
                                        yield f"data: {json.dumps(event_data)}\n\n"
                                    except json.JSONDecodeError:
                                        pass
                    except Exception:
                        pass
                
                # Send heartbeat if needed
                if current_time - last_heartbeat >= heartbeat_interval:
                    yield f"event: heartbeat\ndata: {{}}\n\n"
                    last_heartbeat = current_time
                
                # Small sleep to avoid busy loop
                await asyncio.sleep(0.5)
        else:
            # Ring buffer based - use queue
            while True:
                current_time = time.time()
                
                # Send heartbeat if needed
                if current_time - last_heartbeat >= heartbeat_interval:
                    yield f"event: heartbeat\ndata: {{}}\n\n"
                    last_heartbeat = current_time
                
                # Check for new events in queue (non-blocking)
                try:
                    while True:
                        try:
                            event = client_queue.get_nowait()
                            if event != last_event:
                                last_event = event
                                yield f"data: {json.dumps(event)}\n\n"
                        except asyncio.QueueEmpty:
                            break
                except Exception:
                    pass
                
                # Small sleep to avoid busy loop
                await asyncio.sleep(0.5)
    
    async def cleanup_wrapper():
        """Wrapper to handle cleanup properly."""
        try:
            async for item in event_generator():
                yield item
        finally:
            # Unregister client
            _sse_clients.discard(client_queue)
            if client_id in _file_positions:
                del _file_positions[client_id]
    
    return StreamingResponse(
        cleanup_wrapper(),
        media_type="text/event-stream"
    )


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
    
    # Extract counters from pipeline_state (counters sub-dict kept for compat)
    counters = pipeline_state.get("counters", {}) if pipeline_state else {}
    
    # Build response with defaults
    if pipeline_state:
        response = {
            "pipeline_status": pipeline_state.get("pipeline_status", "UNKNOWN"),
            "current_phase": pipeline_state.get("current_phase"),
            "current_phase_raw_id": pipeline_state.get("current_phase_raw_id", ""),
            "current_agent": pipeline_state.get("current_agent", ""),
            "project_path": pipeline_state.get("project_path", ""),
            "last_action_timestamp": pipeline_state.get("last_action_timestamp"),
            "counters": counters,
            # Retry counters are top-level fields in pipeline_state.json written by orchestrator.py
            "planner_retries": pipeline_state.get("planner_retries", 0),
            "executor_retries": pipeline_state.get("executor_retries", 0),
            "reviewer_retries": pipeline_state.get("reviewer_retries", 0),
        }
    else:
        response = {
            "pipeline_status": "UNKNOWN",
            "current_phase": None,
            "counters": {"success": 0, "failure": 0, "retry": 0},
            "planner_retries": 0,
            "executor_retries": 0,
            "reviewer_retries": 0,
        }
    
    # Read phase state and conditionally add fields
    if phase_state_path:
        phase_state = _read_json_file(phase_state_path)
        if phase_state:
            if "last_error_code" in phase_state:
                response["last_error_code"] = phase_state["last_error_code"]
            if "escalation_resets" in phase_state:
                response["escalation_resets"] = phase_state["escalation_resets"]
            if "last_action_timestamp" in phase_state:
                response["last_action_timestamp"] = phase_state["last_action_timestamp"]
            if "skill_injected" in phase_state:
                response["skill_injected"] = phase_state["skill_injected"]
            if "skill_agent" in phase_state:
                response["skill_agent"] = phase_state["skill_agent"]
            if "escalation_trigger_reason" in phase_state:
                response["escalation_trigger_reason"] = phase_state["escalation_trigger_reason"]
    
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

    # Project path — resolve symlink for display (last two segments shown in header)
    _symlink_path = config.get("project_dir_path") or config.get("symlink_target") or config.get("project_dir")
    if _symlink_path:
        _symlink_path = os.path.expanduser(_symlink_path)
        try:
            response["project_path"] = os.path.realpath(_symlink_path)
        except Exception:
            response["project_path"] = _symlink_path

    return response


# Valid commands for escalation
VALID_COMMANDS = {"RETRY", "RESET_EXECUTION", "RESET_PHASE", "SKIP", "PROCEED", "STOP"}


RESET_CAP_COMMANDS = {"RESET_PHASE", "RESET_EXECUTION"}


def _validate_command_request(project_dir_path, pipeline_status, escalation_resets, command):
    """Validate command request conditions.

    Args:
        project_dir_path: Path to the project directory.
        pipeline_status: Current pipeline status.
        escalation_resets: Number of escalation resets.
        command: The command being issued.

    Returns:
        Tuple of (is_valid, error_message, error_status_code).
        If valid, returns (True, None, None).
    """
    # Check symlink validity (dangling or absent)
    project_path = Path(project_dir_path)

    # Check if it's a symlink first
    if project_path.is_symlink():
        if not project_path.resolve().exists():
            return False, "Project directory symlink is dangling", 503
    elif not project_path.exists():
        # Not a symlink, just doesn't exist
        return False, "Project directory not found", 503

    # Check pipeline status
    if pipeline_status != "WAITING_FOR_HUMAN":
        return False, "Pipeline is not waiting for human input", 409

    # Check reset cap — only applies to RESET_PHASE and RESET_EXECUTION
    if command in RESET_CAP_COMMANDS and escalation_resets >= 3:
        return False, "Reset cap reached", 409

    return True, None, None


def _write_escalation_files(project_dir_path, command):
    """Write escalation output files atomically under the resolved project root.

    Uses realpath so writes land in the symlink target when project_dir_path is a symlink.
    """
    root = os.path.realpath(os.path.expanduser(str(project_dir_path)))
    project_path = Path(root)
    json_path = project_path / "escalation_output.json"
    done_path = project_path / "escalation_output.done"
    
    # Write JSON file first
    data = {
        "command": command,
        "source": "ui",
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    
    with open(json_path, 'w') as f:
        json.dump(data, f)
    
    # Then write done file
    with open(done_path, 'w') as f:
        f.write("")
    
    return True


@app.post("/api/command")
def post_command(request: dict):
    """Handle escalation commands from the UI.
    
    Request body:
        command: One of RETRY, RESET_EXECUTION, RESET_PHASE, SKIP, PROCEED, STOP
    
    Returns:
        200 on success with confirmation message.
        400 for unknown commands.
        409 if pipeline is not waiting for human input or reset cap reached.
        503 if project directory is missing or symlink is dangling.
    """
    command = request.get("command")
    
    # Validate command is in whitelist
    if command not in VALID_COMMANDS:
        raise HTTPException(status_code=400, detail=f"Unknown command: {command}")
    
    config = load_config()
    project_dir_path = config.get("project_dir_path")
    pipeline_state_path = config.get("pipeline_state_path")
    phase_state_path = config.get("phase_state_path")
    
    # Expand paths
    project_dir_path = os.path.expanduser(project_dir_path) if project_dir_path else None
    pipeline_state_path = os.path.expanduser(pipeline_state_path) if pipeline_state_path else None
    phase_state_path = os.path.expanduser(phase_state_path) if phase_state_path else None
    
    # Read pipeline and phase state
    pipeline_state = _read_json_file(pipeline_state_path) if pipeline_state_path else {}
    phase_state = _read_json_file(phase_state_path) if phase_state_path else {}
    
    pipeline_status = pipeline_state.get("pipeline_status") if pipeline_state else None
    escalation_resets = phase_state.get("escalation_resets", 0) if phase_state else 0
    
    # Validate request
    is_valid, error_msg, error_code = _validate_command_request(
        project_dir_path, pipeline_status, escalation_resets, command
    )
    
    if not is_valid:
        raise HTTPException(status_code=error_code, detail=error_msg)
    
    # Write escalation files
    _write_escalation_files(project_dir_path, command)
    
    return {"status": "ok", "command": command}


@app.post("/api/resume-ready")
def post_resume_ready():
    """Transition pipeline from STOPPED to WAITING_FOR_HUMAN so /api/command can be used.

    Reads pipeline_state.json, confirms pipeline_status is STOPPED, then atomically
    writes pipeline_status: WAITING_FOR_HUMAN (all other fields preserved).
    Returns 409 if pipeline is not in STOPPED state.
    """
    config = load_config()
    pipeline_state_path = config.get("pipeline_state_path")
    pipeline_state_path = os.path.expanduser(pipeline_state_path) if pipeline_state_path else None

    if not pipeline_state_path:
        raise HTTPException(status_code=503, detail="Pipeline state path not configured")

    pipeline_state = _read_json_file(pipeline_state_path)
    if not pipeline_state:
        raise HTTPException(status_code=503, detail="Could not read pipeline_state.json")

    if pipeline_state.get("pipeline_status") != "STOPPED":
        raise HTTPException(
            status_code=409,
            detail=f"Pipeline is not in STOPPED state (current: {pipeline_state.get('pipeline_status')})"
        )

    pipeline_state["pipeline_status"] = "WAITING_FOR_HUMAN"
    # Ensure the orchestrator hits the escalation command handler (WAITING_FOR_HUMAN branch)
    # regardless of what current_agent was when the pipeline stopped.
    pipeline_state["current_agent"] = "escalation"

    tmp_path = pipeline_state_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(pipeline_state, f, indent=2)
    os.replace(tmp_path, pipeline_state_path)

    return {"ok": True}


@app.post("/api/resume-orchestrator")
def post_resume_orchestrator():
    """Spawn the orchestrator process as a non-blocking subprocess.

    Reads project_path from pipeline_state.json and autodev_repo_path from config.
    If pipeline_state.project_path disagrees with the pipeline-project symlink target,
    uses the symlink realpath so resume targets the active project directory.
    Returns 200 immediately without waiting for the orchestrator to start.
    """
    config = load_config()
    pipeline_state_path = config.get("pipeline_state_path")
    pipeline_state_path = os.path.expanduser(pipeline_state_path) if pipeline_state_path else None

    pipeline_state = _read_json_file(pipeline_state_path) if pipeline_state_path else {}
    project_path = pipeline_state.get("project_path") if pipeline_state else None

    project_dir = config.get("project_dir_path") or config.get("symlink_target") or config.get("project_dir")
    project_dir = os.path.expanduser(project_dir) if project_dir else None
    symlink_real = None
    if project_dir:
        try:
            symlink_real = os.path.realpath(project_dir)
        except OSError:
            symlink_real = None

    if symlink_real and project_path:
        try:
            state_real = os.path.realpath(os.path.expanduser(str(project_path)))
        except OSError:
            state_real = str(project_path)
        if state_real != symlink_real:
            project_path = symlink_real
    elif symlink_real and not project_path:
        project_path = symlink_real

    if not project_path:
        raise HTTPException(status_code=503, detail="No project_path in pipeline_state.json")

    spawned = _spawn_orchestrator(project_path, config)
    if not spawned.get("ok"):
        raise HTTPException(status_code=503, detail=spawned.get("error") or "Failed to spawn orchestrator")

    return {"ok": True}


@app.get("/api/roadmap")
def get_roadmap():
    """Get the parsed roadmap with in-progress phase identified.
    
    Returns a JSON array of phase objects with id, goal, status, and exit_criteria.
    If pipeline_state.json contains current_phase_raw_id, the matching phase's status
    is overridden to 'in_progress' (taking precedence over checkbox status).
    Returns [] when roadmap_path is absent or file is empty.
    """
    config = load_config()
    
    roadmap_path = config.get('roadmap_path')
    pipeline_state_path = config.get('pipeline_state_path')
    
    # Expand paths if not already expanded
    roadmap_path = os.path.expanduser(roadmap_path) if roadmap_path else None
    pipeline_state_path = os.path.expanduser(pipeline_state_path) if pipeline_state_path else None
    
    # Parse roadmap - returns [] if absent or empty
    phases = parse_roadmap(roadmap_path) if roadmap_path else []
    
    if not phases:
        return []
    
    # Read pipeline_state to get current_phase_raw_id
    current_phase_raw_id = None
    if pipeline_state_path:
        pipeline_state = _read_json_file(pipeline_state_path)
        if pipeline_state:
            current_phase_raw_id = pipeline_state.get('current_phase_raw_id')
    
    # Override status to 'in_progress' for matching phase only when pipeline is
    # actively running — not when it has reached a terminal state like PIPELINE_COMPLETE.
    terminal_statuses = {"PIPELINE_COMPLETE", "HALTED_SILENT", "BLOCKED"}
    pipeline_status = pipeline_state.get("pipeline_status", "") if pipeline_state_path and _read_json_file(pipeline_state_path) else ""
    if pipeline_state_path:
        _ps = _read_json_file(pipeline_state_path)
        pipeline_status = _ps.get("pipeline_status", "") if _ps else ""
    if current_phase_raw_id and pipeline_status not in terminal_statuses:
        for phase in phases:
            if phase['id'] == current_phase_raw_id:
                phase['status'] = 'in_progress'
                break
    
    return phases


def _empty_metrics_summary():
    """Return a zero-valued metrics summary dict."""
    return {
        "total_phases": 0,
        "total_duration_seconds": 0,
        "total_executor_attempts": 0,
        "total_reviewer_passes": 0,
        "total_blame_fires": 0,
        "total_escalations": 0,
        "phases": []
    }


@app.get("/api/metrics-summary")
def get_metrics_summary():
    """Return aggregated run metrics from metrics.jsonl in the project directory.

    Reads {project_dir_path}/metrics.jsonl. Deduplicates by phase (keeps last row
    per phase, so cumulative attempt counts are correct even if a phase was reset
    and re-run). Returns sensible zeros if the file is absent or empty.
    """
    config = load_config()
    project_dir_path = config.get("project_dir_path")
    if not project_dir_path:
        return _empty_metrics_summary()

    metrics_path = Path(project_dir_path) / "metrics.jsonl"
    if not metrics_path.exists():
        return _empty_metrics_summary()

    rows = []
    try:
        with open(metrics_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return _empty_metrics_summary()

    if not rows:
        return _empty_metrics_summary()

    # Deduplicate by phase — keep the last occurrence (highest attempt counts)
    seen: dict = {}
    for row in rows:
        phase = row.get("phase")
        if phase:
            seen[phase] = row

    phases = list(seen.values())

    total_duration = sum((p.get("duration_seconds") or 0) for p in phases)
    total_executor = sum((p.get("executor_attempts") or 0) for p in phases)
    total_reviewer = sum((p.get("reviewer_passes") or 0) for p in phases)
    total_blame = sum((p.get("blame_fires") or 0) for p in phases)
    total_escalations = sum((p.get("escalations") or 0) for p in phases)

    return {
        "total_phases": len(phases),
        "total_duration_seconds": total_duration,
        "total_executor_attempts": total_executor,
        "total_reviewer_passes": total_reviewer,
        "total_blame_fires": total_blame,
        "total_escalations": total_escalations,
        "phases": [
            {
                "phase": p.get("phase"),
                "goal": p.get("goal"),
                "duration_seconds": p.get("duration_seconds"),
                "executor_attempts": p.get("executor_attempts", 0),
                "reviewer_passes": p.get("reviewer_passes", 0),
                "blame_fires": p.get("blame_fires", 0),
                "escalations": p.get("escalations", 0),
                "skill_used": p.get("skill_used"),
            }
            for p in phases
        ],
    }


POLL_TIMEOUT = 120  # seconds; patchable in tests
POLL_INTERVAL = 2   # seconds between sentinel checks


def _parse_agent_response(content: str) -> dict:
    """Parse agent response content into structured components.

    QUESTIONS block: accepts ``QUESTIONS`` or ``QUESTIONS:``; supports ``[SINGLE]``/``[MULTI]``,
    numbered questions (``1. ...``), implicit question lines, and ``- `` / ``* `` options.
    """
    lines = content.splitlines()
    drafting = None
    assumptions: list[str] = []
    questions: list[dict] = []
    prose_lines: list[str] = []

    start_idx = 0
    if lines and lines[0].startswith("DRAFTING:"):
        drafting = lines[0][len("DRAFTING:"):].strip()
        start_idx = 1

    in_questions_block = False
    current_question: dict | None = None
    i = start_idx

    def _flush_question() -> None:
        nonlocal current_question
        if current_question is not None:
            questions.append(current_question)
            current_question = None

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        qhead = stripped.upper()

        if qhead == "QUESTIONS" or qhead.startswith("QUESTIONS:"):
            in_questions_block = True
            _flush_question()
            i += 1
            continue

        if in_questions_block:
            if stripped.startswith("[SINGLE]") or stripped.startswith("[MULTI]"):
                _flush_question()
                qtype = "single" if stripped.startswith("[SINGLE]") else "multi"
                rest = stripped[len("[SINGLE]") :].strip() if qtype == "single" else stripped[len("[MULTI]") :].strip()
                current_question = {"type": qtype, "text": rest, "options": []}
            elif re.match(r"^\d+[\.\)]\s+", stripped):
                _flush_question()
                qtext = re.sub(r"^\d+[\.\)]\s+", "", stripped).strip()
                current_question = {"type": "single", "text": qtext, "options": []}
            elif stripped.startswith("- ") and current_question is not None:
                current_question["options"].append(stripped[2:].strip())
            elif stripped.startswith("* ") and current_question is not None:
                current_question["options"].append(stripped[2:].strip())
            elif stripped == "":
                pass
            elif current_question is None and stripped and not stripped.startswith("["):
                # Implicit first question (plain line after QUESTIONS:)
                current_question = {"type": "single", "text": stripped, "options": []}
            else:
                _flush_question()
                in_questions_block = False
                if line.startswith("ASSUMPTION:"):
                    assumptions.append(line[len("ASSUMPTION:"):].strip())
                else:
                    prose_lines.append(line)
            i += 1
            continue

        if line.startswith("ASSUMPTION:"):
            assumptions.append(line[len("ASSUMPTION:"):].strip())
            i += 1
            continue

        prose_lines.append(line)
        i += 1

    _flush_question()

    prose = "\n".join(prose_lines).strip()
    return {
        "prose": prose,
        "drafting": drafting,
        "assumptions": assumptions,
        "questions": questions,
    }


def _default_idea_session(name: str = "") -> dict:
    return {
        "name": name,
        "messages": [],
        "prd_content": "",
        "roadmap_content": "",
        "created": None,
        "updated": None,
    }


def _iso_from_mtime(path: Path) -> str:
    return datetime.utcfromtimestamp(path.stat().st_mtime).isoformat() + "Z"


def _rehydrate_session_from_artifacts(idea_dir: Path, session_data: dict) -> tuple[dict, bool]:
    """Backfill empty session.json from turns/*.md and prd_draft.md if available."""
    if not isinstance(session_data, dict):
        session_data = _default_idea_session()
    else:
        session_data.setdefault("name", "")
        session_data.setdefault("messages", [])
        session_data.setdefault("prd_content", "")
        session_data.setdefault("roadmap_content", "")
        session_data.setdefault("created", None)
        session_data.setdefault("updated", None)

    has_messages = bool(session_data.get("messages"))
    has_prd = bool((session_data.get("prd_content") or "").strip())
    if has_messages and has_prd:
        return session_data, False

    turns_dir = idea_dir / "turns"
    md_turns = []
    if turns_dir.exists():
        for md_file in turns_dir.glob("*.md"):
            try:
                turn_num = int(md_file.stem)
            except ValueError:
                continue
            md_turns.append((turn_num, md_file))
    md_turns.sort(key=lambda x: x[0])

    changed = False
    if not has_messages and md_turns:
        rebuilt_messages = []
        for _turn_num, md_file in md_turns:
            rebuilt_messages.append(
                {
                    "role": "assistant",
                    "content": md_file.read_text(),
                    "ts": _iso_from_mtime(md_file),
                }
            )
        session_data["messages"] = rebuilt_messages
        if not session_data.get("created"):
            session_data["created"] = rebuilt_messages[0]["ts"]
        changed = True

    if not has_prd:
        prd_path = idea_dir / "prd_draft.md"
        if prd_path.exists():
            session_data["prd_content"] = prd_path.read_text()
            changed = True

    if changed:
        ts_candidates = []
        if session_data.get("messages"):
            ts_candidates.extend([m.get("ts") for m in session_data["messages"] if m.get("ts")])
        if (idea_dir / "prd_draft.md").exists():
            ts_candidates.append(_iso_from_mtime(idea_dir / "prd_draft.md"))
        latest_ts = max(ts_candidates) if ts_candidates else datetime.utcnow().isoformat() + "Z"
        if not session_data.get("updated") or str(session_data.get("updated")) < latest_ts:
            session_data["updated"] = latest_ts

    return session_data, changed


def _enrich_assistant_messages_with_parsed(session_data: dict) -> None:
    """Ensure assistant messages carry parsed QUESTIONS/assumptions for UI reload."""
    for m in session_data.get("messages") or []:
        if m.get("role") != "assistant":
            continue
        if m.get("parsed") is not None:
            continue
        content = m.get("content")
        if not content or not isinstance(content, str):
            continue
        m["parsed"] = _parse_agent_response(content)


@app.get("/api/ideas/{idea_id}/session")
def get_ideas_session(idea_id: str):
    """Return the full session.json for an idea, or empty schema if not found."""
    config = load_config()
    ideas_dir = os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas"))
    idea_dir = Path(ideas_dir) / idea_id
    session_path = idea_dir / "session.json"

    if not session_path.exists():
        return _default_idea_session()

    session_data = _read_json_file(str(session_path))
    if session_data is None:
        return _default_idea_session()
    session_data, changed = _rehydrate_session_from_artifacts(idea_dir, session_data)
    if changed:
        _atomic_write_json_file(session_path, session_data)
    _enrich_assistant_messages_with_parsed(session_data)
    return session_data


async def _trigger_readiness_assessment(idea_id: str, config: dict) -> None:
    """Fire non-blocking readiness webhook; deletes prior readiness.done first."""
    _active_readiness_jobs.add(idea_id)
    _readiness_job_started_at[idea_id] = datetime.utcnow().timestamp()
    logger.info(f"[READINESS] Triggering assessment for idea {idea_id}")
    try:
        ideas_dir = Path(os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas")))
        sentinel = ideas_dir / idea_id / "readiness.done"
        sentinel.unlink(missing_ok=True)
        hooks_url = config.get("hooks_url", "http://localhost:18789/hooks/agent")
        hooks_token = config.get("hooks_token", "")
        payload = {
            "agentId": "prd-creator",
            "sessionKey": f"ideas:{idea_id}:readiness",
            "wakeMode": "now",
            "message": (
                f"[SESSION] ideas:{idea_id}:readiness\n\n"
                f"A new PRD draft is available. Read "
                f"~/.openclaw/ideas/{idea_id}/prd_draft.md and produce an "
                f"updated readiness assessment. Apply the readiness-reviewer "
                f"skill. Write readiness.json then readiness.done as specified."
            ),
        }
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                hooks_url,
                json=payload,
                headers={"Authorization": f"Bearer {hooks_token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            )
            logger.info(f"[READINESS] Webhook sent for {idea_id}, response: {resp.status}")
    except Exception as exc:
        if isinstance(exc, asyncio.TimeoutError):
            logger.warning(f"[READINESS] Assessment timed out for {idea_id}")
        else:
            logger.error(f"[READINESS] Webhook failed for {idea_id}: {exc}")
    finally:
        _active_readiness_jobs.discard(idea_id)


@app.post("/api/ideas/{idea_id}/message")
async def post_ideas_message(idea_id: str, request: Request):
    """POST a user message to the ideas agent, poll for sentinel, update session."""
    config = load_config()
    body = await request.json()
    content = body.get("content")
    turn_n = body.get("turn")
    attachment = body.get("attachment")  # optional: {"filename": str, "content": str}

    if not content or turn_n is None:
        raise HTTPException(status_code=422, detail="Body must contain {content: str, turn: int}")

    ideas_dir = os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas"))
    hooks_url = config.get("hooks_url", "http://localhost:18789/hooks/agent")
    hooks_token = config.get("hooks_token", "")

    # Prepend attachment context when provided
    message_content = content
    if attachment and isinstance(attachment, dict):
        fname = attachment.get("filename", "attachment.md")
        fcontent = attachment.get("content", "")
        message_content = f"[ATTACHMENT: {fname}]\n{fcontent}\n[/ATTACHMENT]\n\n{content}"

    # Inject unsubmitted annotations into message context
    idea_dir = Path(ideas_dir) / idea_id
    session_path_pre = idea_dir / "session.json"
    pending_annotation_ids: list[str] = []
    if session_path_pre.exists():
        pre_session = _read_json_file(str(session_path_pre)) or {}
        unsubmitted = [a for a in pre_session.get("annotations", []) if not a.get("submitted")]
        if unsubmitted:
            ann_lines = "\n".join(f'Section "{a["section"]}": "{a["comment"]}"' for a in unsubmitted)
            message_content = f"[USER ANNOTATIONS]\n{ann_lines}\n[/USER ANNOTATIONS]\n\n{message_content}"
            pending_annotation_ids = [a["id"] for a in unsubmitted]

    # Build session key: ideas:{id}:session-{n}
    session_key = f"ideas:{idea_id}:session-{turn_n}"

    # Webhook payload — first line MUST be [SESSION] for agent output path parsing (AGENTS.md)
    webhook_payload = {
        "agentId": "prd-creator",
        "sessionKey": session_key,
        "wakeMode": "now",
        "message": f"[SESSION] ideas:{idea_id}:session-{turn_n}\n\n{message_content}",
    }

    # Send webhook POST via a per-request aiohttp session
    headers = {"Authorization": f"Bearer {hooks_token}"}
    async with aiohttp.ClientSession() as session:
        await session.post(hooks_url, json=webhook_payload, headers=headers)
    turns_dir = idea_dir / "turns"
    # Sentinel paths per ~/.openclaw/workspace-prd-creator/AGENTS.md: turns/{n}.md / turns/{n}.done
    done_path = turns_dir / f"{turn_n}.done"
    md_path = turns_dir / f"{turn_n}.md"
    prd_draft_path = idea_dir / "prd_draft.md"

    deadline = datetime.utcnow().timestamp() + POLL_TIMEOUT
    while datetime.utcnow().timestamp() < deadline:
        if done_path.exists():
            break
        await asyncio.sleep(POLL_INTERVAL)
    else:
        # Timed out
        raise HTTPException(status_code=408, detail=f"Agent turn timed out after {POLL_TIMEOUT}s")

    # Read agent response
    agent_response = ""
    if md_path.exists():
        agent_response = md_path.read_text()

    # Read updated prd_content from prd_draft.md
    prd_content = ""
    if prd_draft_path.exists():
        prd_content = prd_draft_path.read_text()

    # Load existing session.json
    session_path = idea_dir / "session.json"
    if session_path.exists():
        session_data = _read_json_file(str(session_path)) or {
            "messages": [], "prd_content": "", "created": None, "updated": None
        }
    else:
        session_data = {"messages": [], "prd_content": "", "created": None, "updated": None}

    parsed = _parse_agent_response(agent_response)

    # Append user and assistant messages (assistant carries parsed for QuestionFlow on reload)
    now = datetime.utcnow().isoformat() + "Z"
    session_data.setdefault("messages", [])
    session_data["messages"].append({"role": "user", "content": content, "ts": now})
    session_data["messages"].append(
        {"role": "assistant", "content": agent_response, "ts": now, "parsed": parsed}
    )
    session_data["prd_content"] = prd_content
    session_data["updated"] = now
    if session_data.get("created") is None:
        session_data["created"] = now

    # Mark submitted annotations
    if pending_annotation_ids:
        for ann in session_data.get("annotations", []):
            if ann.get("id") in pending_annotation_ids:
                ann["submitted"] = True

    # Auto-name from first # heading in prd_draft (only while still "New Idea" or empty)
    nm = session_data.get("name", "")
    if nm in ("", "New Idea"):
        heading_name = _extract_first_h1_heading(prd_content)
        if heading_name:
            session_data["name"] = heading_name
        else:
            # Fallback: first 40 chars of user's first message, title-cased
            first_user = next(
                (m["content"] for m in session_data.get("messages", []) if m.get("role") == "user"),
                "",
            )
            if first_user.strip():
                session_data["name"] = first_user.strip()[:40].title()

    # Atomic write via .tmp + os.replace
    tmp_path = str(session_path) + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(session_data, f)
    os.replace(tmp_path, session_path)

    _readiness_job_started_at[idea_id] = datetime.utcnow().timestamp()
    asyncio.create_task(_trigger_readiness_assessment(idea_id, config))

    return {"response": agent_response, "prd_content": prd_content, "parsed": parsed}


# ---------------------------------------------------------------------------
# Annotations endpoints (Phase 4)
# ---------------------------------------------------------------------------

def _load_session_for_idea(idea_dir: Path) -> dict:
    """Load session.json for an idea, returning empty schema on missing file."""
    session_path = idea_dir / "session.json"
    if not session_path.exists():
        return {"messages": [], "prd_content": "", "annotations": [], "created": None, "updated": None}
    data = _read_json_file(str(session_path)) or {}
    data.setdefault("annotations", [])
    return data


def _save_session_for_idea(idea_dir: Path, session_data: dict) -> None:
    """Atomic write of session.json."""
    session_path = idea_dir / "session.json"
    session_data.setdefault("annotations", [])
    tmp_path = str(session_path) + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(session_data, f)
    os.replace(tmp_path, session_path)


@app.post("/api/ideas/{idea_id}/annotations")
async def post_idea_annotation(idea_id: str, request: Request):
    """Create a new annotation for a PRD section.

    Body: {"section": str, "comment": str}
    Returns: {"id": uuid}
    """
    config = load_config()
    ideas_dir = os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas"))
    idea_dir = Path(ideas_dir) / idea_id
    if not idea_dir.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    body = await request.json()
    section = body.get("section", "").strip()
    comment = body.get("comment", "").strip()
    if not section or not comment:
        raise HTTPException(status_code=422, detail="Body must contain {section: str, comment: str}")

    annotation_id = str(uuid.uuid4())
    annotation = {
        "id": annotation_id,
        "section": section,
        "comment": comment,
        "ts": datetime.utcnow().isoformat() + "Z",
        "submitted": False,
    }

    session_data = _load_session_for_idea(idea_dir)
    session_data["annotations"].append(annotation)
    _save_session_for_idea(idea_dir, session_data)

    return {"id": annotation_id}


@app.patch("/api/ideas/{idea_id}/annotations/{annotation_id}")
async def patch_idea_annotation(idea_id: str, annotation_id: str, request: Request):
    """Update annotation comment text if not yet submitted.

    Body: {"comment": str}
    Returns 409 if annotation is already submitted.
    """
    config = load_config()
    ideas_dir = os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas"))
    idea_dir = Path(ideas_dir) / idea_id
    if not idea_dir.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    body = await request.json()
    new_comment = body.get("comment", "").strip()
    if not new_comment:
        raise HTTPException(status_code=422, detail="Body must contain {comment: str}")

    session_data = _load_session_for_idea(idea_dir)
    for ann in session_data.get("annotations", []):
        if ann.get("id") == annotation_id:
            if ann.get("submitted"):
                raise HTTPException(status_code=409, detail="Annotation already submitted and cannot be edited")
            ann["comment"] = new_comment
            _save_session_for_idea(idea_dir, session_data)
            return {"ok": True}

    raise HTTPException(status_code=404, detail="Annotation not found")


@app.delete("/api/ideas/{idea_id}/annotations/{annotation_id}")
def delete_idea_annotation(idea_id: str, annotation_id: str):
    """Delete an annotation by id.

    Returns 404 if annotation not found.
    """
    config = load_config()
    ideas_dir = os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas"))
    idea_dir = Path(ideas_dir) / idea_id
    if not idea_dir.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    session_data = _load_session_for_idea(idea_dir)
    annotations = session_data.get("annotations", [])
    new_annotations = [a for a in annotations if a.get("id") != annotation_id]
    if len(new_annotations) == len(annotations):
        raise HTTPException(status_code=404, detail="Annotation not found")

    session_data["annotations"] = new_annotations
    _save_session_for_idea(idea_dir, session_data)
    return {"ok": True}


@app.get("/api/ideas/{idea_id}/annotations")
def get_idea_annotations(idea_id: str):
    """Return all annotations for an idea.

    Returns [] if idea has no annotations.
    """
    config = load_config()
    ideas_dir = os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas"))
    idea_dir = Path(ideas_dir) / idea_id
    if not idea_dir.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    session_data = _load_session_for_idea(idea_dir)
    return session_data.get("annotations", [])


@app.get("/api/ideas")
def get_ideas():
    """List all idea documents.

    Returns:
        JSON array of {id, name, summary, updated} objects, sorted newest-first.
        Returns [] if ideas_dir is absent or empty.
    """
    import shutil
    config = load_config()
    ideas_dir = os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas"))
    ideas_path = Path(ideas_dir)

    if not ideas_path.exists():
        return []

    ideas = []
    for subdir in ideas_path.iterdir():
        if not subdir.is_dir():
            continue
        # Only list ideas that have completed at least one agent turn (UI-E3 contract)
        first_done = subdir / "turns" / "1.done"
        if not first_done.exists():
            continue
        session_path = subdir / "session.json"
        session_data = _read_json_file(str(session_path)) if session_path.exists() else _default_idea_session()
        session_data, changed = _rehydrate_session_from_artifacts(subdir, session_data)
        if changed:
            _atomic_write_json_file(session_path, session_data)
        prd_content = session_data.get("prd_content", "") or ""

        raw_name = (session_data.get("name") or "").strip()
        if _should_resolve_idea_name(raw_name):
            name, _ = _resolve_display_name_for_listing(subdir, session_data)
            if session_path.exists():
                session_data["name"] = name
                _atomic_write_json_file(session_path, session_data)
        else:
            name = raw_name

        summary = _extract_summary(prd_content)
        updated = session_data.get("updated", "")

        ideas.append({
            "id": subdir.name,
            "name": name,
            "summary": summary,
            "updated": updated,
        })

    # Sort newest first by updated timestamp
    ideas.sort(key=lambda x: x.get("updated") or "", reverse=True)
    return ideas


@app.post("/api/ideas")
def post_ideas():
    """Create a new idea document.

    Creates {ideas_dir}/{uuid}/session.json with empty schema.
    Returns {"id": <uuid>}.
    """
    config = load_config()
    ideas_dir = os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas"))
    ideas_path = Path(ideas_dir)
    ideas_path.mkdir(parents=True, exist_ok=True)

    idea_id = str(uuid.uuid4())
    idea_dir = ideas_path / idea_id
    idea_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.utcnow().isoformat() + "Z"
    session_data = {
        "name": "New Idea",
        "messages": [],
        "prd_content": "",
        "roadmap_content": "",
        "created": now,
        "updated": now,
    }

    session_path = idea_dir / "session.json"
    tmp_path = str(session_path) + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(session_data, f)
    os.replace(tmp_path, session_path)

    return {"id": idea_id}


@app.delete("/api/ideas/{idea_id}")
def delete_ideas(idea_id: str):
    """Delete an idea document and all its contents.

    Returns 404 if the idea directory does not exist.
    """
    import shutil
    config = load_config()
    ideas_dir = os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas"))
    idea_path = Path(ideas_dir) / idea_id

    if not idea_path.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    shutil.rmtree(idea_path)
    return {"ok": True}


@app.get("/api/ideas/{idea_id}/download")
def download_ideas(idea_id: str):
    """Download an idea's prd_content as a markdown file.

    Filename is derived from the first # heading in prd_content,
    or falls back to the idea id. Suffix is always "-prd.md".
    Returns 404 if the idea is not found.
    """
    config = load_config()
    ideas_dir = os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas"))
    session_path = Path(ideas_dir) / idea_id / "session.json"

    if not session_path.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    session_data = _read_json_file(str(session_path)) or {}
    prd_content = session_data.get("prd_content", "") or ""

    # Derive filename from first # heading or fall back to id
    filename = idea_id
    for line in prd_content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            heading = stripped[2:].strip()
            # Sanitize: replace spaces with hyphens, keep alphanum/dash/underscore
            filename = heading.replace(" ", "-")
            break

    filename = filename + "-prd.md"

    from fastapi.responses import Response
    return Response(
        content=prd_content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/ideas/{idea_id}/upload")
async def post_ideas_upload(
    idea_id: str,
    file: UploadFile = File(...),
):
    """Upload any .md file, write uploaded_seed.md, trigger synthesis webhook, poll sentinel.

    Does not reject for template/format — the agent synthesizes into the canonical PRD structure.
    """
    filename = file.filename or ""
    if not filename.lower().endswith(".md"):
        raise HTTPException(
            status_code=400,
            detail="Only .md files are accepted",
        )

    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be valid UTF-8 text")

    if not text.strip():
        raise HTTPException(status_code=400, detail="File must be non-empty")

    config = load_config()
    ideas_dir = os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas"))
    hooks_url = config.get("hooks_url", "http://localhost:18789/hooks/agent")
    hooks_token = config.get("hooks_token", "")
    idea_dir = Path(ideas_dir) / idea_id

    if not idea_dir.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    # Atomic write: uploaded_seed.md
    seed_path = idea_dir / "uploaded_seed.md"
    tmp_seed = str(seed_path) + ".tmp"
    with open(tmp_seed, "w") as f:
        f.write(text)
    os.replace(tmp_seed, seed_path)

    # Next available turn index for sentinel polling (avoid colliding with chat turns)
    turns_dir = idea_dir / "turns"
    turns_dir.mkdir(parents=True, exist_ok=True)
    upload_turn = 1
    while (turns_dir / f"{upload_turn}.done").exists():
        upload_turn += 1

    session_key = f"ideas:{idea_id}:upload-{upload_turn}"
    webhook_payload = {
        "agentId": "prd-creator",
        "sessionKey": session_key,
        "wakeMode": "now",
        "message": (
            f"[SESSION] ideas:{idea_id}:upload-{upload_turn}\n\n"
            f"I uploaded a file. Please read ~/.openclaw/ideas/{idea_id}/uploaded_seed.md "
            f"and synthesize its content into the canonical PRD template (all sections), "
            f"preserving my intent. Explain briefly what you structured in your reply."
        ),
    }

    headers = {"Authorization": f"Bearer {hooks_token}"}
    async with aiohttp.ClientSession() as session:
        await session.post(hooks_url, json=webhook_payload, headers=headers)

    done_path = turns_dir / f"{upload_turn}.done"
    deadline = datetime.utcnow().timestamp() + POLL_TIMEOUT
    while datetime.utcnow().timestamp() < deadline:
        if done_path.exists():
            break
        await asyncio.sleep(POLL_INTERVAL)
    else:
        raise HTTPException(
            status_code=408,
            detail=f"Upload synthesis timed out after {POLL_TIMEOUT}s",
        )

    prd_draft_path = idea_dir / "prd_draft.md"
    prd_content = prd_draft_path.read_text() if prd_draft_path.exists() else text

    session_path = idea_dir / "session.json"
    session_data = _read_json_file(str(session_path)) or {
        "name": "New Idea",
        "messages": [],
        "prd_content": "",
        "roadmap_content": "",
        "created": None,
        "updated": None,
    }
    session_data.setdefault("name", "New Idea")
    session_data["prd_content"] = prd_content
    session_data["updated"] = datetime.utcnow().isoformat() + "Z"

    tmp_path = str(session_path) + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(session_data, f)
    os.replace(tmp_path, session_path)

    return {"status": "format_ok", "trigger_clarity_check": True}


@app.post("/api/ideas/{idea_id}/clarity-check")
async def post_ideas_clarity_check(idea_id: str):
    """Trigger the PRD clarity check agent and poll for its result.

    Reads current prd_content from session.json, sends a webhook POST to
    hooks_url, then polls for clarity_result.done (2s interval, 60s timeout).
    Returns the contents of clarity_result.json on success, 504 on timeout.
    """
    config = load_config()
    ideas_dir = os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas"))
    hooks_url = config.get("hooks_url", "http://localhost:18789/hooks/agent")
    hooks_token = config.get("hooks_token", "")

    idea_dir = Path(ideas_dir) / idea_id
    if not idea_dir.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    session_path = idea_dir / "session.json"
    session_data = _read_json_file(str(session_path))
    if not session_data:
        raise HTTPException(status_code=404, detail="Session not found")

    prd_content = session_data.get("prd_content", "")
    if not prd_content:
        raise HTTPException(status_code=422, detail="No prd_content to check")

    # Build webhook payload
    timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
    session_key = f"ideas:{idea_id}:clarity-{timestamp_ms}"
    webhook_payload = {
        "agentId": "prd-creator",
        "sessionKey": session_key,
        "wakeMode": "now",
        "message": (
            "Review the following PRD for clarity and completeness. "
            "Do not write or modify any files other than clarity_result.json and clarity_result.done listed below. "
            "Analyze whether all essential sections are present and well-formed. "
            f"Write a JSON object to ~/.openclaw/ideas/{idea_id}/clarity_result.json with schema "
            '{"pass": bool, "missing_sections": [str], "issues": [str]}, '
            f"then create ~/.openclaw/ideas/{idea_id}/clarity_result.done.\n\n"
            f"PRD CONTENT:\n{prd_content}"
        ),
    }

    # Send webhook POST
    headers = {"Authorization": f"Bearer {hooks_token}"}
    async with aiohttp.ClientSession() as session:
        await session.post(hooks_url, json=webhook_payload, headers=headers)

    # Poll for clarity_result.done
    done_path = idea_dir / "clarity_result.done"
    result_path = idea_dir / "clarity_result.json"
    deadline = datetime.utcnow().timestamp() + 60

    while datetime.utcnow().timestamp() < deadline:
        if done_path.exists():
            break
        await asyncio.sleep(2)
    else:
        raise HTTPException(status_code=504, detail="Clarity check timed out after 60s")

    if not result_path.exists():
        raise HTTPException(status_code=500, detail="clarity_result.done exists but clarity_result.json is missing")

    result_data = _read_json_file(str(result_path))
    if result_data is None:
        raise HTTPException(status_code=500, detail="clarity_result.json is not valid JSON")

    return result_data


CONVERT_TIMEOUT = 180   # seconds; patchable in tests
CONVERT_POLL_INTERVAL = 2  # seconds between sentinel checks


@app.get("/api/ideas/{idea_id}/readiness")
def get_idea_readiness(idea_id: str):
    """Serve agent-written readiness.json; status reflects sentinel + JSON validity."""
    config = load_config()
    ideas_dir = Path(os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas")))
    idea_dir = ideas_dir / idea_id
    if not idea_dir.exists():
        raise HTTPException(status_code=404, detail="Idea not found")
    sentinel = idea_dir / "readiness.done"
    json_path = idea_dir / "readiness.json"
    if sentinel.exists():
        data = _read_json_file(str(json_path))
        _active_readiness_jobs.discard(idea_id)
        _readiness_job_started_at.pop(idea_id, None)
        if data is None:
            status = "unavailable"
            logger.debug(f"[READINESS] Status for {idea_id}: {status}")
            return {"status": status, "data": None}
        status = "ready"
        logger.info(f"[READINESS] Sentinel found for {idea_id}")
        logger.debug(f"[READINESS] Status for {idea_id}: {status}")
        return {"status": status, "data": data}

    if idea_id in _active_readiness_jobs:
        status = "updating"
        logger.debug(f"[READINESS] Status for {idea_id}: {status}")
        return {"status": status, "data": None}

    started_at = _readiness_job_started_at.get(idea_id)
    if started_at is not None:
        age = datetime.utcnow().timestamp() - started_at
        if age <= READINESS_ACTIVE_WINDOW_SECONDS:
            status = "updating"
            logger.debug(f"[READINESS] Status for {idea_id}: {status}")
            return {"status": status, "data": None}
        logger.warning(f"[READINESS] Assessment timed out for {idea_id}")
        _readiness_job_started_at.pop(idea_id, None)

    status = "unavailable"
    logger.debug(f"[READINESS] Status for {idea_id}: {status}")
    return {"status": status, "data": None}


@app.get("/api/ideas/{idea_id}/readiness/poll")
def poll_readiness_done(idea_id: str):
    """Lightweight poll for readiness.done sentinel."""
    config = load_config()
    ideas_dir = Path(os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas")))
    idea_dir = ideas_dir / idea_id
    if not idea_dir.exists():
        raise HTTPException(status_code=404, detail="Idea not found")
    return {"done": (idea_dir / "readiness.done").exists()}


@app.post("/api/ideas/{idea_id}/convert")
async def post_ideas_convert(idea_id: str):
    """Trigger PRD-to-roadmap conversion.

    Reads conversion prompt from config.conversion_prompt_path, sends a webhook
    to the prd-creator agent, polls for roadmap_draft.done (2s interval, 180s
    timeout), then atomically stores the resulting roadmap_content in
    session.json and returns it.

    Returns 404 if the idea is not found.
    Returns 422 if prd_content is empty.
    Returns 503 if the conversion prompt file is missing.
    Returns 408 if polling times out.
    Returns 200 with {"roadmap_content": str} on success.
    """
    config = load_config()
    ideas_dir = os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas"))
    hooks_url = config.get("hooks_url", "http://localhost:18789/hooks/agent")
    hooks_token = config.get("hooks_token", "")
    conversion_prompt_path = os.path.expanduser(
        config.get("conversion_prompt_path", "")
    )

    idea_dir = Path(ideas_dir) / idea_id
    if not idea_dir.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    session_path = idea_dir / "session.json"
    session_data = _read_json_file(str(session_path)) or {}
    prd_content = session_data.get("prd_content", "") or ""

    if not prd_content:
        raise HTTPException(status_code=422, detail="No prd_content to convert")

    # Read conversion prompt — 503 if missing
    if not Path(conversion_prompt_path).exists():
        raise HTTPException(status_code=503, detail="Conversion prompt file not found")

    conversion_prompt = Path(conversion_prompt_path).read_text()

    # Build webhook payload
    timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
    session_key = f"ideas:{idea_id}:convert-{timestamp_ms}"
    webhook_payload = {
        "agentId": "prd-creator",
        "sessionKey": session_key,
        "wakeMode": "now",
        "message": (
            f"{conversion_prompt.strip()}\n\n"
            f"---\n\n"
            f"{prd_content}\n\n"
            f"Write the resulting roadmap.md content to "
            f"~/.openclaw/ideas/{idea_id}/roadmap_draft.md, then create "
            f"~/.openclaw/ideas/{idea_id}/roadmap_draft.done."
        ),
    }

    # Send webhook POST
    headers = {"Authorization": f"Bearer {hooks_token}"}
    async with aiohttp.ClientSession() as session:
        await session.post(hooks_url, json=webhook_payload, headers=headers)

    # Poll for roadmap_draft.done
    done_path = idea_dir / "roadmap_draft.done"
    deadline = datetime.utcnow().timestamp() + CONVERT_TIMEOUT

    while datetime.utcnow().timestamp() < deadline:
        if done_path.exists():
            break
        await asyncio.sleep(CONVERT_POLL_INTERVAL)
    else:
        raise HTTPException(
            status_code=408,
            detail=f"Conversion timed out after {CONVERT_TIMEOUT}s"
        )

    # Read roadmap content
    roadmap_draft_path = idea_dir / "roadmap_draft.md"
    roadmap_content = ""
    if roadmap_draft_path.exists():
        roadmap_content = roadmap_draft_path.read_text()

    # Atomically store roadmap_content in session.json
    session_data["roadmap_content"] = roadmap_content
    session_data["updated"] = datetime.utcnow().isoformat() + "Z"
    tmp_path = str(session_path) + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(session_data, f)
    os.replace(tmp_path, session_path)

    return {"roadmap_content": roadmap_content}


@app.get("/api/ideas/{idea_id}/download-roadmap")
def get_ideas_download_roadmap(idea_id: str):
    """Download the roadmap_content from session.json as a markdown file.

    Filename is derived from the first # heading in prd_content,
    or falls back to the idea id. Suffix is always "-roadmap.md".
    Returns 404 if the idea is not found or roadmap_content is empty.
    """
    config = load_config()
    ideas_dir = os.path.expanduser(config.get("ideas_dir", "~/.openclaw/ideas"))
    session_path = Path(ideas_dir) / idea_id / "session.json"

    if not session_path.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    session_data = _read_json_file(str(session_path)) or {}
    roadmap_content = session_data.get("roadmap_content", "") or ""

    if not roadmap_content:
        raise HTTPException(status_code=404, detail="No roadmap content available")

    # Derive filename from first # heading in prd_content, or fall back to id
    prd_content = session_data.get("prd_content", "") or ""
    filename = idea_id
    for line in prd_content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            heading = stripped[2:].strip()
            filename = heading.replace(" ", "-")
            break

    filename = filename + "-roadmap.md"

    from fastapi.responses import Response
    return Response(
        content=roadmap_content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _orchestrator_alive_from_config(config: dict) -> bool:
    """Best-effort liveness from pipeline.lock (same semantics as /api/state)."""
    lp = config.get("lock_path")
    lp = os.path.expanduser(lp) if lp else None
    if not lp:
        return False
    try:
        return _check_orchestrator_liveness(lp)
    except Exception:
        return False


@app.post("/api/stop")
def post_stop():
    """Request pipeline halt: sentinel file for active agents, or escalation STOP when waiting for human.

    - RUNNING / WAITING_FOR_SENTINEL: writes ``pipeline_stop_requested`` under the project directory.
    - WAITING_FOR_HUMAN: validates and writes ``escalation_output.json`` + ``escalation_output.done``
      (same contract as ``POST /api/command`` with STOP).

    Returns:
        200 with ok, message, orchestrator_alive, and optional hint when escalation stop may need a running orchestrator.
        409 if pipeline is not in a stoppable state.
        503 if pipeline state cannot be read.
    """
    config = load_config()
    pipeline_state_path = config.get("pipeline_state_path")
    project_dir_path = config.get("project_dir_path")
    phase_state_path = config.get("phase_state_path")

    pipeline_state_path = os.path.expanduser(pipeline_state_path) if pipeline_state_path else None
    project_dir_path = os.path.expanduser(project_dir_path) if project_dir_path else None
    phase_state_path = os.path.expanduser(phase_state_path) if phase_state_path else None

    pipeline_state = _read_json_file(pipeline_state_path) if pipeline_state_path else None
    if not pipeline_state:
        raise HTTPException(status_code=503, detail="Pipeline state not found")

    status = pipeline_state.get("pipeline_status")
    alive = _orchestrator_alive_from_config(config)

    if status in ("RUNNING", "WAITING_FOR_SENTINEL"):
        stop_file = Path(os.path.realpath(project_dir_path)) / "pipeline_stop_requested"
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.touch()

        return {
            "ok": True,
            "message": "Stop requested — pipeline will halt after current agent completes",
            "orchestrator_alive": alive,
        }

    if status == "WAITING_FOR_HUMAN":
        phase_state = _read_json_file(phase_state_path) if phase_state_path else {}
        escalation_resets = phase_state.get("escalation_resets", 0) if phase_state else 0

        is_valid, error_msg, error_code = _validate_command_request(
            project_dir_path, status, escalation_resets, "STOP"
        )
        if not is_valid:
            raise HTTPException(status_code=error_code, detail=error_msg)

        _write_escalation_files(project_dir_path, "STOP")

        hint = None
        if not alive:
            hint = (
                "Orchestrator does not appear to be running — resume or start it so the stop command is applied."
            )

        return {
            "ok": True,
            "message": "Stop command queued for orchestrator",
            "orchestrator_alive": alive,
            **({"hint": hint} if hint else {}),
        }

    raise HTTPException(
        status_code=409,
        detail=f"Pipeline is not in a stoppable state (current: {status})",
    )


@app.post("/api/setup/roadmap-seed")
async def post_setup_roadmap_seed(request: Request):
    """Store roadmap seed content atomically to ~/.openclaw/setup_session.json.

    Body: {"content": str}
    Returns: {"ok": true}
    """
    body = await request.json()
    content = body.get("content")
    if content is None:
        raise HTTPException(status_code=422, detail="Missing required field: content")
    setup_path = Path("~/.openclaw/setup_session.json").expanduser()
    setup_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = str(setup_path) + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump({"roadmap_seed": content}, f)
    os.replace(tmp_path, str(setup_path))
    return {"ok": True}


# ─── Roadmap validation ───────────────────────────────────────────────────────

_PHASE_LINE_RE = re.compile(
    r"^- \[.\] `([A-Z]+-[A-Z]\d+)` \| (?:LOW|HIGH) \| .+",
    re.MULTILINE,
)


def _validate_roadmap_content(content: str) -> dict:
    """Validate roadmap content format.

    Checks:
    1. Phase lines match the required format.
    2. Each phase has a '> Test:' line within 10 lines.
    3. No duplicate phase IDs.
    4. At least one phase line exists.

    Returns {"valid": bool, "errors": [{"line": int, "content": str, "message": str}]}
    """
    errors = []
    lines = content.splitlines()

    # Collect phase matches with line numbers (1-based)
    phase_matches = []  # (line_number, phase_id)
    for i, line in enumerate(lines, start=1):
        m = _PHASE_LINE_RE.match(line)
        if m:
            phase_matches.append((i, m.group(1), line))

    if not phase_matches:
        errors.append({
            "line": 0,
            "content": "",
            "message": "At least one valid phase line is required",
        })
        return {"valid": False, "errors": errors}

    # Check Test: line within 10 lines of each phase line
    for line_num, phase_id, line_content in phase_matches:
        found_test = False
        for j in range(line_num, min(line_num + 10, len(lines) + 1)):
            if re.match(r"^\s*> Test:", lines[j - 1]):
                found_test = True
                break
        if not found_test:
            errors.append({
                "line": line_num,
                "content": line_content,
                "message": f"Phase {phase_id} (line {line_num}) is missing a '> Test:' line",
            })

    # Check for duplicate phase IDs
    all_ids = re.findall(r"`([A-Z]+-[A-Z]\d+)`", content)
    seen: dict = {}
    for pid in all_ids:
        seen[pid] = seen.get(pid, 0) + 1
    for pid, count in seen.items():
        if count > 1:
            errors.append({
                "line": 0,
                "content": pid,
                "message": f"Duplicate phase ID: {pid} appears {count} times",
            })

    return {"valid": len(errors) == 0, "errors": errors}


def _normalize_setup_repo_path(raw) -> Path:
    """Strip, expanduser, and require an absolute path (avoids CWD-relative mistakes)."""
    s = str(raw or "").strip()
    if not s:
        raise ValueError("empty")
    if "\x00" in s:
        raise ValueError("null")
    if len(s) >= 512:
        raise ValueError("long")
    p = Path(s).expanduser()
    if not p.is_absolute():
        raise ValueError("relative")
    return p


@app.post("/api/setup/validate-repo-path")
async def post_setup_validate_repo_path(request: Request):
    """Validate repo path string (format + absolute path; no filesystem existence check)."""
    body = await request.json()
    path = body.get("path", "")
    if path is None:
        path = ""
    try:
        _normalize_setup_repo_path(path)
    except ValueError as exc:
        code = str(exc)
        if code == "empty":
            return {"valid": False, "error": "Enter a directory path to continue"}
        if code == "null":
            return {"valid": False, "error": "Path contains invalid characters"}
        if code == "long":
            return {"valid": False, "error": "Path is too long"}
        if code == "relative":
            return {
                "valid": False,
                "error": (
                    "Use an absolute path starting with / "
                    "(e.g. /home/pi/projects/my-app)"
                ),
            }
        raise
    return {"valid": True, "error": None}


@app.post("/api/setup/check-repo-path")
async def post_setup_check_repo_path(request: Request):
    """Return filesystem existence and git metadata for a path string."""
    body = await request.json()
    raw = body.get("path", "")
    try:
        path = _normalize_setup_repo_path(raw)
    except ValueError:
        return {
            "path": str(raw or "").strip(),
            "exists": False,
            "parent_exists": False,
            "is_git_repo": False,
            "error": "invalid_path",
        }
    return {
        "path": str(path),
        "exists": path.exists(),
        "parent_exists": path.parent.exists(),
        "is_git_repo": (path / ".git").exists(),
    }


@app.post("/api/setup/create-repo-dir")
async def post_setup_create_repo_dir(request: Request):
    """Create a single directory (no parents). Body: {"path": str}."""
    body = await request.json()
    raw = body.get("path", "")
    try:
        path = _normalize_setup_repo_path(raw)
    except ValueError:
        return {
            "ok": False,
            "error": "Use an absolute path starting with / (e.g. /home/pi/projects/my-app)",
        }
    try:
        path.mkdir(parents=False, exist_ok=False)
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/setup/validate-roadmap")
async def post_setup_validate_roadmap(request: Request):
    """Validate roadmap seed content format.

    Body: {"content": str}
    Returns: {"valid": bool, "errors": [{"line": int, "content": str, "message": str}]}
    No file writes.
    """
    body = await request.json()
    content = body.get("content", "")
    return _validate_roadmap_content(content)


# ─── Preflight checks ────────────────────────────────────────────────────────

_PIPELINE_GITIGNORE_ENTRIES = [
    "*.done",
    "phase_state.json",
    "planner_output.json",
    "executor_output.json",
    "reviewer_output.json",
    "escalation_output.json",
    "current_phase.json",
]
_PIPELINE_GITIGNORE_HEADER = "# Pipeline metadata — orchestrator-managed per-turn state, never committed"

_WORKSPACE_DOCS = ["AGENTS.md", "TOOLS.md", "SOUL.md", "USER.md", "IDENTITY.md"]
_WORKSPACE_AGENTS = ["planner", "executor", "reviewer", "escalation"]


def _normalize_doc_text_for_compare(s: str) -> str:
    """Normalize markdown for equality checks (line endings + trailing whitespace)."""
    if not s:
        return ""
    text = s.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).rstrip()


def _atomic_write_file(path: str, content: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, path)


def _preflight_materialize(repo_path: str, roadmap_seed, prd_content) -> list:
    """Write roadmap/prd from preflight request when valid. Returns extra check rows."""
    import glob as glob_mod

    checks = []
    os.makedirs(repo_path, exist_ok=True)

    rs = roadmap_seed if roadmap_seed is not None else ""
    rs = rs.strip()
    if rs:
        val = _validate_roadmap_content(rs)
        if not val["valid"]:
            em = "; ".join(e["message"] for e in val["errors"][:3])
            checks.append({
                "check": "roadmap seed",
                "status": "fail",
                "message": f"Invalid roadmap format: {em}",
            })
            return checks

        matches = sorted(glob_mod.glob(os.path.join(repo_path, "*oadmap*.md")))
        mismatch_names = []
        for p in matches:
            try:
                with open(p) as f:
                    disk = f.read()
            except OSError as exc:
                checks.append({
                    "check": "roadmap conflict",
                    "status": "fail",
                    "message": f"Could not read {os.path.basename(p)}: {exc}",
                })
                return checks
            if _normalize_doc_text_for_compare(disk) != _normalize_doc_text_for_compare(rs):
                mismatch_names.append(os.path.basename(p))

        if mismatch_names:
            checks.append({
                "check": "roadmap conflict",
                "status": "fail",
                "message": (
                    "Roadmap on disk does not match the seed in the UI. Edit the seed, replace the file(s), or remove them. "
                    f"Conflicting: {', '.join(mismatch_names)}"
                ),
            })
            return checks

        roadmap_path = os.path.join(repo_path, "roadmap.md")
        if not matches:
            try:
                _atomic_write_file(roadmap_path, rs)
            except OSError as exc:
                checks.append({
                    "check": "roadmap write",
                    "status": "fail",
                    "message": str(exc),
                })
                return checks
            checks.append({
                "check": "roadmap write",
                "status": "fixed",
                "message": "Wrote roadmap.md from seed",
            })

    pc = prd_content if prd_content is not None else ""
    pc = pc.strip()
    if pc:
        prd_path = os.path.join(repo_path, "prd.md")
        if os.path.exists(prd_path):
            try:
                existing = Path(prd_path).read_text()
            except OSError as exc:
                checks.append({
                    "check": "prd conflict",
                    "status": "fail",
                    "message": f"Could not read prd.md: {exc}",
                })
                return checks
            if _normalize_doc_text_for_compare(existing) != _normalize_doc_text_for_compare(pc):
                checks.append({
                    "check": "prd conflict",
                    "status": "fail",
                    "message": "prd.md on disk does not match the PRD staged in the UI.",
                })
                return checks
        else:
            try:
                _atomic_write_file(prd_path, pc)
            except OSError as exc:
                checks.append({
                    "check": "prd write",
                    "status": "fail",
                    "message": str(exc),
                })
                return checks
            checks.append({
                "check": "prd write",
                "status": "fixed",
                "message": "Wrote prd.md",
            })

    return checks


def _run_preflight_checks(repo_path: str) -> list:
    """Run ordered preflight checks for a project directory.

    Auto-fixes symlink and .gitignore when possible. Returns list of
    {"check": str, "status": str, "message": str} with status pass|fail|warn|fixed.
    """
    import subprocess
    import glob as glob_mod

    repo_path = os.path.realpath(os.path.expanduser(repo_path))
    openclaw_dir = os.path.expanduser("~/.openclaw")
    checks = []

    # 1. Symlink — create or repair ~/.openclaw/pipeline-project → repo_path
    symlink_path = os.path.join(openclaw_dir, "pipeline-project")
    try:
        ok = os.path.lexists(symlink_path) and os.path.realpath(symlink_path) == repo_path
        if ok:
            checks.append({
                "check": "symlink",
                "status": "pass",
                "message": f"Symlink points to {repo_path}",
            })
        else:
            if os.path.lexists(symlink_path):
                os.remove(symlink_path)
            os.symlink(repo_path, symlink_path)
            checks.append({
                "check": "symlink",
                "status": "fixed",
                "message": f"Symlink created → {repo_path}",
            })
    except OSError as exc:
        checks.append({
            "check": "symlink",
            "status": "fail",
            "message": (
                f"Could not create symlink ({exc}). Run: "
                f"ln -sfn {repo_path} ~/.openclaw/pipeline-project"
            ),
        })

    # 2. .gitignore presence — create with pipeline block if missing
    gitignore_path = os.path.join(repo_path, ".gitignore")
    if not os.path.exists(gitignore_path):
        try:
            body = (
                _PIPELINE_GITIGNORE_HEADER + "\n"
                + "\n".join(_PIPELINE_GITIGNORE_ENTRIES)
                + "\n"
            )
            with open(gitignore_path, "w") as f:
                f.write(body)
            checks.append({
                "check": ".gitignore",
                "status": "fixed",
                "message": "Created .gitignore with pipeline entries.",
            })
        except OSError as exc:
            checks.append({
                "check": ".gitignore",
                "status": "fail",
                "message": f"Could not create .gitignore: {exc}",
            })
    else:
        checks.append({
            "check": ".gitignore",
            "status": "pass",
            "message": ".gitignore present",
        })

    # 3. .gitignore entries — append missing pipeline lines
    if os.path.exists(gitignore_path):
        try:
            with open(gitignore_path, "r") as f:
                existing = f.read()
            missing = [e for e in _PIPELINE_GITIGNORE_ENTRIES if e not in existing]
            if missing:
                inject = "\n" + _PIPELINE_GITIGNORE_HEADER + "\n" + "\n".join(missing) + "\n"
                with open(gitignore_path, "a") as f:
                    f.write(inject)
                checks.append({
                    "check": ".gitignore entries",
                    "status": "fixed",
                    "message": f"Added {len(missing)} missing entries: {', '.join(missing)}",
                })
            else:
                checks.append({
                    "check": ".gitignore entries",
                    "status": "pass",
                    "message": "All required entries present",
                })
        except Exception as exc:
            checks.append({
                "check": ".gitignore entries",
                "status": "fail",
                "message": f"Could not read/write .gitignore: {exc}",
            })

    # 3b. Git executable on PATH (required for init/commits)
    gv = subprocess.run(["git", "--version"], capture_output=True, text=True)
    if gv.returncode == 0:
        checks.append({
            "check": "git",
            "status": "pass",
            "message": (gv.stdout or "").strip() or "git is available",
        })
    else:
        checks.append({
            "check": "git",
            "status": "fail",
            "message": "git is not available on PATH — install git or fix PATH.",
        })

    # 4. Git repo + main/master branch (auto-init when .git is missing)
    did_fresh_init = False
    git_dir = os.path.join(repo_path, ".git")
    if not os.path.exists(git_dir):
        try:
            subprocess.run(["git", "init", repo_path], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", repo_path, "branch", "-M", "main"],
                check=True,
                capture_output=True,
            )
            did_fresh_init = True
            checks.append({
                "check": "git repo",
                "status": "fixed",
                "message": "Initialized git repository (branch main)",
            })
        except (subprocess.CalledProcessError, OSError) as exc:
            stderr = getattr(exc, "stderr", None)
            if stderr is not None and isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            else:
                stderr = str(exc)
            checks.append({
                "check": "git repo",
                "status": "fail",
                "message": (
                    "Not a git repository and auto-init failed. Run: "
                    f"git -C {repo_path} init && git -C {repo_path} branch -M main"
                    + (f" — {stderr}" if stderr else "")
                ),
            })

    git_dir = os.path.join(repo_path, ".git")
    if os.path.exists(git_dir) and not did_fresh_init:
        result = subprocess.run(
            ["git", "-C", repo_path, "branch", "--list", "main", "master"],
            capture_output=True, text=True,
        )
        if result.stdout.strip():
            checks.append({"check": "git repo", "status": "pass",
                            "message": "Git repo present with main/master branch"})
        else:
            sym = subprocess.run(
                ["git", "-C", repo_path, "symbolic-ref", "--short", "HEAD"],
                capture_output=True, text=True,
            )
            current_branch = (sym.stdout or "").strip()
            if current_branch in ("main", "master"):
                checks.append({"check": "git repo", "status": "warn",
                                "message": (
                                    f"Git repo is on '{current_branch}' but has no commits yet. "
                                    f"Make an initial commit before launching the pipeline. "
                                    f"Run: git -C {repo_path} add -A && git -C {repo_path} commit -m 'init'"
                                )})
            else:
                checks.append({"check": "git repo", "status": "fail",
                                "message": (
                                    "No main or master branch found. Run: "
                                    f"git -C {repo_path} checkout -b main"
                                )})

    # 5. Workspace directories and docs
    for agent in _WORKSPACE_AGENTS:
        ws_dir = os.path.join(openclaw_dir, f"workspace-{agent}")
        if not os.path.isdir(ws_dir):
            checks.append({"check": f"workspace-{agent}", "status": "fail",
                            "message": f"workspace-{agent} directory missing"})
        else:
            for doc in _WORKSPACE_DOCS:
                doc_path = os.path.join(ws_dir, doc)
                if not os.path.exists(doc_path):
                    checks.append({"check": f"workspace-{agent}/{doc}", "status": "fail",
                                    "message": f"workspace-{agent}/{doc} missing — operator must install this file."})
            # Only add pass if no failures for this workspace
            missing_docs = [d for d in _WORKSPACE_DOCS if not os.path.exists(os.path.join(ws_dir, d))]
            if not missing_docs:
                checks.append({"check": f"workspace-{agent}", "status": "pass",
                                "message": f"workspace-{agent} present with all required docs"})

    # 6. Roadmap file on disk (warn if missing)
    import glob as glob_mod
    roadmap_files = glob_mod.glob(os.path.join(repo_path, "*oadmap*.md"))
    if roadmap_files:
        checks.append({"check": "roadmap file", "status": "pass",
                        "message": f"Found: {os.path.basename(roadmap_files[0])}"})
    else:
        checks.append({"check": "roadmap file", "status": "warn",
                        "message": (
                            "No roadmap file found — lock a seed and Run Preflight to write roadmap.md, "
                            "or use Launch to bootstrap the project."
                        )})

    return checks


@app.post("/api/setup/preflight")
async def post_setup_preflight(request: Request):
    """Run preflight validation checks for a project directory.

    Body: {"repo_path": str, "roadmap_seed": optional, "prd_content": optional,
           "confirm_roadmap_archive": optional bool, "keep_filename": optional str}
    When multiple *oadmap*.md exist, returns roadmap_ambiguous until confirm_roadmap_archive.
    Returns: {"checks": [...], optional roadmap_ambiguous, roadmap_files, recommended_keep}
    """
    body = await request.json()
    repo_path = body.get("repo_path", "")
    roadmap_seed = body.get("roadmap_seed")
    prd_content = body.get("prd_content")
    confirm_roadmap_archive = bool(body.get("confirm_roadmap_archive"))
    keep_filename = body.get("keep_filename")
    if not repo_path:
        raise HTTPException(status_code=422, detail="repo_path is required")
    repo_abs = os.path.realpath(os.path.expanduser(repo_path.strip()))
    try:
        os.makedirs(repo_abs, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Cannot use repo path: {exc}") from exc

    roadmap_paths = _glob_project_roadmap_paths(repo_abs)
    if len(roadmap_paths) > 1:
        basenames = [os.path.basename(p) for p in roadmap_paths]
        if not confirm_roadmap_archive:
            rk = _recommended_keep_roadmap_basename(repo_abs)
            return {
                "checks": [
                    {
                        "check": "roadmap files",
                        "status": "warn",
                        "message": (
                            f"Multiple roadmap files: {', '.join(basenames)}. "
                            "Confirm to archive extras under autodev_archive/."
                        ),
                    }
                ],
                "roadmap_ambiguous": True,
                "roadmap_files": basenames,
                "recommended_keep": rk,
            }
        keep = (keep_filename or _recommended_keep_roadmap_basename(repo_abs)).strip()
        if keep not in basenames:
            raise HTTPException(
                status_code=422,
                detail=f"keep_filename must be one of: {', '.join(basenames)}",
            )
        _archive_extra_roadmaps(repo_abs, keep)

    mat = _preflight_materialize(repo_abs, roadmap_seed, prd_content)
    if any(c.get("status") == "fail" for c in mat):
        return {"checks": mat}

    header_checks = []
    if roadmap_seed is not None and isinstance(roadmap_seed, str) and roadmap_seed.strip():
        header_checks.append({
            "check": "roadmap seed",
            "status": "warn",
            "message": (
                "Seed format is validated here; content quality is not. "
                "For a substantive PRD-first workflow, use Project Ideas and add a starting document there."
            ),
        })

    checks = _run_preflight_checks(repo_abs)
    all_checks = header_checks + mat + checks
    if not any(c.get("status") == "fail" for c in all_checks):
        append_recent_project(repo_abs)
    return {"checks": all_checks}


@app.get("/api/setup/recent-projects")
def get_setup_recent_projects():
    """Recent project directories (real paths) that passed preflight or switch validation."""
    return {"projects": _read_recent_projects()}


@app.post("/api/setup/switch-project")
async def post_setup_switch_project(request: Request):
    """Validate and optionally switch active project (pipeline must be STOPPED).

    Body: repo_path, optional roadmap_seed/prd_content, confirm_roadmap_archive, keep_filename,
          confirm_destructive (list of basenames), start_orchestrator (bool).
    """
    allowed, cur_status = _pipeline_allows_project_switch()
    if not allowed:
        raise HTTPException(
            status_code=409,
            detail=(
                "Stop the pipeline before changing the project directory "
                f"(current status: {cur_status})."
            ),
        )

    body = await request.json()
    repo_path = (body.get("repo_path") or "").strip()
    roadmap_seed = body.get("roadmap_seed")
    prd_content = body.get("prd_content")
    confirm_roadmap_archive = bool(body.get("confirm_roadmap_archive"))
    keep_filename = body.get("keep_filename")
    confirm_destructive = body.get("confirm_destructive")
    if confirm_destructive is None:
        confirm_destructive = []
    if not isinstance(confirm_destructive, list):
        raise HTTPException(status_code=422, detail="confirm_destructive must be a list")
    start_orchestrator = bool(body.get("start_orchestrator"))

    if not repo_path:
        raise HTTPException(status_code=422, detail="repo_path is required")

    repo_abs = os.path.realpath(os.path.expanduser(repo_path))
    try:
        os.makedirs(repo_abs, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Cannot use repo path: {exc}") from exc

    roadmap_paths = _glob_project_roadmap_paths(repo_abs)
    if len(roadmap_paths) > 1:
        basenames = [os.path.basename(p) for p in roadmap_paths]
        if not confirm_roadmap_archive:
            return {
                "ok": False,
                "roadmap_ambiguous": True,
                "roadmap_files": basenames,
                "recommended_keep": _recommended_keep_roadmap_basename(repo_abs),
            }
        keep = (keep_filename or _recommended_keep_roadmap_basename(repo_abs)).strip()
        if keep not in basenames:
            raise HTTPException(
                status_code=422,
                detail=f"keep_filename must be one of: {', '.join(basenames)}",
            )
        _archive_extra_roadmaps(repo_abs, keep)

    mat = _preflight_materialize(repo_abs, roadmap_seed, prd_content)
    if any(c.get("status") == "fail" for c in mat):
        return {"ok": False, "checks": mat, "coherence": None}

    checks = _run_preflight_checks(repo_abs)
    all_pre = mat + checks
    if any(c.get("status") == "fail" for c in checks):
        return {"ok": False, "checks": all_pre, "coherence": None}

    coherence = _validate_project_coherence(repo_abs)
    if not coherence.get("ok"):
        if confirm_destructive:
            ok_del, err = _apply_destructive_project_files(repo_abs, confirm_destructive)
            if not ok_del:
                return {
                    "ok": False,
                    "checks": all_pre,
                    "coherence": {"ok": False, "issues": coherence.get("issues", [])},
                    "destructive_error": err,
                }
            coherence = _validate_project_coherence(repo_abs)
        if not coherence.get("ok"):
            return {
                "ok": False,
                "checks": all_pre,
                "coherence": {"ok": False, "issues": coherence.get("issues", [])},
            }

    append_recent_project(repo_abs)

    if not start_orchestrator:
        return {
            "ok": True,
            "checks": all_pre,
            "coherence": {"ok": True, "issues": []},
            "ready_to_start": True,
        }

    config = load_config()
    lock_path = _expand_lock_path(config)
    if lock_path and _check_orchestrator_liveness(lock_path):
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "code": "orchestrator_running",
                "error": (
                    "An orchestrator is already running. Stop it before starting the pipeline "
                    "for this project."
                ),
            },
        )

    pipeline_state_path = config.get("pipeline_state_path")
    pipeline_state_path = os.path.expanduser(pipeline_state_path) if pipeline_state_path else None
    if not pipeline_state_path:
        return {
            "ok": False,
            "checks": all_pre,
            "coherence": {"ok": True, "issues": []},
            "error": "pipeline_state_path is not configured",
        }

    parent = os.path.dirname(pipeline_state_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        _write_json_atomic(pipeline_state_path, _clean_pipeline_state_for_project(repo_abs))
    except OSError as exc:
        return {
            "ok": False,
            "checks": all_pre,
            "coherence": {"ok": True, "issues": []},
            "error": f"Could not write pipeline_state.json: {exc}",
        }

    spawned = _spawn_orchestrator(repo_abs, config)
    if not spawned.get("ok"):
        return {
            "ok": False,
            "checks": all_pre,
            "coherence": {"ok": True, "issues": []},
            "error": spawned.get("error") or "Failed to spawn orchestrator",
        }

    return {
        "ok": True,
        "checks": all_pre,
        "coherence": {"ok": True, "issues": []},
        "started": True,
    }


# ─── Launch sequence ──────────────────────────────────────────────────────────

def _run_init_project(repo_path: str, roadmap_seed: str, prd_content=None) -> dict:
    """Initialize a project directory (Mode A: new repo, Mode B: existing repo).

    Mode A: .git does NOT exist → create full structure, git init, initial commit.
    Mode B: .git exists → create only missing files, append missing gitignore entries.

    Returns {"ok": bool, "error": str|null}
    """
    import subprocess
    import shutil

    repo_path = os.path.expanduser(repo_path)
    name = os.path.basename(repo_path.rstrip("/"))
    now = datetime.utcnow().isoformat() + "Z"
    mode = "B" if os.path.exists(os.path.join(repo_path, ".git")) else "A"

    def atomic_write(path: str, content: str):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(content)
        os.replace(tmp, path)

    try:
        if mode == "A":
            # Step 1: directory structure
            os.makedirs(os.path.join(repo_path, "phases"), exist_ok=True)
            os.makedirs(os.path.join(repo_path, "tests"), exist_ok=True)
            src_dir = os.path.join(repo_path, "src", name)
            os.makedirs(src_dir, exist_ok=True)
            init_py = os.path.join(src_dir, "__init__.py")
            if not os.path.exists(init_py):
                open(init_py, "w").close()

            # Step 2: pipeline.json
            pipeline = {
                "project": name,
                "created": now,
                "current_phase": None,
                "current_plan": None,
                "phase_start_time": None,
                "completed_count": 0,
                "status": "idle",
            }
            atomic_write(os.path.join(repo_path, "pipeline.json"), json.dumps(pipeline, indent=2))

            # Step 3: roadmap.md
            atomic_write(os.path.join(repo_path, "roadmap.md"), roadmap_seed)

            # Step 4: validate roadmap
            validation = _validate_roadmap_content(roadmap_seed)
            if not validation["valid"]:
                shutil.rmtree(repo_path, ignore_errors=True)
                errors_str = "; ".join(e["message"] for e in validation["errors"][:3])
                return {"ok": False, "error": f"Roadmap invalid: {errors_str}"}

            # Step 5: PRD (Ideas handoff or placeholder)
            prd_path = os.path.join(repo_path, "prd.md")
            if prd_content and str(prd_content).strip():
                atomic_write(prd_path, str(prd_content))
            elif not os.path.exists(prd_path):
                atomic_write(prd_path, "# PRD\n\n_To be completed._\n")
            lessons_path = os.path.join(repo_path, "lessons.md")
            if not os.path.exists(lessons_path):
                atomic_write(lessons_path, "# Lessons\n\n_Hard-won insights go here._\n")
            metrics_path = os.path.join(repo_path, "metrics.jsonl")
            if not os.path.exists(metrics_path):
                open(metrics_path, "w").close()

            # Step 6: .gitignore
            gitignore_content = (
                "__pycache__/\n*.pyc\n.pytest_cache/\n*.egg-info/\ndist/\nbuild/\n.venv/\n.ruff_cache/\n\n"
                + _PIPELINE_GITIGNORE_HEADER + "\n"
                + "\n".join(_PIPELINE_GITIGNORE_ENTRIES) + "\n"
            )
            atomic_write(os.path.join(repo_path, ".gitignore"), gitignore_content)

            # Step 7: git init + commit
            subprocess.run(["git", "init", repo_path], check=True, capture_output=True)
            subprocess.run(["git", "-C", repo_path, "checkout", "-b", "main"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", repo_path, "add", "-A"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", repo_path, "commit", "-m", "init: project structure with roadmap"],
                check=True, capture_output=True,
            )

        else:  # Mode B
            # Create only missing structure
            for d in ["phases", "tests"]:
                os.makedirs(os.path.join(repo_path, d), exist_ok=True)
            src_dir = os.path.join(repo_path, "src", name)
            os.makedirs(src_dir, exist_ok=True)
            init_py = os.path.join(src_dir, "__init__.py")
            if not os.path.exists(init_py):
                open(init_py, "w").close()

            for fname, content in [
                ("pipeline.json", json.dumps({
                    "project": name, "created": now, "current_phase": None,
                    "current_plan": None, "phase_start_time": None,
                    "completed_count": 0, "status": "idle",
                }, indent=2)),
                ("roadmap.md", roadmap_seed),
                ("lessons.md", "# Lessons\n\n_Hard-won insights go here._\n"),
            ]:
                path = os.path.join(repo_path, fname)
                if not os.path.exists(path):
                    atomic_write(path, content)

            prd_path = os.path.join(repo_path, "prd.md")
            if prd_content and str(prd_content).strip():
                atomic_write(prd_path, str(prd_content))
            elif not os.path.exists(prd_path):
                atomic_write(prd_path, "# PRD\n\n_To be completed._\n")

            metrics_path = os.path.join(repo_path, "metrics.jsonl")
            if not os.path.exists(metrics_path):
                open(metrics_path, "w").close()

            # Append missing gitignore entries
            gitignore_path = os.path.join(repo_path, ".gitignore")
            if os.path.exists(gitignore_path):
                with open(gitignore_path) as f:
                    existing = f.read()
                missing = [e for e in _PIPELINE_GITIGNORE_ENTRIES if e not in existing]
                if missing:
                    with open(gitignore_path, "a") as f:
                        f.write("\n" + _PIPELINE_GITIGNORE_HEADER + "\n" + "\n".join(missing) + "\n")

            # git add + commit new files only
            subprocess.run(["git", "-C", repo_path, "add", "-A"], check=True, capture_output=True)
            result = subprocess.run(
                ["git", "-C", repo_path, "status", "--porcelain"],
                capture_output=True, text=True,
            )
            if result.stdout.strip():
                subprocess.run(
                    ["git", "-C", repo_path, "commit", "-m", "init: add pipeline project structure"],
                    check=True, capture_output=True,
                )

        # Step 8 (both modes): set symlink
        symlink_path = os.path.expanduser("~/.openclaw/pipeline-project")
        if os.path.lexists(symlink_path):
            os.remove(symlink_path)
        os.symlink(repo_path, symlink_path)

        return {"ok": True, "error": None}

    except subprocess.CalledProcessError as exc:
        if mode == "A":
            shutil.rmtree(repo_path, ignore_errors=True)
        return {"ok": False, "error": f"Git command failed: {exc.stderr.decode(errors='replace') if exc.stderr else str(exc)}"}
    except OSError as exc:
        if mode == "A":
            shutil.rmtree(repo_path, ignore_errors=True)
        return {"ok": False, "error": str(exc)}


@app.post("/api/setup/launch")
async def post_setup_launch(request: Request):
    """Initialize project directory, set symlink, sync pipeline_state.json, spawn orchestrator.

    Body: {"repo_path": str, "roadmap_seed": str, "prd_content": optional}
    Returns: {"ok": bool, "error": str|null}; 409 with code orchestrator_running if lock held.
    """
    body = await request.json()
    repo_path = body.get("repo_path", "")
    roadmap_seed = body.get("roadmap_seed", "")
    prd_content = body.get("prd_content")
    if not repo_path:
        raise HTTPException(status_code=422, detail="repo_path is required")

    result = _run_init_project(repo_path, roadmap_seed, prd_content)
    if not result.get("ok"):
        return result

    project_real = os.path.realpath(os.path.expanduser(repo_path.strip()))
    config = load_config()
    lock_path = _expand_lock_path(config)
    if lock_path and _check_orchestrator_liveness(lock_path):
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": (
                    "An orchestrator is already running. Stop the pipeline or wait before launching a new project."
                ),
                "code": "orchestrator_running",
            },
        )

    pipeline_state_path = config.get("pipeline_state_path")
    pipeline_state_path = os.path.expanduser(pipeline_state_path) if pipeline_state_path else None
    if not pipeline_state_path:
        return {
            "ok": False,
            "error": "pipeline_state_path is not configured; cannot sync state or start orchestrator.",
        }

    parent = os.path.dirname(pipeline_state_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        _write_json_atomic(pipeline_state_path, _clean_pipeline_state_for_project(project_real))
    except OSError as exc:
        return {"ok": False, "error": f"Could not write pipeline_state.json: {exc}"}

    spawned = _spawn_orchestrator(project_real, config)
    if not spawned.get("ok"):
        return {"ok": False, "error": spawned.get("error") or "Failed to spawn orchestrator"}

    return {"ok": True, "error": None}