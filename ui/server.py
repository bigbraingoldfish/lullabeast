"""UI server module."""
import fcntl
import json
import os
from collections import deque
from datetime import datetime
from pathlib import Path

import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager

from ui.roadmap_parser import parse_roadmap
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

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


def _create_synthetic_event(event_type, agent=None, phase=None, detail=None):
    """Create a synthetic event dict with required fields.
    
    Args:
        event_type: Type of event (e.g., 'status_changed')
        agent: Current agent name
        phase: Current phase ID
        detail: Additional detail string
    
    Returns:
        Dict with ts, event, agent, phase, detail fields.
    """
    return {
        "ts": datetime.utcnow().isoformat() + "Z",
        "event": event_type,
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
    
    # Override status to 'in_progress' for matching phase (if not empty string)
    if current_phase_raw_id:
        for phase in phases:
            if phase['id'] == current_phase_raw_id:
                phase['status'] = 'in_progress'
                break
    
    return phases