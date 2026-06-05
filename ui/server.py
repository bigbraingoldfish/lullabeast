"""UI server module."""
import base64
import aiohttp
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkstemp

import asyncio

from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager

from ui.roadmap_parser import parse_roadmap

# Queue dependency rules (shared with orchestrator — keep in sync)
import sys as _sys
_AUTODEV_UI_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _AUTODEV_UI_ROOT not in _sys.path:
    _sys.path.insert(0, _AUTODEV_UI_ROOT)
_AUTODEV_PIPELINE_DIR = os.path.join(_AUTODEV_UI_ROOT, "autodev", "pipeline")
if _AUTODEV_PIPELINE_DIR not in _sys.path:
    _sys.path.insert(0, _AUTODEV_PIPELINE_DIR)
from autodev.pipeline.queue_semantics import (
    parent_blocks_child,
    ESCALATION_ANSWERED,
    QUEUE_MAX_CAS_RETRIES,
    QueueAbort,
    QueueVersionConflict,
    bump_queue_version,
    mutate_queue,
    read_queue_version,
)
from autodev.pipeline.sentinel_poller import PollResult  # noqa: E402
from env_resolvers import resolve_openclaw_root, resolve_pipeline_root  # noqa: E402
from skill_manager import SkillManager  # noqa: E402  (W5-E: inline completion reviewer)
from webhook_client import invoke_agent_webhook  # noqa: E402
from sentinel_poller import cleanup_output_files  # noqa: E402

ORCHESTRATOR_FILENAME = "orchestrator.py"
WEBHOOK_AGENT_ID = "prd-creator"
ROADMAP_CONVERTER_AGENT_ID = "roadmap-converter"
# ORCHESTRATOR_POLL_TIMEOUT: spawn/orchestrator wait (separate from ideas POLL_TIMEOUT below).
ORCHESTRATOR_POLL_TIMEOUT = 120
# Stdout/stderr from UI-spawned orchestrator (`_spawn_orchestrator`); tail surfaced on /api/state when down mid-flight (B-04).
ORCHESTRATOR_SPAWN_LOG_PATH = "/tmp/orchestrator.log"


# Ring buffer for synthetic events (max 50 entries)
_ring_buffer = deque(maxlen=50)

# How far into pipeline_events.jsonl the polling loop has read.
# -1 = not yet initialised; set to current EOF on first call to avoid replaying history.
_events_file_offset: int = -1

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
# Ideas UI polls readiness for 60×3s (180s); keep this window aligned (ui/index.html startReadinessPoll).
READINESS_ACTIVE_WINDOW_SECONDS = 180


def _pipeline_artifacts_dir(project_root: str | os.PathLike) -> str:
    """Resolved per-project pipeline artifact directory (matches orchestrator PROJECT_ARTIFACTS_DIR)."""
    root = os.path.realpath(os.path.expanduser(str(project_root)))
    return os.path.join(root, ".autodev", "pipeline")


def _migrate_legacy_pipeline_artifacts(repo_path: str) -> list[str]:
    """Move legacy root-level pipeline files into ``.autodev/pipeline/``. Idempotent."""
    repo_path = os.path.realpath(os.path.expanduser(repo_path))
    art = os.path.join(repo_path, ".autodev", "pipeline")
    os.makedirs(art, exist_ok=True)
    msgs: list[str] = []
    legacy_files = (
        "phase_state.json",
        "current_phase.json",
        "pipeline.json",
        "lessons.md",
        "metrics.jsonl",
        "planner_output.json",
        "executor_output.json",
        "reviewer_output.json",
        "escalation_output.json",
        "escalation_output.done",
        "pending_escalation_command.json",
        "pending_escalation_command.done",
        "failure_context.json",
        "pipeline_stop_requested",
        "escalation_summary.json",
    )
    for name in legacy_files:
        src = os.path.join(repo_path, name)
        if not os.path.lexists(src):
            continue
        dst = os.path.join(art, name)
        if os.path.lexists(dst):
            continue
        try:
            shutil.move(src, dst)
            msgs.append(f"migrated {name}")
        except OSError:
            pass
    root_phases = os.path.join(repo_path, "phases")
    dst_phases = os.path.join(art, "phases")
    if os.path.isdir(root_phases) and not os.path.lexists(dst_phases):
        try:
            shutil.move(root_phases, dst_phases)
            msgs.append("migrated phases/")
        except OSError:
            pass
    return msgs


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


def _poll_pipeline_events_file(path: str) -> list[dict]:
    """Read lines appended to the events JSONL file since the last call.

    Distinct from the async _tail_events_file generator (per-client SSE streaming).
    This function is called by _polling_loop to push new orchestrator events
    (gate_pass, gate_fail, escalation_trigger, etc.) to all connected SSE clients.

    On the first call (_events_file_offset == -1), parks at the current EOF so
    that historical events are not replayed into already-connected SSE clients.
    Detects file rotation / truncation (size < offset) and resets the offset.

    Returns a list of event dicts with the same shape as _create_synthetic_event
    output (id, ts, event_type, agent, phase, detail).  Non-JSON lines and OS
    errors are silently skipped.
    """
    global _events_file_offset
    if not path:
        return []
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    # First call: park at EOF — do not replay existing history.
    if _events_file_offset < 0:
        _events_file_offset = size
        return []
    # File was truncated or rotated.
    if size < _events_file_offset:
        _events_file_offset = 0
    # Nothing new written since last check.
    if size == _events_file_offset:
        return []
    events = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(_events_file_offset)
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    raw = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                events.append({
                    "id":         raw.get("id") or str(uuid.uuid4()),
                    "ts":         raw.get("ts") or raw.get("timestamp", ""),
                    "event_type": raw.get("event_type") or raw.get("event", "status_changed"),
                    "agent":      raw.get("agent"),
                    "phase":      raw.get("phase"),
                    "detail":     raw.get("detail"),
                })
            _events_file_offset = f.tell()
    except OSError:
        pass
    return events


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
            
            # Tail pipeline_events.jsonl for new events written by the orchestrator.
            # Delivers gate_pass, gate_fail, escalation_trigger, etc. to connected
            # SSE clients without requiring a page refresh.
            try:
                config = load_config()
                events_path = config.get("events_path")
                if events_path:
                    events_path = os.path.expanduser(events_path)
                    for evt in _poll_pipeline_events_file(events_path):
                        _ring_buffer.append(evt)
                        await _notify_sse_clients(evt)
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
    try:
        _cfg = load_config()
        if _cfg.get("auto_sync_agent_workspaces", True):
            _sync_agent_workspaces(_cfg)
    except Exception as e:
        # File-level errors are collected inside _sync_agent_workspaces; this is
        # a last-resort guard so startup always continues.
        _wlog = logging.getLogger("autodev.workspace_sync")
        _wlog.warning("WORKSPACE-SYNC startup: %s", e, exc_info=True)
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
    # Paths below are placeholders; _finalize_autodev_config_paths derives the
    # repo-local <repo>/.autodev layout (or honours AUTODEV_PIPELINE_ROOT when set).
    "pipeline_state_path": "",
    "phase_state_path": "",
    "lock_path": "",
    "events_path": "",
    "roadmap_path": "",
    "project_dir_path": "",
    "ideas_dir": "",
    "hooks_url": "http://localhost:18789/hooks/agent",
    "hooks_token": "",
    "base_branch": "",
    "conversion_prompt_path": "",
    # OPENCLAW_ROOT — OpenClaw hub (contains openclaw.json, workspace-*).
    "openclaw_root": resolve_openclaw_root(),
    "autodev_repo_path": os.environ.get("AUTODEV_REPO_PATH") or _AUTODEV_UI_ROOT,
    # AUTODEV_PIPELINE_ROOT — pipeline state directory. Empty string means
    # "derive default from repo path" in _finalize_autodev_config_paths.
    "autodev_pipeline_root": "",
    "roadmap_converter_workspace": "~/.openclaw/workspace-roadmap-converter",
    "pipeline_queue_path": "",
    "poll_timeout": 900,  # ideas-message full-turn backstop. MUST equal the POLL_TIMEOUT constant below — load_config merges DEFAULTS so this value wins in production; the constant is only the fallback for tests that omit the key. 900 (not 180) because a thorough multi-call PRD turn exceeds 180s. Guarded by tests/test_config_defaults_consistency.py
    "poll_interval": 2,
    "ideas_idle_threshold": 300,  # max stamp silence after first activity → "stalled". 300s (not 120) because a single opaque model call (PRD draft) ran 118s silent live; matches pipeline stall philosophy
    # When True, copy autodev/agents/* guidance into OPENCLAW_ROOT/workspace-* on each
    # UI server start (same mtime rules as install.sh). Set False to manage workspace
    # files only via ./install.sh (e.g. custom agent instructions).
    "auto_sync_agent_workspaces": True,
}


def _truthy_env(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


def _user_override_keys(user_config: dict) -> set[str]:
    """Keys the user meaningfully set in ui/config.json.

    Empty or whitespace-only strings are treated as unset (same as missing key) so
    config.example.json placeholders do not block _finalize_autodev_config_paths.
    Non-string values (port, booleans, numbers) always count as overrides.
    """
    out: set[str] = set()
    for k, v in user_config.items():
        if isinstance(v, str) and not v.strip():
            continue
        out.add(k)
    return out


def _finalize_autodev_config_paths(config: dict, user_override_keys: set[str]) -> None:
    """Fill runtime paths.

    Resolution order for the pipeline state directory (highest → lowest):
      1. JSON config key ``autodev_pipeline_root``.
      2. Environment variable ``AUTODEV_PIPELINE_ROOT``.
      3. ``<repo>/.autodev`` default.

    Legacy aliases (``autodev_runtime_root`` JSON key, ``AUTODEV_RUNTIME_ROOT``
    env var, ``AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME`` switch) are ignored —
    operators who need pipeline state to live next to OpenClaw set
    ``AUTODEV_PIPELINE_ROOT`` to the OpenClaw root explicitly.

    Keys in ``user_override_keys`` (from ui/config.json via ``_user_override_keys``) are never
    overwritten. Empty-string file values are excluded from that set so placeholders match defaults.
    """
    repo = config.get("autodev_repo_path") or _AUTODEV_UI_ROOT
    oc = config.get("openclaw_root") or resolve_openclaw_root()

    # 1: honour user-supplied JSON canonical key.
    explicit_json = ""
    if (
        "autodev_pipeline_root" in user_override_keys
        and config.get("autodev_pipeline_root")
    ):
        explicit_json = os.path.expanduser(str(config["autodev_pipeline_root"]))

    if explicit_json:
        runtime_base = explicit_json
    else:
        # 2/3: env var or the repo-local default.
        runtime_base = resolve_pipeline_root(repo)

    config["autodev_pipeline_root"] = runtime_base
    # Drop any legacy alias key that may have crept in from older config files.
    config.pop("autodev_runtime_root", None)

    derived = {
        "pipeline_state_path": os.path.join(runtime_base, "pipeline_state.json"),
        "lock_path": os.path.join(runtime_base, "pipeline.lock"),
        "pipeline_queue_path": os.path.join(runtime_base, "pipeline_queue.json"),
        "events_path": os.path.join(runtime_base, "pipeline_events.jsonl"),
        "ideas_dir": os.path.join(runtime_base, "ideas"),
        "project_dir_path": os.path.join(runtime_base, "pipeline-project"),
    }
    for key, val in derived.items():
        if key not in user_override_keys:
            config[key] = val

    pd = config.get("project_dir_path", "")
    if "phase_state_path" not in user_override_keys:
        config["phase_state_path"] = os.path.join(
            pd, ".autodev", "pipeline", "phase_state.json"
        )
    if "roadmap_path" not in user_override_keys:
        config["roadmap_path"] = os.path.join(pd, "roadmap.md")

    if "conversion_prompt_path" not in user_override_keys:
        config["conversion_prompt_path"] = os.path.join(
            repo, "autodev", "prompts", "prd-to-roadmap-conversion.txt"
        )

    if "roadmap_converter_workspace" not in user_override_keys:
        config["roadmap_converter_workspace"] = os.path.join(oc, "workspace-roadmap-converter")


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
    user_override_keys: set[str] = set()

    # Merge user config if exists
    if Path(config_path).exists():
        with open(config_path, 'r') as f:
            try:
                user_config = json.load(f)
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"config.json is not valid JSON ({e}). Fix or remove: {config_path}"
                ) from e
            user_override_keys = _user_override_keys(user_config)
            config.update(user_config)

    # Webhook Bearer token: env wins over file (avoid committing secrets)
    _hooks_env = os.environ.get("AUTODEV_HOOKS_TOKEN", "").strip()
    if _hooks_env:
        config["hooks_token"] = _hooks_env

    # Ideas sentinel polling: env wins over file (same pattern as hooks token).
    # (Only the post-first-activity stall threshold is tunable; the chat send
    # waits for the definitive stall/backstop verdict and does not fast-fail on
    # a pre-first-activity startup grace.)
    _idle_env = os.environ.get("AUTODEV_IDEAS_IDLE_THRESHOLD", "").strip()
    if _idle_env:
        try:
            config["ideas_idle_threshold"] = float(_idle_env)
        except ValueError:
            pass

    # Expand ~ on all string values (skip port which is int)
    for key, value in list(config.items()):
        if isinstance(value, str):
            config[key] = os.path.expanduser(value)

    _finalize_autodev_config_paths(config, user_override_keys)
    
    return config


def _idea_paths_for_messages(config: dict, idea_id: str) -> dict[str, str]:
    """Absolute paths for idea-scoped files (agent webhook instructions).

    Returned keys: ``dir``, ``prd_draft``, ``roadmap_draft``, ``roadmap_done``,
    ``verification_draft``, ``verification_done``, ``clarity_result``,
    ``clarity_done``. The ``verification_*`` paths land alongside the roadmap
    artefacts; the converter writes both in the same Mode 1 session.
    """
    root = Path(config.get("ideas_dir", ""))
    d = root / idea_id
    return {
        "dir": str(d),
        "prd_draft": str(d / "prd_draft.md"),
        "roadmap_draft": str(d / "roadmap_draft.md"),
        "roadmap_done": str(d / "roadmap_draft.done"),
        "verification_draft": str(d / "verification_draft.md"),
        "verification_done": str(d / "verification_draft.done"),
        "clarity_result": str(d / "clarity_result.json"),
        "clarity_done": str(d / "clarity_result.done"),
    }


# Canonical PRD section titles — must match ui/index.html PRD_SECTION_TITLES and parse logic.
PRD_SECTION_TITLES: tuple[str, ...] = (
    "Problem Statement",
    "Goals & Success Metrics",
    "User Stories",
    "Functional Requirements",
    "Edge Cases",
    "Non-Functional Requirements",
    "Dependencies & Integrations",
    "Milestones & Timeline",
    "Risks & Mitigations",
    "Open Questions",
    "Glossary & Domain Terms",
    "Revision History",
)


def _slugify_section(title: str) -> str:
    """Slug for API keys; matches frontend slugifySectionName()."""
    s = title.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


_PRD_SLUG_TO_TITLE: dict[str, str] = {_slugify_section(t): t for t in PRD_SECTION_TITLES}


def _normalize_prd_section_heading_text(inner: str) -> str:
    """Strip a leading ordinal prefix (e.g. '1. ' or '12.  ') from a heading title.

    Handles the common model output style '## 1. Problem Statement' so that
    the extracted inner text ('1. Problem Statement') is normalised to
    'Problem Statement' before matching against PRD_SECTION_TITLES.

    Keep in sync with the frontend parsePrdSections normalisation in ui/index.html.
    """
    return re.sub(r"^\d+\.\s*", "", inner.strip())


def _match_prd_section_heading_line(line: str) -> Optional[str]:
    """If line opens a canonical PRD section, return its title; else None."""
    m = re.match(r"^#{1,3}\s+(.+)\s*$", line)
    if m:
        raw = _normalize_prd_section_heading_text(m.group(1)).lower()
        for t in PRD_SECTION_TITLES:
            if t.lower() == raw:
                return t
    m = re.match(r"^\d+\.\s*(.+)\s*$", line)
    if m:
        raw = m.group(1).strip().lower()
        for t in PRD_SECTION_TITLES:
            if t.lower() == raw:
                return t
    return None


def _parse_prd_sections(content: str) -> dict[str, str]:
    """Split PRD markdown into canonical sections; mirrors frontend parsePrdSections()."""
    out: dict[str, str] = {t: "" for t in PRD_SECTION_TITLES}
    if not content or not content.strip():
        return out
    lines = content.split("\n")
    current: Optional[str] = None
    for line in lines:
        matched = _match_prd_section_heading_line(line)
        if matched is not None:
            current = matched
            continue
        if current:
            out[current] = f"{out[current]}\n{line}" if out[current] else line
    return out


def _snapshot_prd_draft_before_agent_write(idea_dir: Path) -> None:
    """Copy prd_draft.md to prd_draft.previous.md before the agent overwrites the draft."""
    prd_draft_path = idea_dir / "prd_draft.md"
    prd_prev_path = idea_dir / "prd_draft.previous.md"
    if prd_draft_path.exists():
        _atomic_write_file(str(prd_prev_path), prd_draft_path.read_text(encoding="utf-8"))


def _build_prd_section_diff_payload(
    current_text: str,
    previous_text: Optional[str],
) -> dict:
    """Compare parsed sections; return { sections: { slug: { title, status, previous, current } } }."""
    sections_out: dict = {}
    cur = _parse_prd_sections(current_text or "")
    if previous_text is None:
        # No previous file: every non-empty section is "added"
        for title in PRD_SECTION_TITLES:
            c = (cur.get(title) or "").strip()
            if not c:
                continue
            sk = _slugify_section(title)
            sections_out[sk] = {
                "title": title,
                "status": "added",
                "previous": None,
                "current": c,
            }
        return {"sections": sections_out}

    prev = _parse_prd_sections(previous_text)
    for title in PRD_SECTION_TITLES:
        p = (prev.get(title) or "").strip()
        c = (cur.get(title) or "").strip()
        if p == c:
            continue
        sk = _slugify_section(title)
        if not p and c:
            st = "added"
        elif p and not c:
            st = "removed"
        else:
            st = "modified"
        sections_out[sk] = {
            "title": title,
            "status": st,
            "previous": p if p else None,
            "current": c if c else None,
        }
    return {"sections": sections_out}


def _replace_prd_section_body(full_md: str, target_title: str, new_body: str) -> str:
    """Replace one canonical section's body; preserve original heading line. Append if section missing."""
    lines = full_md.split("\n") if full_md else []
    out: list[str] = []
    i = 0
    found = False
    n = len(lines)
    while i < n:
        title_here = _match_prd_section_heading_line(lines[i])
        if title_here == target_title:
            found = True
            out.append(lines[i])
            body = (new_body or "").rstrip("\n")
            if body.strip():
                out.append(body)
            i += 1
            while i < n:
                if _match_prd_section_heading_line(lines[i]) is not None:
                    break
                i += 1
            continue
        out.append(lines[i])
        i += 1
    if not found and (new_body or "").strip():
        if out and out[-1].strip():
            out.append("")
        out.append(f"## {target_title}")
        out.append((new_body or "").rstrip("\n"))
    return "\n".join(out)


def _read_conversion_prompt_text(config: dict) -> str:
    """Load conversion instructions from config path, repo default, or inline fallback."""
    p = Path(config.get("conversion_prompt_path") or "")
    if p.is_file():
        return p.read_text(encoding="utf-8")
    fb = Path(_AUTODEV_UI_ROOT) / "autodev" / "prompts" / "prd-to-roadmap-conversion.txt"
    if fb.is_file():
        return fb.read_text(encoding="utf-8")
    return (
        "Convert the PRD into a canonical AutoDev roadmap using the injected "
        "roadmap-generation skill. Do not add conversational preamble."
    )


# FastAPI app
app = FastAPI(lifespan=lifespan)
from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory="ui/static"), name="static")


@app.exception_handler(QueueVersionConflict)
async def _queue_version_conflict_handler(_request: Request, exc: QueueVersionConflict):
    """F9 — a queue mutation lost the optimistic-concurrency race past the bounded retry budget.
    This is transient (the orchestrator was writing concurrently), so surface 503 (retryable),
    not a 4xx client error. Single handler for every queue-mutation endpoint."""
    return JSONResponse(
        status_code=503,
        content={"detail": "Queue is being updated concurrently; please retry.", "error": str(exc)},
    )


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


_METRICS_MAX_ENTRIES = 10


def _record_operation_metric(op_name: str, duration_seconds: float, config: dict) -> None:
    """Append a timing entry for op_name; trim to last _METRICS_MAX_ENTRIES; atomic write."""
    try:
        ideas_dir = Path(config.get("ideas_dir") or "")
        metrics_path = Path(ideas_dir) / "operation_metrics.json"
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
        except Exception:
            existing = {}
        operations = existing.get("operations", {})
        entries = operations.get(op_name, [])
        entries.append({
            "duration_seconds": round(duration_seconds, 2),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })
        # Trim to last N entries
        if len(entries) > _METRICS_MAX_ENTRIES:
            entries = entries[-_METRICS_MAX_ENTRIES:]
        operations[op_name] = entries
        existing["operations"] = operations
        _atomic_write_json_file(metrics_path, existing)
    except Exception as exc:
        logger.warning(f"[METRICS] Failed to record metric for {op_name}: {exc}")


def _get_operation_metrics(config: dict) -> dict:
    """Return per-operation avg_seconds and sample_count from the metrics file."""
    try:
        ideas_dir = Path(config.get("ideas_dir") or "")
        metrics_path = Path(ideas_dir) / "operation_metrics.json"
        if not metrics_path.exists():
            return {}
        raw = json.loads(metrics_path.read_text())
        operations = raw.get("operations", {})
        result = {}
        for op_name, entries in operations.items():
            if entries:
                avg = sum(e.get("duration_seconds", 0) for e in entries) / len(entries)
                result[op_name] = {
                    "avg_seconds": round(avg, 1),
                    "sample_count": len(entries),
                }
        return result
    except Exception:
        return {}


def _inject_converter_skill(skill_name: str, config: dict) -> None:
    """Copy a roadmap-converter skill into the agent workspace atomically.

    Source: {autodev_repo_path}/autodev/skill-library/roadmap-converter/{skill_name}/SKILL.md
    Dest:   {roadmap_converter_workspace}/skills/{skill_name}/SKILL.md

    Raises RuntimeError if the source skill file is not found.
    Creates the destination directory if it does not exist.
    Uses mkstemp + os.replace for atomic write.
    """
    source = (
        Path(config["autodev_repo_path"])
        / "autodev"
        / "skill-library"
        / "roadmap-converter"
        / skill_name
        / "SKILL.md"
    )
    if not source.exists():
        raise RuntimeError(
            f"Skill source not found: {source}. "
            f"Expected skill '{skill_name}' in autodev/skill-library/roadmap-converter/."
        )
    dest = Path(config["roadmap_converter_workspace"]) / "skills" / skill_name / "SKILL.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = mkstemp(dir=str(dest.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(source.read_text())
        os.replace(tmp, str(dest))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
    lock_file = open(lock_path, 'a')
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


def _read_log_tail_lines(path: str, max_lines: int = 5, max_bytes: int = 65536) -> list[str]:
    """Return up to the last ``max_lines`` complete lines from a text file (bounded read from EOF).

    Used for B-04 diagnostics when the orchestrator exits while pipeline_state still shows
    an in-flight run. Missing file, empty file, and decode errors are non-fatal.
    """
    if not path or max_lines <= 0:
        return []
    max_chunk = max_bytes
    try:
        with open(path, "rb") as f:
            try:
                f.seek(0, os.SEEK_END)
                size = f.tell()
            except OSError:
                return []
            if size <= 0:
                return []
            chunk = min(max_chunk, size)
            try:
                f.seek(size - chunk, os.SEEK_SET)
            except OSError:
                return []
            raw = f.read()
    except OSError:
        return []
    if not raw:
        return []
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not lines:
        return []
    return lines[-max_lines:]


def _orchestrator_log_path(config: dict) -> str:
    """Canonical path for the orchestrator's stdout/stderr log.

    Both launchers append the orchestrator's output here — the UI's
    ``_spawn_orchestrator`` and the crash-recovery ``heartbeat_cron.py`` (whose
    ``LOG_FILE`` is ``{AUTODEV_PIPELINE_ROOT}/orchestrator.log``) — and both
    readers read it: the Pipeline log tab via ``/api/log/tail`` and the B-04
    crash-context tail in ``/api/state``. Co-locating it with pipeline state
    (``{autodev_pipeline_root}/orchestrator.log``) gives one persistent,
    reboot-surviving audit log that every party agrees on.

    Falls back to ``ORCHESTRATOR_SPAWN_LOG_PATH`` (``/tmp/orchestrator.log``)
    only when no pipeline root is configured. ``load_config`` always populates
    ``autodev_pipeline_root`` (JSON key → ``AUTODEV_PIPELINE_ROOT`` env →
    ``<repo>/.autodev``), so the fallback is reached only by callers that pass a
    bare config dict without it.
    """
    pipeline_root = (config.get("autodev_pipeline_root") or "").strip()
    if pipeline_root:
        return os.path.join(os.path.expanduser(pipeline_root), "orchestrator.log")
    return ORCHESTRATOR_SPAWN_LOG_PATH


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


# ---------------------------------------------------------------------------
# Queue helpers
# ---------------------------------------------------------------------------

def _read_queue_file(config=None):
    """Read pipeline_queue.json; returns empty structure if absent."""
    if config is None:
        config = load_config()
    path = os.path.expanduser(config.get("pipeline_queue_path") or "")
    if not os.path.exists(path):
        return {"queue": [], "queue_mode": "auto", "last_updated": ""}
    return _read_json_file(path) or {"queue": [], "queue_mode": "auto", "last_updated": ""}


def _write_queue_file(path: str, data: dict) -> None:
    """Atomic write of pipeline_queue.json (stamp next version, then _write_json_atomic).

    The atomic-replace primitive. ``bump_queue_version`` stamps ``base+1`` here (the single
    increment site); the compare-and-swap that makes concurrent UI + orchestrator writes safe
    lives in ``_mutate_queue_file`` — route queue mutations through that, not bare
    ``_write_queue_file``. See the orchestrator ``_read_queue`` docstring for the F9 model.
    """
    from datetime import datetime, timezone as tz
    bump_queue_version(data)
    data["last_updated"] = datetime.now(tz.utc).isoformat()
    _write_json_atomic(path, data)


def _peek_queue_version(config) -> int:
    """Cheap read of the on-disk queue_version (the CAS "compare" half). 0 if absent."""
    return read_queue_version(_read_queue_file(config))


def _mutate_queue_file(config, mutate_fn, *, max_retries=QUEUE_MAX_CAS_RETRIES):
    """Compare-and-swap wrapper around ``_read_queue_file``/``_write_queue_file`` for the server.

    ``mutate_fn(data)`` applies a pure, idempotent, id-keyed change to a freshly-read queue and
    returns the call's result (or raises ``QueueAbort`` to commit nothing). On exhausted
    contention ``mutate_queue`` raises ``QueueVersionConflict``; queue endpoints map that to
    HTTP 503. See ``queue_semantics.mutate_queue`` for the full contract.
    """
    path = os.path.expanduser(config.get("pipeline_queue_path") or "")
    return mutate_queue(
        lambda: _read_queue_file(config),
        lambda d: _write_queue_file(path, d),
        lambda: _peek_queue_version(config),
        mutate_fn, max_retries=max_retries,
    )


def _queue_demote_stale_active_entries(config: dict, canonical_real: str) -> bool:
    """Demote queue rows stuck in ACTIVE whose project_path realpath != *canonical*.

    ``canonical_real`` may be any path string that resolves to the canonical project
    directory (typically the same realpath as ``pipeline_state.json`` ``project_path``).

    Returns True if ``pipeline_queue.json`` was modified. No-op when *canonical_real*
    is empty or cannot be resolved (never demotes on bad input).
    """
    if not canonical_real or not str(canonical_real).strip():
        return False
    try:
        target = os.path.realpath(os.path.expanduser(str(canonical_real)))
    except OSError:
        return False

    q_path = os.path.expanduser(config.get("pipeline_queue_path") or "")
    if not q_path:
        return False

    def _apply(q):
        entries = q.get("queue", [])
        if not entries:
            raise QueueAbort()
        changed = False
        for e in entries:
            if e.get("state") != "ACTIVE":
                continue
            ep = (e.get("project_path") or "").strip()
            if not ep:
                e["state"] = "READY"
                e["started_at"] = None
                changed = True
                continue
            try:
                er = os.path.realpath(os.path.expanduser(ep))
            except OSError:
                e["state"] = "READY"
                e["started_at"] = None
                changed = True
                continue
            if er != target:
                e["state"] = "READY"
                e["started_at"] = None
                changed = True
        if not changed:
            raise QueueAbort()
        return True

    try:
        # Best-effort alignment: a CAS exhaustion (astronomically unlikely with two writers)
        # must not 503 the caller — treat it as "did not demote this cycle".
        return _mutate_queue_file(config, _apply) is True
    except QueueVersionConflict:
        return False


def _queue_demote_stale_active_from_pipeline_state(config: dict) -> bool:
    """Demote ACTIVE rows that disagree with ``pipeline_state.json`` ``project_path``."""
    ps_path = os.path.expanduser(config.get("pipeline_state_path") or "")
    if not ps_path or not os.path.exists(ps_path):
        return False
    ps = _read_json_file(ps_path) or {}
    pp = (ps.get("project_path") or "").strip()
    if not pp:
        return False
    return _queue_demote_stale_active_entries(config, pp)


def _queue_entry_realpath(e: dict) -> str | None:
    """Resolved realpath for a queue entry's ``project_path``, or None if missing/invalid."""
    ep = (e.get("project_path") or "").strip()
    if not ep:
        return None
    try:
        return os.path.realpath(os.path.expanduser(ep))
    except OSError:
        return None


def _queue_mark_matching_entry_active(config: dict, project_real: str) -> None:
    """Align queue with the orchestrator spawn path: demote stale ACTIVE, promote match, pin to front.

    Demotes any ACTIVE row whose project_path realpath differs from *project_real*,
    sets ACTIVE + ``started_at`` on matching rows, then moves matching rows to positions
    ``1..M`` (stable order), with all other rows following in their prior relative order.
    Single atomic write when anything changes.
    """
    from datetime import datetime, timezone as tz

    try:
        target = os.path.realpath(os.path.expanduser(str(project_real)))
    except OSError:
        return

    q_path = os.path.expanduser(config.get("pipeline_queue_path") or "")
    if not q_path:
        return

    now = datetime.now(tz.utc).isoformat()

    def _apply(q):
        entries = q.get("queue", [])
        if not entries:
            raise QueueAbort()
        changed = False
        for e in entries:
            if e.get("state") != "ACTIVE":
                continue
            er = _queue_entry_realpath(e)
            if er is None:
                e["state"] = "READY"
                e["started_at"] = None
                changed = True
                continue
            if er != target:
                e["state"] = "READY"
                e["started_at"] = None
                changed = True

        by_position = sorted(entries, key=lambda x: x.get("position") or 0)
        matching = []
        non_matching = []
        for e in by_position:
            er = _queue_entry_realpath(e)
            if er is not None and er == target:
                matching.append(e)
            else:
                non_matching.append(e)

        for e in matching:
            if e.get("state") != "ACTIVE":
                e["state"] = "ACTIVE"
                changed = True
            if not e.get("started_at"):
                e["started_at"] = now
                changed = True

        if matching:
            new_order = matching + non_matching
            for i, e in enumerate(new_order, start=1):
                if e.get("position") != i:
                    changed = True
                e["position"] = i
            q["queue"] = new_order
        elif changed:
            q["queue"] = by_position

        if not changed:
            raise QueueAbort()
        return True

    try:
        # Best-effort alignment — a CAS exhaustion must not 503 the spawn caller.
        _mutate_queue_file(config, _apply)
    except QueueVersionConflict:
        pass


def _queue_entries_active_first_by_pipeline_state(ordered: list, ps_real: str) -> list:
    """Stable read-only reorder: rows whose path realpath matches *ps_real* appear first."""
    if not ps_real or not ordered:
        return list(ordered)
    front, rest = [], []
    for e in ordered:
        er = _queue_entry_realpath(e)
        if er and er == ps_real:
            front.append(e)
        else:
            rest.append(e)
    return front + rest


def _merge_ingested_active_project(ordered, ps):
    """If ``pipeline_state.json`` references a project not in the queue, append a synthetic row.

    The synthetic entry is for display only (``ingested: true``, stable ``ingest-*`` id). Returns
    ``(merged_entries, did_append)``.
    """
    if not ps or not isinstance(ordered, list):
        return list(ordered or []), False
    ps_project = (ps.get("project_path") or "").strip()
    if not ps_project:
        return list(ordered), False
    try:
        ps_real = os.path.realpath(os.path.expanduser(ps_project))
    except OSError:
        return list(ordered), False
    for e in ordered:
        ep = (e.get("project_path") or "").strip()
        if not ep:
            continue
        try:
            if os.path.realpath(os.path.expanduser(ep)) == ps_real:
                return list(ordered), False
        except OSError:
            continue
    from datetime import datetime, timezone as tz
    import uuid as _uuid

    now = datetime.now(tz.utc).isoformat()
    ingest_id = f"ingest-{_uuid.uuid5(_uuid.NAMESPACE_URL, ps_real)}"
    max_pos = max((e.get("position") or 0) for e in ordered) if ordered else 0
    synth = {
        "id": ingest_id,
        "project_path": ps_project,
        "idea_id": None,
        "name": os.path.basename(ps_project.rstrip("/")) or ps_project,
        "state": "ACTIVE",
        "position": max_pos + 1,
        "parent_id": None,
        "added_at": now,
        "started_at": None,
        "completed_at": None,
        "blocked_at": None,
        "skip_count": 0,
        "preflight_validated_at": now,
        "notes": "",
        "ingested": True,
    }
    return list(ordered) + [synth], True


def _compute_dependency_tree(entries):
    """Build nested parent-child structure from flat queue list."""
    by_id = {e["id"]: dict(e, children=[]) for e in entries}
    roots = []
    for e in by_id.values():
        pid = e.get("parent_id")
        if pid and pid in by_id:
            by_id[pid]["children"].append(e)
        else:
            roots.append(e)
    return roots


def _find_next_eligible(entries):
    """Return id of first READY/SKIPPED_PENDING entry with met dependency (or no parent)."""
    state_by_id = {e["id"]: e["state"] for e in entries}
    for e in sorted(entries, key=lambda x: x["position"]):
        if e["state"] not in ("READY", "SKIPPED_PENDING"):
            continue
        if e.get("parent_id") and state_by_id.get(e["parent_id"]) != "COMPLETED":
            continue
        return e["id"]
    return None


def _detect_circular_dependency(entries, entry_id, proposed_parent_id):
    """Walk parent chain from proposed_parent_id. Return True if entry_id is reached."""
    if proposed_parent_id is None:
        return False
    if proposed_parent_id == entry_id:
        return True
    parent_by_id = {e["id"]: e.get("parent_id") for e in entries}
    current = proposed_parent_id
    visited = set()
    while current:
        if current == entry_id:
            return True
        if current in visited:
            break
        visited.add(current)
        current = parent_by_id.get(current)
    return False


def _resequence_positions(entries):
    """Sort by position and reassign 1..N with no gaps. Mutates in place, returns list."""
    entries.sort(key=lambda e: e["position"])
    for i, e in enumerate(entries, 1):
        e["position"] = i
    return entries


def _compute_display_ranks(entries):
    """Return {entry_id: int|None} — roots get sequential rank (1,2,3…), children get None."""
    sorted_entries = sorted(entries, key=lambda e: e["position"])
    ranks = {}
    rank = 0
    for e in sorted_entries:
        if not e.get("parent_id"):
            rank += 1
            ranks[e["id"]] = rank
        else:
            ranks[e["id"]] = None
    return ranks


def _get_all_descendants(entries, entry_id):
    """Return set of all descendant IDs (recursive). Does not include entry_id itself."""
    children = {e["id"] for e in entries if e.get("parent_id") == entry_id}
    result = set(children)
    for cid in list(children):
        result |= _get_all_descendants(entries, cid)
    return result


def _move_group_atomically(entries, parent_id, new_pos):
    """Move parent + all descendants as a unit to new_pos (1-based position for parent).

    Strips the group from the sorted list, inserts the group block at the target position
    among the remaining entries, then resequences all positions.
    """
    desc = _get_all_descendants(entries, parent_id)
    group_ids = {parent_id} | desc
    sorted_all = sorted(entries, key=lambda e: e["position"])
    group_block = [e for e in sorted_all if e["id"] in group_ids]
    non_group = [e for e in sorted_all if e["id"] not in group_ids]
    insert_idx = max(0, min(new_pos - 1, len(non_group)))
    final = non_group[:insert_idx] + group_block + non_group[insert_idx:]
    for i, e in enumerate(final, 1):
        e["position"] = i
    entries[:] = final
    return entries


def _validate_queue_entry_ids_order(entries, entry_ids):
    """Return None if entry_ids is a valid permutation with valid dependency layout; else an error string."""
    by_id = {e["id"]: e for e in entries}
    expected = set(by_id.keys())
    if not entry_ids:
        return "entry_ids is required"
    if len(entry_ids) != len(entries):
        return "entry_ids must include each queue entry exactly once"
    if len(set(entry_ids)) != len(entry_ids):
        return "duplicate entry id in entry_ids"
    if set(entry_ids) != expected:
        return "entry_ids must match the current queue entries"
    index_of = {uid: i for i, uid in enumerate(entry_ids)}
    for e in entries:
        pid = e.get("parent_id")
        if pid:
            if pid not in index_of:
                return "invalid parent reference in queue data"
            if index_of[pid] >= index_of[e["id"]]:
                return "parent must appear before child in the requested order"
    for eid in by_id:
        desc = _get_all_descendants(entries, eid)
        group = {eid} | desc
        indices = sorted(index_of[uid] for uid in group)
        if not indices:
            continue
        lo, hi = indices[0], indices[-1]
        if hi - lo + 1 != len(group):
            return "each subtree must appear as a contiguous block in the requested order"
        if entry_ids[lo] != eid:
            return "subtree root must lead its contiguous block in the requested order"
    return None


def _spawn_orchestrator(project_path: str, config: dict | None = None, revive_entry_id: str | None = None) -> dict:
    """Start orchestrator.py with --project-path. Returns {"ok": bool, "error": str|None}.

    When *revive_entry_id* is supplied (F2 — the dashboard 'Resume' control for a parked
    queue entry), ``--revive <id>`` is added. The orchestrator then resumes that entry via the
    revival path (restore the escalated phase + apply any banked command) instead of the
    phase-0 reset ``--project-path`` would trigger on a project switch; it falls back to
    ``--project-path`` if the entry turns out not to be revivable.

    Env construction rules (canonical names only — no legacy aliases):
      * ``OPENCLAW_ROOT`` is always set from ``config["openclaw_root"]``.
      * ``AUTODEV_PIPELINE_ROOT`` is only written when the UI config supplies a
        non-empty value. If the config is blank we **preserve** whatever the
        parent env exported — writing ``""`` over a real value was the original
        bug.
      * Legacy aliases (``AUTODEV_ROOT``, ``AUTODEV_RUNTIME_ROOT``,
        ``AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME``) are never emitted. Stale values
        carried over from the parent env are also scrubbed so downstream readers
        cannot accidentally resurrect the old layout.
    """
    import subprocess
    import sys

    if config is None:
        config = load_config()
    autodev_repo_path = config.get("autodev_repo_path") or _AUTODEV_UI_ROOT
    orchestrator_script = os.path.join(autodev_repo_path, "autodev", "pipeline", ORCHESTRATOR_FILENAME)
    if not os.path.exists(orchestrator_script):
        return {"ok": False, "error": f"{ORCHESTRATOR_FILENAME} not found at {orchestrator_script}"}
    # Write the orchestrator's stdout/stderr to the canonical pipeline-root log so
    # the Pipeline log tab (which reads the same file) streams it live. Previously
    # this opened /tmp/orchestrator.log while the reader looked in the pipeline
    # root — the two never met, so the live panel showed a stale orphan file.
    log_file = open(_orchestrator_log_path(config), "a")
    env = os.environ.copy()

    openclaw_root_value = str(config.get("openclaw_root") or resolve_openclaw_root())
    env["OPENCLAW_ROOT"] = openclaw_root_value
    env["AUTODEV_REPO_PATH"] = str(autodev_repo_path)

    pipeline_root_value = str(config.get("autodev_pipeline_root") or "").strip()
    if pipeline_root_value:
        env["AUTODEV_PIPELINE_ROOT"] = pipeline_root_value

    # Hard-cut scrub: legacy aliases must never reach the orchestrator, even if
    # they were inherited from the parent env (e.g. a stale operator shell).
    for legacy in (
        "AUTODEV_ROOT",
        "AUTODEV_RUNTIME_ROOT",
        "AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME",
    ):
        env.pop(legacy, None)

    cmd = [sys.executable, orchestrator_script, "--project-path", project_path]
    if revive_entry_id:
        cmd += ["--revive", str(revive_entry_id)]
    subprocess.Popen(
        cmd,
        cwd=autodev_repo_path,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
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
    cp = os.path.join(_pipeline_artifacts_dir(repo_abs), "current_phase.json")
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
    art = _pipeline_artifacts_dir(repo_abs)
    for n in names:
        if n not in _SWITCH_DESTRUCTIVE_WHITELIST:
            return False, f"Destructive action not allowed for {n!r}"
        p = os.path.join(art, n)
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
    if ps in ("STOPPED", "UNKNOWN", "PIPELINE_COMPLETE", "HALTED_SILENT", "BLOCKED", None):
        return True, None
    return False, ps


USER_PROJECT_PATH_BROKEN_FRIENDLY = (
    "Pipeline project folder is broken or missing — "
    "commands can't run until it's fixed."
)


def _expand_project_dir_config(config: dict) -> str | None:
    p = config.get("project_dir_path") or config.get("symlink_target") or config.get("project_dir")
    if not p or not str(p).strip():
        return None
    return os.path.expanduser(str(p))


def _project_dir_unhealthy(expanded_path: str | None) -> bool:
    """True if configured project dir is missing or symlink target is missing."""
    if not expanded_path:
        return True
    project_path = Path(expanded_path)
    try:
        if project_path.is_symlink():
            return not project_path.resolve().exists()
        return not project_path.exists()
    except OSError:
        return True


def _project_path_503_detail(expanded_path: str | None, *, dangling: bool) -> str:
    """End-user first line + newline + technical line for operators."""
    disp = expanded_path or "(not configured)"
    if dangling:
        tech = (
            f"Technical: {disp} is a symlink pointing to a folder that no longer exists. "
            "Fix: ln -sfn /path/to/your-project ~/.openclaw/pipeline-project"
        )
    else:
        tech = (
            f"Technical: project_dir_path {disp!r} does not exist. "
            "Set the correct path in ui/config.json or create the project folder."
        )
    return f"{USER_PROJECT_PATH_BROKEN_FRIENDLY}\n{tech}"


def _project_switch_allowed() -> tuple[bool, str | None]:
    """Allow switch-project when pipeline stopped, or when a configured project_dir_path is broken."""
    allowed, cur = _pipeline_allows_project_switch()
    if allowed:
        return True, None
    config = load_config()
    pdp = _expand_project_dir_config(config)
    if pdp is not None and _project_dir_unhealthy(pdp):
        return True, None
    return False, cur


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


@app.get("/api/log/tail")
def get_log_tail(lines: int = 500):
    """Return the last ``lines`` lines of the orchestrator pipeline log.

    The pipeline log lives at {autodev_pipeline_root}/orchestrator.log and
    captures stdout/stderr from the orchestrator process (spawned by the UI or
    revived by heartbeat_cron — both append to the same file via
    ``_orchestrator_log_path``). When no pipeline root is configured the writer
    falls back to ORCHESTRATOR_SPAWN_LOG_PATH (/tmp/orchestrator.log), but this
    endpoint still returns path="" in that case rather than surfacing a /tmp file.

    Returns {"lines": [...], "path": "<absolute_path_or_empty>"}.
    Missing or empty files return lines=[].
    """
    config = load_config()
    pipeline_root = (config.get("autodev_pipeline_root") or "").strip()
    if not pipeline_root:
        return {"lines": [], "path": ""}
    log_path = _orchestrator_log_path(config)
    result = _read_log_tail_lines(log_path, max_lines=lines, max_bytes=524288)
    return {"lines": result, "path": log_path}


def _compute_escalation_view(
    project_root,
    *,
    phase_state_path=None,
    queue_halted_reason=None,
    current_phase_raw_id=None,
):
    """Escalation/advisory view + eligibility probes for ONE project.

    Single source of truth shared by ``GET /api/state`` (the active symlink project) and
    ``GET /api/queue/{id}/snapshot`` (the selected queue entry). Extracting it kills the
    two-sources drift that let the Queue escalation panel describe the active project
    while dispatching commands to the selected one.

    Returns a dict with:

    * **Always-present probe keys** (filesystem/git, not phase_state fields):
      ``executor_output_exists``, ``planner_output_exists``, ``phase_branch_exists``,
      ``merge_probe_passed``. False when the project / phase id is unresolvable.
    * **Present-only phase_state keys** — included ONLY when present in the project's
      ``phase_state.json`` (mirrors ``get_state``'s ``if "x" in phase_state`` semantics so
      ``/api/state`` keeps its "absent when unset" contract): ``escalation_resets``,
      ``escalation_trigger_reason``, ``escalation_headline``, ``escalation_message``
      (fallback → trigger_reason, then ``[:500]``), ``escalation_advisory_status``,
      ``escalation_recommended_action`` (``[:200]``), ``last_error_code``,
      ``skill_injected``, ``skill_agent``, ``waiting_for_human_at``.

    Args:
        project_root: project directory (expanded + realpath'd internally). Falsy → probes
            return False and (when ``phase_state_path`` is also None) no phase_state fields.
        phase_state_path: explicit phase_state.json path. None → derived from
            ``project_root`` (the per-entry snapshot relies on this so a parked entry reads
            its OWN phase_state). For the active project the configured path and the
            derived path resolve to the same file; the override exists purely to keep
            ``/api/state`` byte-for-byte.
        queue_halted_reason: when provided (``/api/state``), applies the queue-halted
            friendly transform to ``escalation_message``. None (the snapshot) lets the raw
            message through unmodified.
        current_phase_raw_id: phase id for the branch/merge probes. None/empty → both
            probes skip gracefully (False).
    """
    view = {}

    _root_real = ""
    if project_root:
        try:
            _root_real = os.path.realpath(os.path.expanduser(str(project_root)))
        except Exception:
            _root_real = ""

    # Resolve phase_state path: explicit (active /api/state) or derived (per-entry snapshot).
    if phase_state_path is None and _root_real:
        phase_state_path = os.path.join(_pipeline_artifacts_dir(_root_real), "phase_state.json")
    elif phase_state_path:
        phase_state_path = os.path.expanduser(phase_state_path)

    # ── Present-only phase_state fields (mirror get_state's `if "x" in phase_state`) ──
    if phase_state_path:
        phase_state = _read_json_file(phase_state_path)
        if phase_state:
            if "last_error_code" in phase_state:
                view["last_error_code"] = phase_state["last_error_code"]
            if "escalation_resets" in phase_state:
                view["escalation_resets"] = phase_state["escalation_resets"]
            # P1 Stage G2 — single surfacing point for the nuclear cap counter; feeds the
            # nuclear-reset button's visibility gate in BOTH the Monitor and Queue panels.
            if "nuclear_resets" in phase_state:
                view["nuclear_resets"] = phase_state["nuclear_resets"]
            if "skill_injected" in phase_state:
                view["skill_injected"] = phase_state["skill_injected"]
            if "skill_agent" in phase_state:
                view["skill_agent"] = phase_state["skill_agent"]
            if "escalation_trigger_reason" in phase_state:
                view["escalation_trigger_reason"] = phase_state["escalation_trigger_reason"]
            # P1 Stage G1: clean, non-blame headline.
            if "escalation_headline" in phase_state:
                view["escalation_headline"] = phase_state["escalation_headline"]
            if "waiting_for_human_at" in phase_state:
                view["waiting_for_human_at"] = phase_state["waiting_for_human_at"]
            # escalation_message: dedicated field first; fall back to trigger reason.
            if "escalation_message" in phase_state:
                view["escalation_message"] = phase_state["escalation_message"]
            elif "escalation_trigger_reason" in phase_state:
                view["escalation_message"] = phase_state["escalation_trigger_reason"]
            # Never pass raw agent output through uncapped.
            if "escalation_message" in view and isinstance(view["escalation_message"], str):
                view["escalation_message"] = view["escalation_message"][:500]
            # Advisory status and recommended action (pre-alert LLM review).
            if "escalation_advisory_status" in phase_state:
                view["escalation_advisory_status"] = phase_state["escalation_advisory_status"]
            if "escalation_recommended_action" in phase_state:
                _ra = phase_state["escalation_recommended_action"]
                view["escalation_recommended_action"] = _ra[:200] if isinstance(_ra, str) else _ra

    # ── Queue-halted friendly transform (only when a halt reason is supplied) ──
    # Keep pipeline_state.queue_halted_reason as the machine token; humanize the message.
    if queue_halted_reason is not None:
        _escalation_msg = view.get("escalation_message")
        if isinstance(_escalation_msg, str) and isinstance(queue_halted_reason, str):
            if _escalation_msg.strip().lower().startswith("queue halted:"):
                _friendly = {
                    "all_blocked": (
                        "Queue halted: all queued projects are currently BLOCKED. "
                        "Unblock at least one project (or fix its blocker) to resume auto-advance."
                    ),
                    "all_dependency_hold": (
                        "Queue halted: all queued projects are in DEPENDENCY_HOLD. "
                        "Complete or clear parent dependencies to resume."
                    ),
                    "mixed": (
                        "Queue halted: remaining projects are blocked and/or dependency-held. "
                        "Resolve at least one hold/blocker to resume."
                    ),
                }.get(queue_halted_reason)
                if _friendly:
                    view["escalation_message"] = _friendly

    # ── Always-present probes: artifact existence (gate Re-run Reviewer / Mark Complete) ──
    if _root_real:
        _art = _pipeline_artifacts_dir(_root_real)
        view["executor_output_exists"] = os.path.isfile(os.path.join(_art, "executor_output.json"))
        view["planner_output_exists"] = os.path.isfile(os.path.join(_art, "planner_output.json"))
    else:
        view["executor_output_exists"] = False
        view["planner_output_exists"] = False

    _raw_id = current_phase_raw_id or ""

    # phase_branch_exists: probe refs/heads/phase/<raw_id> in the project repo.
    if _root_real and _raw_id:
        try:
            _br_result = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/phase/{_raw_id}"],
                cwd=_root_real,
                capture_output=True,
            )
            view["phase_branch_exists"] = _br_result.returncode == 0
        except Exception:
            view["phase_branch_exists"] = False
    else:
        view["phase_branch_exists"] = False

    # merge_probe_passed: probe whether phase/<raw_id> is an ancestor of the base branch.
    if _root_real and _raw_id:
        try:
            _base_branch = ""
            try:
                _base_branch = (load_config().get("base_branch") or "").strip()
            except Exception:
                _base_branch = ""
            if not _base_branch:
                _base_branch = _detect_base_branch(_root_real)
            _mp_result = subprocess.run(
                ["git", "merge-base", "--is-ancestor", f"phase/{_raw_id}", _base_branch],
                cwd=_root_real,
                capture_output=True,
            )
            view["merge_probe_passed"] = _mp_result.returncode == 0
        except Exception:
            view["merge_probe_passed"] = False
    else:
        view["merge_probe_passed"] = False

    return view


def _resolve_entry_raw_id(project_path):
    """Resolve a queue entry's OWN current phase raw_id for branch/merge probes.

    A parked entry cannot use the global ``pipeline_state.current_phase_raw_id`` — that is
    the ACTIVE project's. Reads the entry's own ``phase_state.json`` first (``current_phase_raw_id``),
    then ``current_phase.json`` (``raw_id``). Returns None when unresolvable, in which case
    the probes skip gracefully (Mark Complete shows disabled — conservative by design).
    """
    if not project_path:
        return None
    art = _pipeline_artifacts_dir(project_path)
    ph = _read_json_file(os.path.join(art, "phase_state.json"))
    if isinstance(ph, dict) and ph.get("current_phase_raw_id"):
        return ph["current_phase_raw_id"]
    cp = _read_json_file(os.path.join(art, "current_phase.json"))
    if isinstance(cp, dict) and cp.get("raw_id"):
        return cp["raw_id"]
    return None


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
            "sentinel_wait_started_at": pipeline_state.get("sentinel_wait_started_at"),
            "queue_halted_reason": pipeline_state.get("queue_halted_reason"),
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
            "queue_halted_reason": None,
            "counters": {"success": 0, "failure": 0, "retry": 0},
            "planner_retries": 0,
            "executor_retries": 0,
            "reviewer_retries": 0,
            "sentinel_wait_started_at": None,
        }
    
    # Escalation/advisory view + eligibility probes for the ACTIVE project, computed by
    # the shared _compute_escalation_view helper (same computation the Queue snapshot runs
    # for the selected entry — single source, no drift). The present-only escalation keys
    # preserve get_state's "absent when unset" contract; the 4 probe keys are always set.
    _view = _compute_escalation_view(
        config.get("project_dir_path") or "",
        phase_state_path=phase_state_path,
        queue_halted_reason=response.get("queue_halted_reason"),
        current_phase_raw_id=response.get("current_phase_raw_id"),
    )
    response.update(_view)

    # Two phase_state fields NOT in the shared helper — the Queue snapshot sources these
    # live (from pipeline_state), so they remain get_state-local here.
    if phase_state_path:
        _ps_extra = _read_json_file(phase_state_path)
        if _ps_extra:
            if "last_action_timestamp" in _ps_extra:
                response["last_action_timestamp"] = _ps_extra["last_action_timestamp"]
            if "waiting_for_human_resolved_at" in _ps_extra:
                response["waiting_for_human_resolved_at"] = _ps_extra["waiting_for_human_resolved_at"]
            # reviewer_contract_retries lives ONLY in phase_state (reviewer_retries is in
            # pipeline_state). The attempt-dot UI needs it to render reviewer contract
            # failures honestly (red, not green ✓) — CONTRACT_FAILURE never bumps
            # reviewer_retries. Default 0 so the UI's gate-on-revContract>0 honesty branch
            # is a no-op when no contract failures occurred.
            response["reviewer_contract_retries"] = _ps_extra.get("reviewer_contract_retries", 0)

    # Add server-derived fields
    # Orchestrator liveness
    if lock_path:
        try:
            response["orchestrator_alive"] = _check_orchestrator_liveness(lock_path)
        except Exception:
            response["orchestrator_alive"] = False
    else:
        response["orchestrator_alive"] = False

    tail: list[str] = []
    if not response.get("orchestrator_alive") and response.get("pipeline_status") in (
        "RUNNING",
        "WAITING_FOR_SENTINEL",
    ):
        tail = _read_log_tail_lines(_orchestrator_log_path(config), 5)
    response["orchestrator_spawn_log_tail"] = tail

    # Event source
    response["event_source"] = _determine_event_source(events_path) if events_path else "synthetic"

    # Keep project_path sourced from pipeline_state to avoid mixing status from one
    # project with symlink target from another in a single /api/state payload.
    # Expose symlink target separately for UI diagnostics.
    _symlink_path = config.get("project_dir_path") or config.get("symlink_target") or config.get("project_dir")
    if _symlink_path:
        _symlink_path = os.path.expanduser(_symlink_path)
        try:
            response["project_symlink_target"] = os.path.realpath(_symlink_path)
        except Exception:
            response["project_symlink_target"] = _symlink_path
        if not response.get("project_path"):
            response["project_path"] = response["project_symlink_target"]

    pdp_exp = _expand_project_dir_config(config)
    unhealthy = _project_dir_unhealthy(pdp_exp)
    response["project_dir_ok"] = not unhealthy
    response["project_dir_message"] = None if not unhealthy else USER_PROJECT_PATH_BROKEN_FRIENDLY

    # Setup completion marker
    _setup_marker = os.path.expanduser("~/.autodev_setup_complete")
    response["setup_complete"] = os.path.exists(_setup_marker)

    # Queue summary — non-critical; silently skip if queue file absent or unreadable
    try:
        q_path = config.get("pipeline_queue_path")
        if q_path:
            q_path_exp = os.path.expanduser(q_path)
            if os.path.exists(q_path_exp):
                q = _read_json_file(q_path_exp) or {}
                q_entries = q.get("queue", [])
                response["queue_length"] = len(q_entries)
                response["ready_count"] = sum(1 for e in q_entries if e["state"] == "READY")
                response["blocked_count"] = sum(1 for e in q_entries if e["state"] in ("BLOCKED", "ESCALATION"))
                response["completed_count"] = sum(1 for e in q_entries if e["state"] == "COMPLETED")
                response["queue_mode"] = q.get("queue_mode", "auto")
                response["queue_halted"] = response.get("pipeline_status") == "QUEUE_HALTED"
    except Exception:
        pass  # queue summary is non-critical

    # Suggested branch for git checkout recovery (same resolution as POST /api/pipeline/git-recover
    # when the client does not override — config base_branch then repo heuristics).
    _gcr_path = config.get("project_dir_path")
    _gcr_path = os.path.expanduser(_gcr_path) if _gcr_path else ""
    _gcr_real = ""
    if _gcr_path:
        try:
            _gcr_real = os.path.realpath(_gcr_path)
        except OSError:
            _gcr_real = ""
    if _gcr_real and os.path.isdir(_gcr_real):
        response["git_recover_suggested_branch"] = _detect_base_branch(
            _gcr_real, (config.get("base_branch") or "").strip()
        )
    else:
        response["git_recover_suggested_branch"] = None

    return response


# Valid commands for escalation
VALID_COMMANDS = {"RETRY", "RESET_EXECUTION", "RESET_PHASE", "RESET_REVIEWER", "SKIP", "PROCEED", "STOP", "NUCLEAR_RESET"}


RESET_CAP_COMMANDS = {"RESET_PHASE", "RESET_EXECUTION", "RESET_REVIEWER"}

# P1 Stage G2 — NUCLEAR_RESET is governed by its OWN cap (nuclear_resets), independent of
# the escalation_resets cap above. It is deliberately NOT in RESET_CAP_COMMANDS: it must
# remain available precisely when escalation_resets >= 3 (the normal recover budget is spent).
NUCLEAR_RESET_CAP = 2


def _validate_command_request(project_dir_path, pipeline_status, escalation_resets, command, nuclear_resets=0):
    """Validate command request conditions.

    Args:
        project_dir_path: Path to the project directory.
        pipeline_status: Current pipeline status.
        escalation_resets: Number of escalation resets (caps RESET_CAP_COMMANDS at 3).
        command: The command being issued.
        nuclear_resets: Number of nuclear resets (caps NUCLEAR_RESET at 2). Defaults to 0
            so callers that cannot issue NUCLEAR_RESET (e.g. the STOP-only
            /api/pipeline/stop path) need not supply it.

    Returns:
        Tuple of (is_valid, error_message, error_status_code).
        If valid, returns (True, None, None).
    """
    expanded = os.path.expanduser(str(project_dir_path)) if project_dir_path else None
    if not project_dir_path:
        return False, _project_path_503_detail(None, dangling=False), 503

    project_path = Path(project_dir_path)

    if project_path.is_symlink():
        if not project_path.resolve().exists():
            return False, _project_path_503_detail(expanded, dangling=True), 503
    elif not project_path.exists():
        return False, _project_path_503_detail(expanded, dangling=False), 503

    # Check pipeline status
    if pipeline_status != "WAITING_FOR_HUMAN":
        return False, (
            f"Pipeline is not waiting for human input (current status: {pipeline_status}). "
            "This command can only be sent when the status is WAITING FOR HUMAN."
        ), 409

    # Check reset cap — only applies to RESET_PHASE, RESET_EXECUTION, RESET_REVIEWER
    if command in RESET_CAP_COMMANDS and escalation_resets >= 3:
        return False, (
            "Reset cap reached (3/3 resets used this phase). "
            "Use PROCEED to advance past this phase or STOP to halt the pipeline."
        ), 409

    # P1 Stage G2 — independent nuclear cap. NUCLEAR_RESET is intentionally NOT gated on
    # escalation_resets here (it is available precisely when that budget is spent — the
    # escalation_resets >= 3 visibility rule is enforced UI-side); only its own cap applies.
    if command == "NUCLEAR_RESET" and nuclear_resets >= NUCLEAR_RESET_CAP:
        return False, (
            f"Nuclear reset cap reached ({NUCLEAR_RESET_CAP}/{NUCLEAR_RESET_CAP} for this phase). "
            "Use Abandon Phase to skip or Stop to halt the pipeline."
        ), 409

    return True, None, None


def _write_escalation_files(project_dir_path, command):
    """Write escalation output files atomically under ``.autodev/pipeline/``.

    Uses realpath so writes land in the symlink target when project_dir_path is a symlink.
    """
    root = os.path.realpath(os.path.expanduser(str(project_dir_path)))
    project_path = Path(_pipeline_artifacts_dir(root))
    project_path.mkdir(parents=True, exist_ok=True)
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


def _write_pending_escalation_files(project_dir_path, command):
    """Defer a command for a parked project (symlink points elsewhere). Write-then-done ordering."""
    root = os.path.realpath(os.path.expanduser(str(project_dir_path)))
    root_path = Path(root)
    if not root_path.is_dir():
        raise HTTPException(status_code=503, detail=f"Target project directory not found: {root}")
    project_path = Path(_pipeline_artifacts_dir(root))
    project_path.mkdir(parents=True, exist_ok=True)
    json_path = project_path / "pending_escalation_command.json"
    done_path = project_path / "pending_escalation_command.done"
    data = {
        "command": command,
        "source": "ui",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    fd, tmp = mkstemp(dir=str(project_path), prefix="pec_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, str(json_path))
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    with open(done_path, "w") as f:
        f.write("")
    return True


def _detect_base_branch(project_dir: str, configured_base_branch: str = "") -> str:
    """Resolve the best base branch for git recovery operations."""
    candidate = (configured_base_branch or "").strip()
    if candidate:
        return candidate

    for branch in ("main", "master", "develop", "trunk"):
        if subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=project_dir,
        ).returncode == 0:
            return branch

    remote_head = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    remote_ref = (remote_head.stdout or "").strip()
    if remote_head.returncode == 0 and remote_ref.startswith("refs/remotes/origin/"):
        return remote_ref[len("refs/remotes/origin/") :]

    init_branch = subprocess.run(
        ["git", "config", "--get", "init.defaultBranch"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    configured = (init_branch.stdout or "").strip()
    if init_branch.returncode == 0 and configured:
        return configured

    return "main"


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
    target_project_path = request.get("target_project_path")
    
    # Expand paths
    project_dir_path = os.path.expanduser(project_dir_path) if project_dir_path else None
    pipeline_state_path = os.path.expanduser(pipeline_state_path) if pipeline_state_path else None
    phase_state_path = os.path.expanduser(phase_state_path) if phase_state_path else None

    active_real = None
    if project_dir_path:
        try:
            active_real = os.path.realpath(project_dir_path)
        except OSError:
            active_real = None

    deferred_target = None
    if target_project_path:
        if not isinstance(target_project_path, str) or not target_project_path.strip():
            raise HTTPException(status_code=422, detail="target_project_path must be a non-empty string")
        try:
            tgt_real = os.path.realpath(os.path.expanduser(target_project_path.strip()))
        except OSError as e:
            raise HTTPException(status_code=422, detail=f"Invalid target_project_path: {e}") from e
        if active_real and tgt_real == active_real:
            target_project_path = None
        else:
            deferred_target = tgt_real

    if deferred_target:
        q_path = os.path.expanduser(config.get("pipeline_queue_path") or "")
        q = _read_json_file(q_path) if q_path and os.path.exists(q_path) else {}
        match = None
        for e in q.get("queue", []):
            try:
                if os.path.realpath(os.path.expanduser(e.get("project_path", ""))) == deferred_target:
                    match = e
                    break
            except OSError:
                continue
        if not match or match.get("state") not in ("ESCALATION", ESCALATION_ANSWERED):
            raise HTTPException(
                status_code=409,
                detail="Deferred command requires a parked queue entry (ESCALATION) for target_project_path.",
            )
        tgt_phase = os.path.join(_pipeline_artifacts_dir(deferred_target), "phase_state.json")
        phase_state = _read_json_file(tgt_phase) if os.path.exists(tgt_phase) else {}
        escalation_resets = phase_state.get("escalation_resets", 0) if phase_state else 0
        nuclear_resets = phase_state.get("nuclear_resets", 0) if phase_state else 0
        is_valid, error_msg, error_code = _validate_command_request(
            deferred_target, "WAITING_FOR_HUMAN", escalation_resets, command, nuclear_resets
        )
        if not is_valid:
            raise HTTPException(status_code=error_code, detail=error_msg)
        _write_pending_escalation_files(deferred_target, command)
        return {"status": "ok", "command": command, "deferred": True}

    # Read pipeline and phase state (active symlink project)
    pipeline_state = _read_json_file(pipeline_state_path) if pipeline_state_path else {}
    phase_state = _read_json_file(phase_state_path) if phase_state_path else {}
    
    pipeline_status = pipeline_state.get("pipeline_status") if pipeline_state else None
    escalation_resets = phase_state.get("escalation_resets", 0) if phase_state else 0
    nuclear_resets = phase_state.get("nuclear_resets", 0) if phase_state else 0

    # If status already moved off WAITING_FOR_HUMAN but the active queue row is parked
    # in ESCALATION for this same project, treat command as deferred. This avoids
    # monitor-screen race failures when queue-level status flips to QUEUE_HALTED.
    if pipeline_status != "WAITING_FOR_HUMAN" and active_real:
        q_path = os.path.expanduser(config.get("pipeline_queue_path") or "")
        q = _read_json_file(q_path) if q_path and os.path.exists(q_path) else {}
        for e in q.get("queue", []):
            try:
                ep = os.path.realpath(os.path.expanduser(e.get("project_path", "")))
            except OSError:
                continue
            if ep != active_real:
                continue
            if e.get("state") not in ("ESCALATION", ESCALATION_ANSWERED):
                continue
            if e.get("parked_pipeline_status") not in (None, "WAITING_FOR_HUMAN"):
                continue
            is_valid, error_msg, error_code = _validate_command_request(
                active_real, "WAITING_FOR_HUMAN", escalation_resets, command, nuclear_resets
            )
            if not is_valid:
                raise HTTPException(status_code=error_code, detail=error_msg)
            _write_pending_escalation_files(active_real, command)
            return {"status": "ok", "command": command, "deferred": True}

    # Validate request
    is_valid, error_msg, error_code = _validate_command_request(
        project_dir_path, pipeline_status, escalation_resets, command, nuclear_resets
    )
    
    if not is_valid:
        raise HTTPException(status_code=error_code, detail=error_msg)
    
    # Write escalation files
    _write_escalation_files(project_dir_path, command)
    
    return {"status": "ok", "command": command}


@app.post("/api/pipeline/git-recover")
def post_pipeline_git_recover(request: dict):
    """Attempt safe git recovery after branch-checkout failures."""
    config = load_config()
    project_dir_path = config.get("project_dir_path")
    pipeline_state_path = config.get("pipeline_state_path")

    project_dir = os.path.realpath(os.path.expanduser(project_dir_path)) if project_dir_path else ""
    if not project_dir or not os.path.isdir(project_dir):
        raise HTTPException(status_code=503, detail="Active project directory is unavailable.")

    base_branch_request = (request.get("base_branch") or "").strip()
    config_base_branch = (config.get("base_branch") or "").strip()
    base_branch = _detect_base_branch(project_dir, base_branch_request or config_base_branch)

    subprocess.run(
        ["git", "stash", "push", "--include-untracked"],
        cwd=project_dir,
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        subprocess.run(["git", "checkout", base_branch], cwd=project_dir, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or str(e)).strip()
        raise HTTPException(status_code=409, detail=f"Git recovery failed: {detail}") from e

    if pipeline_state_path:
        pipeline_state_file = os.path.expanduser(pipeline_state_path)
        state = _read_json_file(pipeline_state_file) or {}
        state["pipeline_status"] = "RUNNING"
        state["current_agent"] = "planner"
        state["last_action"] = f"Manual git recovery completed on branch {base_branch}"
        state["last_action_timestamp"] = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(pipeline_state_file, state)

    return {"ok": True, "base_branch": base_branch}


@app.post("/api/resume-ready")
def post_resume_ready():
    """Transition pipeline from STOPPED or HALTED_SILENT to WAITING_FOR_HUMAN so /api/command can be used.

    Reads pipeline_state.json, confirms pipeline_status is STOPPED or HALTED_SILENT,
    then atomically writes pipeline_status: WAITING_FOR_HUMAN + current_agent:
    escalation (all other fields preserved). This is the clean operator recovery
    from a silent halt (F11) — git-recover remains the heavy, phase-destroying
    fallback. Returns 409 if pipeline is not in STOPPED or HALTED_SILENT state.
    """
    config = load_config()
    pipeline_state_path = config.get("pipeline_state_path")
    pipeline_state_path = os.path.expanduser(pipeline_state_path) if pipeline_state_path else None

    if not pipeline_state_path:
        raise HTTPException(
            status_code=503,
            detail="pipeline_state_path is not set in config. Open Setup & Preflight to configure it.",
        )

    pipeline_state = _read_json_file(pipeline_state_path)
    if not pipeline_state:
        raise HTTPException(
            status_code=503,
            detail="Failed to read pipeline_state.json — it may be corrupt or empty. Check the file at the configured path.",
        )

    _status = pipeline_state.get("pipeline_status")
    if _status not in ("STOPPED", "HALTED_SILENT"):
        raise HTTPException(
            status_code=409,
            detail=f"Pipeline is not in a resumable state (current: {_status}). "
                   f"Resume is available from STOPPED or HALTED_SILENT.",
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


def _repoint_pipeline_project_symlink(config: dict, target_real: str) -> dict:
    """Repoint pipeline-project symlink to target_real (Policy A: pipeline_state wins).

    Does not mutate pipeline_state.json. Returns dict with keys:
    ok (bool), error (str|None), previous_symlink_real (str|None).
    """
    link_raw = (
        config.get("project_dir_path")
        or config.get("symlink_target")
        or config.get("project_dir")
    )
    link_raw = os.path.expanduser(link_raw) if link_raw else None
    if not link_raw or not str(link_raw).strip():
        return {
            "ok": False,
            "error": (
                "project_dir_path (or symlink_target / project_dir) is not set in config; "
                "cannot repoint symlink"
            ),
            "previous_symlink_real": None,
        }
    link_path = link_raw

    if not os.path.isdir(target_real):
        return {
            "ok": False,
            "error": (
                f"Target project path does not exist or is not a directory: {target_real}"
            ),
            "previous_symlink_real": None,
        }

    previous_symlink_real: str | None = None
    try:
        if os.path.lexists(link_path):
            try:
                previous_symlink_real = os.path.realpath(link_path)
            except OSError:
                previous_symlink_real = None

        if os.path.isdir(link_path) and not os.path.islink(link_path):
            return {
                "ok": False,
                "error": (
                    "project_dir_path is a real directory, not a symlink; "
                    "refusing to delete or replace"
                ),
                "previous_symlink_real": previous_symlink_real,
            }
        if os.path.isfile(link_path) and not os.path.islink(link_path):
            return {
                "ok": False,
                "error": "project_dir_path exists as a file; refusing to replace",
                "previous_symlink_real": previous_symlink_real,
            }

        parent = os.path.dirname(link_path)
        if parent:
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as exc:
                return {
                    "ok": False,
                    "error": f"Cannot create parent directory for symlink: {exc}",
                    "previous_symlink_real": previous_symlink_real,
                }

        if os.path.lexists(link_path) and os.path.islink(link_path):
            os.remove(link_path)
        os.symlink(target_real, link_path)
    except OSError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "previous_symlink_real": previous_symlink_real,
        }
    return {"ok": True, "error": None, "previous_symlink_real": previous_symlink_real}


@app.post("/api/resume-orchestrator")
def post_resume_orchestrator():
    """Spawn the orchestrator process as a non-blocking subprocess.

    Reads project_path from pipeline_state.json and autodev_repo_path from config.
    If pipeline_state.project_path disagrees with the pipeline-project symlink realpath,
    repoints the symlink to match state (Policy A), logs, then spawns.
    Returns 422 if the symlink path cannot be safely updated (e.g. real directory at link).
    Returns 409 if pipeline.lock indicates an orchestrator is already running.
    Returns 200 immediately without waiting for the orchestrator to start.
    If spawn fails after a successful repoint, returns 503 with JSON body including
    reconciled: true so the client can retry.
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

    reconciled = False
    reconcile_action = None
    previous_symlink_real_out = None

    if symlink_real and project_path:
        try:
            state_real = os.path.realpath(os.path.expanduser(str(project_path)))
        except OSError:
            state_real = str(project_path)
        if state_real != symlink_real:
            repoint = _repoint_pipeline_project_symlink(config, state_real)
            if not repoint.get("ok"):
                raise HTTPException(
                    status_code=422,
                    detail=repoint.get("error") or "Could not repoint pipeline-project symlink",
                )
            reconciled = True
            reconcile_action = "symlink_to_state"
            previous_symlink_real_out = repoint.get("previous_symlink_real")
            logger.info(
                "[RESUME] reconcile symlink_to_state prev=%s target=%s",
                previous_symlink_real_out,
                state_real,
            )
    elif symlink_real and not project_path:
        project_path = symlink_real

    if not project_path:
        raise HTTPException(status_code=503, detail="No project_path in pipeline_state.json")

    try:
        canonical_project_real = os.path.realpath(os.path.expanduser(str(project_path)))
    except OSError:
        canonical_project_real = str(project_path)

    lock_path = _expand_lock_path(config)
    if lock_path:
        try:
            if _check_orchestrator_liveness(lock_path):
                raise HTTPException(status_code=409, detail="Orchestrator is already running")
        except HTTPException:
            raise
        except Exception:
            pass

    spawned = _spawn_orchestrator(project_path, config)
    if not spawned.get("ok"):
        err_msg = spawned.get("error") or "Failed to spawn orchestrator"
        if reconciled:
            return JSONResponse(
                status_code=503,
                content={
                    "ok": False,
                    "reconciled": True,
                    "reconcile_action": "symlink_to_state",
                    "previous_symlink_real": previous_symlink_real_out,
                    "canonical_project_real": canonical_project_real,
                    "error": err_msg,
                },
            )
        raise HTTPException(status_code=503, detail=err_msg)

    try:
        _queue_mark_matching_entry_active(config, project_path)
    except Exception:
        pass

    return {
        "ok": True,
        "reconciled": reconciled,
        "reconcile_action": reconcile_action if reconciled else None,
        "previous_symlink_real": previous_symlink_real_out if reconciled else None,
        "canonical_project_real": canonical_project_real,
    }


@app.get("/api/roadmap")
def get_roadmap():
    """Get the parsed roadmap with in-progress phase identified.

    Resolves roadmap file with this priority:
    1. pipeline_state[\"project_path\"] (realpath) → _canonical_roadmap_path
    2. config [\"roadmap_path\"] (fallback: first-time setup, idle, explicit override)

    Returns a JSON array of phase objects with id, goal, status, exit_criteria,
    and behavioral_verification. ``behavioral_verification`` is either the
    structured ``{user_observable, how_to_check, failure_language}`` block or
    ``None`` for pre-P0 phases that predate the block (transitional only —
    preflight refuses to stage projects whose roadmap is missing the block).
    If pipeline_state contains current_phase_raw_id, the matching phase's status
    is overridden to 'in_progress' (taking precedence over checkbox status) when
    pipeline_status is not terminal. Returns [] when no roadmap file is found or
    the file is empty.
    """
    config = load_config()

    pipeline_state_path = config.get("pipeline_state_path")
    pipeline_state_path = os.path.expanduser(pipeline_state_path) if pipeline_state_path else None

    pipeline_state = _read_json_file(pipeline_state_path) if pipeline_state_path else None

    # Resolution: prefer pipeline_state project_path, fall back to config
    roadmap_path = None
    raw_project_path = (pipeline_state or {}).get("project_path", "")
    if raw_project_path:
        try:
            project_real = os.path.realpath(os.path.expanduser(str(raw_project_path)))
        except OSError:
            project_real = ""
        if project_real and os.path.isdir(project_real):
            roadmap_path = _canonical_roadmap_path(project_real)

    if not roadmap_path:
        roadmap_path = config.get("roadmap_path")
        roadmap_path = os.path.expanduser(roadmap_path) if roadmap_path else None

    phases = parse_roadmap(roadmap_path) if roadmap_path else []

    if not phases:
        return []

    current_phase_raw_id = (pipeline_state or {}).get("current_phase_raw_id")

    terminal_statuses = {"PIPELINE_COMPLETE", "HALTED_SILENT", "BLOCKED", "STOPPED", "QUEUE_HALTED"}
    pipeline_status = (pipeline_state or {}).get("pipeline_status", "")
    if current_phase_raw_id and pipeline_status not in terminal_statuses:
        for phase in phases:
            if phase["id"] == current_phase_raw_id:
                phase["status"] = "in_progress"
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
        "total_cost": 0.0,
        "planner_cost_total": 0.0,
        "executor_cost_total": 0.0,
        "reviewer_cost_total": 0.0,
        "total_hold_seconds": 0,
        "total_active_seconds": 0,
        "phases": [],
    }


def _parse_event_ts(ts_str: str) -> float | None:
    """Parse an ISO-8601 timestamp from pipeline_events.jsonl (UTC).

    Accepts the orchestrator's canonical ``YYYY-MM-DDTHH:MM:SSZ`` shape and
    common variants with fractional seconds or explicit ``+00:00`` offsets.
    """
    if not ts_str or not isinstance(ts_str, str):
        return None
    s = ts_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _derive_hold_seconds_per_phase(events_path: str, project_name: str) -> dict[str, int]:
    """Pair escalation_trigger → escalation_resolve events in pipeline_events.jsonl.

    Returns ``{phase_id: hold_seconds}`` for the named project. Hold time is the
    elapsed seconds between an ``escalation_trigger`` and its matching
    ``escalation_resolve`` for the same phase. Multiple pairs for the same phase
    are summed.

    Unpaired triggers (no subsequent resolve before another trigger or EOF) are
    skipped with a warning log. Rows missing a ``project`` field are skipped —
    only rows whose ``project`` matches ``project_name`` (case-sensitive,
    matching the orchestrator's symlink resolution) are considered.

    Best-effort: an unreadable file returns an empty dict. The plan documents
    that escalation paths bypassing _write_pipeline_event will be missing here.
    """
    holds: dict[str, int] = {}
    if not project_name or not events_path or not os.path.exists(events_path):
        return holds
    pending: dict[str, float] = {}  # phase -> trigger timestamp
    try:
        with open(events_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if evt.get("project") != project_name:
                    continue
                event_name = evt.get("event")
                if event_name not in ("escalation_trigger", "escalation_resolve"):
                    continue
                phase = evt.get("phase") or ""
                if not phase:
                    continue
                ts = _parse_event_ts(evt.get("ts", ""))
                if ts is None:
                    continue
                if event_name == "escalation_trigger":
                    if phase in pending:
                        print(
                            f"[WARN] _derive_hold_seconds_per_phase: unpaired "
                            f"escalation_trigger for {project_name}/{phase} "
                            f"superseded by a new trigger"
                        )
                    pending[phase] = ts
                else:  # escalation_resolve
                    start = pending.pop(phase, None)
                    if start is None:
                        continue
                    delta = int(round(ts - start))
                    if delta > 0:
                        holds[phase] = holds.get(phase, 0) + delta
    except OSError as e:
        print(f"[WARN] _derive_hold_seconds_per_phase: read failed: {e}")
        return holds
    for phase in pending:
        print(
            f"[WARN] _derive_hold_seconds_per_phase: unpaired "
            f"escalation_trigger for {project_name}/{phase} (no resolve event)"
        )
    return holds


@app.get("/api/metrics-summary")
def get_metrics_summary():
    """Return aggregated run metrics from metrics.jsonl in the project directory.

    Reads ``{project_dir_path}/.autodev/pipeline/metrics.jsonl``. Deduplicates by phase (keeps last row
    per phase, so cumulative attempt counts are correct even if a phase was reset
    and re-run). Returns sensible zeros if the file is absent or empty.

    ``total_duration_seconds`` is the SUM of per-phase ``duration_seconds`` (real
    phase work, in-phase holds included) — never run_summary.json's calendar
    wall-clock, which spans idle gaps across days and inflates the figure.
    """
    config = load_config()
    project_dir_path = config.get("project_dir_path")
    if not project_dir_path:
        return _empty_metrics_summary()

    metrics_path = Path(_pipeline_artifacts_dir(project_dir_path)) / "metrics.jsonl"
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

    total_duration_summed = sum((p.get("duration_seconds") or 0) for p in phases)
    total_executor = sum((p.get("executor_attempts") or 0) for p in phases)
    total_reviewer = sum((p.get("reviewer_passes") or 0) for p in phases)
    total_blame = sum((p.get("blame_fires") or 0) for p in phases)
    total_escalations = sum((p.get("escalations") or 0) for p in phases)

    # TOTAL TIME is the sum of per-phase wall-clock durations. Each phase's
    # duration_seconds is its phase_start→PASS span and already includes that
    # phase's in-phase escalation holds, so the sum is the real work time.
    # We deliberately do NOT consult run_summary.json's total_duration_seconds:
    # that is CALENDAR wall-clock (run_start→run_end) and spans idle nights across
    # days, inflating the figure far above actual work (svg-pic2: 74h21m calendar
    # vs 19h21m of phase work).
    total_duration = total_duration_summed

    def _role_cost(p: dict, role_key: str) -> float:
        role_obj = p.get(role_key) or {}
        if not isinstance(role_obj, dict):
            return 0.0
        try:
            return float(role_obj.get("cost_total", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _phase_cost(p: dict) -> float:
        v = p.get("cost_total", 0.0)
        try:
            return float(v or 0.0)
        except (TypeError, ValueError):
            return 0.0

    total_cost = round(sum(_phase_cost(p) for p in phases), 6)
    planner_cost_total = round(sum(_role_cost(p, "planner_tokens") for p in phases), 6)
    executor_cost_total = round(sum(_role_cost(p, "executor_tokens") for p in phases), 6)
    reviewer_cost_total = round(sum(_role_cost(p, "reviewer_tokens") for p in phases), 6)

    # Hold time: pair escalation_trigger/escalation_resolve events from the
    # pipeline-root events log, filtered by project name.
    events_path = config.get("events_path") or ""
    project_name = os.path.basename(os.path.realpath(project_dir_path)) if project_dir_path else ""
    hold_per_phase = _derive_hold_seconds_per_phase(events_path, project_name)
    total_hold_seconds = sum(hold_per_phase.values())
    # Clamp: holds from phases that never completed (e.g. a repo-init escalation
    # then STOP) are paired from the event log but contribute no summed duration,
    # so hold can exceed total — degrade to 0 active rather than go negative.
    total_active_seconds = max(0, total_duration - total_hold_seconds)

    return {
        "total_phases": len(phases),
        "total_duration_seconds": total_duration,
        "total_executor_attempts": total_executor,
        "total_reviewer_passes": total_reviewer,
        "total_blame_fires": total_blame,
        "total_escalations": total_escalations,
        "total_cost": total_cost,
        "planner_cost_total": planner_cost_total,
        "executor_cost_total": executor_cost_total,
        "reviewer_cost_total": reviewer_cost_total,
        "total_hold_seconds": total_hold_seconds,
        "total_active_seconds": total_active_seconds,
        "phases": [
            {
                "phase": p.get("phase"),
                "goal": p.get("goal"),
                "duration_seconds": p.get("duration_seconds"),
                "executor_attempts": p.get("executor_attempts", 0),
                # P0 Stage H — additive retry-source breakdown. Defaults to 0
                # for pre-Stage-H history rows so the frontend's
                # formatExecAttemptsBreakdown helper always sees numeric
                # values (undefined would render as 'NaN').
                "executor_self_failures": p.get("executor_self_failures", 0),
                "executor_reviewer_rejections": p.get(
                    "executor_reviewer_rejections", 0
                ),
                "reviewer_passes": p.get("reviewer_passes", 0),
                "blame_fires": p.get("blame_fires", 0),
                "escalations": p.get("escalations", 0),
                "skill_used": p.get("skill_used"),
                "cost_total": _phase_cost(p),
                "planner_cost": _role_cost(p, "planner_tokens"),
                "executor_cost": _role_cost(p, "executor_tokens"),
                "reviewer_cost": _role_cost(p, "reviewer_tokens"),
                "hold_seconds": hold_per_phase.get(p.get("phase"), 0),
            }
            for p in phases
        ],
    }


def _read_runs_index(pipeline_root: str) -> list[dict]:
    """Read AUTODEV_PIPELINE_ROOT/runs_index.jsonl; skip malformed lines silently."""
    index_path = os.path.join(pipeline_root, "runs_index.jsonl")
    if not os.path.exists(index_path):
        return []
    entries: list[dict] = []
    try:
        with open(index_path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return entries


def _empty_metrics_global() -> dict:
    return {
        "projects": [],
        "cross_project": {
            "total_runs": 0,
            "avg_executor_attempts": 0.0,
            "escalation_rate": 0.0,
            "skill_injection_rate": 0.0,
            "skill_vs_no_skill_executor_attempts": {"with_skill": 0.0, "without_skill": 0.0},
        },
    }


@app.get("/api/metrics-global")
def get_metrics_global():
    """Cross-project analytics aggregated from runs_index.jsonl and per-project run_summary.json.

    Reads ``AUTODEV_PIPELINE_ROOT/runs_index.jsonl`` (written by W2-B at every terminal
    pipeline exit). For each entry, loads ``<project>/.autodev/pipeline/run_summary.json``.
    Aggregates per-project stats and cross-project totals. Projects are grouped by
    ``os.path.realpath`` of ``project_path`` so index rows that differ only by trailing
    slash or symlink spelling merge. Gracefully handles missing or malformed files.
    No in-release UI consumer (W4-H deferred) — available for operator diagnostics via
    direct API call.
    """
    config = load_config()
    pipeline_root = config.get("autodev_pipeline_root") or ""
    if not pipeline_root:
        return _empty_metrics_global()

    index_entries = _read_runs_index(pipeline_root)
    if not index_entries:
        return _empty_metrics_global()

    # Group by canonical project root (realpath) so trailing-slash / symlink spellings
    # from runs_index.jsonl merge into one row (W3-D).
    by_project: dict[str, list[dict]] = {}
    for idx_row in index_entries:
        if not isinstance(idx_row, dict):
            continue
        proj_path = idx_row.get("project_path", "")
        if not proj_path:
            continue
        try:
            group_key = os.path.realpath(os.path.expanduser(str(proj_path)))
        except OSError:
            group_key = str(proj_path)
        summary_path = os.path.join(_pipeline_artifacts_dir(proj_path), "run_summary.json")
        summary = _read_json_file(summary_path) if os.path.exists(summary_path) else None
        if not isinstance(summary, dict):
            continue
        by_project.setdefault(group_key, []).append(summary)

    if not by_project:
        return _empty_metrics_global()

    projects = []
    all_phases_with_skill: list[int] = []
    all_phases_without_skill: list[int] = []

    for proj_path, summaries in by_project.items():
        runs = len(summaries)
        total_executor = sum(s.get("executor_attempts_total", 0) or 0 for s in summaries)
        total_escalations = sum(s.get("escalations_total", 0) or 0 for s in summaries)
        total_phases = sum(s.get("phases_attempted", 0) or 0 for s in summaries)
        total_skills = sum(len(s.get("skills_injected", []) or []) for s in summaries)

        # Phase-level skill vs no-skill data for cross-project comparison
        for s in summaries:
            for ph in s.get("phases", []) or []:
                if not isinstance(ph, dict):
                    continue
                attempts = ph.get("executor_attempts", 0) or 0
                if ph.get("skill_used"):
                    all_phases_with_skill.append(attempts)
                else:
                    all_phases_without_skill.append(attempts)

        # Latest run: sort by run_end timestamp; last entry wins for outcome/run_end
        sorted_sums = sorted(summaries, key=lambda x: x.get("run_end") or "")
        latest = sorted_sums[-1]

        projects.append({
            "project_name": latest.get("project_name", ""),
            "project_path": proj_path,
            "runs": runs,
            "last_outcome": latest.get("outcome"),
            "last_run_end": latest.get("run_end"),
            "avg_executor_attempts": round(total_executor / runs, 2) if runs else 0.0,
            "escalation_rate": round(total_escalations / runs, 2) if runs else 0.0,
            "skill_injection_rate": round(total_skills / total_phases, 2) if total_phases else 0.0,
            "phases_total": total_phases,
        })

    total_runs = sum(p["runs"] for p in projects)
    total_exec_sum = sum(p["avg_executor_attempts"] * p["runs"] for p in projects)
    total_esc_sum = sum(p["escalation_rate"] * p["runs"] for p in projects)
    total_skill_phases = sum(p["skill_injection_rate"] * p["phases_total"] for p in projects)
    total_phases_all = sum(p["phases_total"] for p in projects)

    skill_vs_no_skill = {
        "with_skill": round(sum(all_phases_with_skill) / len(all_phases_with_skill), 2)
            if all_phases_with_skill else 0.0,
        "without_skill": round(sum(all_phases_without_skill) / len(all_phases_without_skill), 2)
            if all_phases_without_skill else 0.0,
    }

    return {
        "projects": projects,
        "cross_project": {
            "total_runs": total_runs,
            "avg_executor_attempts": round(total_exec_sum / total_runs, 2) if total_runs else 0.0,
            "escalation_rate": round(total_esc_sum / total_runs, 2) if total_runs else 0.0,
            "skill_injection_rate": round(total_skill_phases / total_phases_all, 2)
                if total_phases_all else 0.0,
            "skill_vs_no_skill_executor_attempts": skill_vs_no_skill,
        },
    }


POLL_TIMEOUT = 900  # ideas-message turn backstop (s); patchable in tests.
# Raised 180→900 after live measurement showed a single PRD-draft model call
# can run 118s+ of opaque stamp silence; a thorough multi-call turn exceeds
# 180s.  This is the infra failsafe — the per-gap stall detector
# (ideas_idle_threshold) catches genuine hangs sooner.  Ideas-only;
# ORCHESTRATOR_POLL_TIMEOUT is separate.
POLL_INTERVAL = 2   # seconds between sentinel checks

# Tolerance when comparing .done mtime against attempt_start_wall.
# Some filesystems (NFS, FAT32) have coarse mtime resolution (1–2 seconds);
# a sentinel written at the same wall-clock instant as attempt start can have
# an mtime that is a fraction of a second behind.  2 seconds covers all known
# filesystem granularity without masking genuinely stale sentinels.
IDEAS_LATE_DONE_MTIME_SLACK_SEC: float = 2.0


IDEAS_WEBHOOK_POST_TIMEOUT = aiohttp.ClientTimeout(total=120)

# Bound the fire-and-return gateway POST for the convert / clarity-check /
# fix-roadmap-format flows. These only need to *enqueue* the agent task; the long
# wait belongs to the subsequent idle-detection poll, not the POST. Without a
# timeout a reachable-but-degraded gateway could hang the request indefinitely.
IDEAS_GATEWAY_POST_TIMEOUT = aiohttp.ClientTimeout(total=30)

IDEAS_ATTACHMENT_MAX_BYTES = 10_000_000


def _ideas_scrub_stale_turn_artifacts(idea_dir: Path, turn_n: int, attempt_start_wall: float) -> None:
    """Remove ``turns/{n}.done`` (+ paired ``{n}.md``) when the sentinel predates this attempt.

    Removes the sentinel only when its mtime predates ``attempt_start_wall`` by more than
    ``IDEAS_LATE_DONE_MTIME_SLACK_SEC`` (same boundary as ``_late_done_valid_for_attempt``).
    When ``{n}.done`` is missing, leaves ``{n}.md`` intact (in-flight prose without sentinel).
    """
    turns_dir = idea_dir / "turns"
    if not turns_dir.is_dir():
        return
    done_p = turns_dir / f"{turn_n}.done"
    md_p = turns_dir / f"{turn_n}.md"
    if not done_p.exists():
        return
    try:
        done_mtime = os.path.getmtime(done_p)
    except OSError:
        return
    if done_mtime < (attempt_start_wall - IDEAS_LATE_DONE_MTIME_SLACK_SEC):
        try:
            done_p.unlink(missing_ok=True)
            md_p.unlink(missing_ok=True)
        except OSError:
            pass


def _merge_draft_into_session_data(
    idea_dir: Path,
    session_data: dict,
    basename: str,
    session_key: str,
) -> bool:
    """Salvage helper core. Both typed wrappers below delegate here.

    If ``<basename>.md`` + ``<basename>.done`` both exist in ``idea_dir`` and
    the disk text differs (stripped) from ``session_data[session_key]``,
    mutate ``session_data`` in place and return True. Caller is responsible
    for atomic persistence on True.
    """
    draft_path = idea_dir / f"{basename}.md"
    done_path = idea_dir / f"{basename}.done"
    if not (draft_path.exists() and done_path.exists()):
        return False
    disk_text = draft_path.read_text()
    session_text = session_data.get(session_key) or ""
    if disk_text.strip() and disk_text.strip() != session_text.strip():
        session_data[session_key] = disk_text
        return True
    return False


def _merge_roadmap_draft_into_session_data(idea_dir: Path, session_data: dict) -> bool:
    """If ``roadmap_draft.md`` + ``roadmap_draft.done`` exist and disk text differs from session, sync.

    Thin wrapper over :func:`_merge_draft_into_session_data` — the shared
    salvage core. Returns True iff ``session_data`` was mutated.
    """
    return _merge_draft_into_session_data(
        idea_dir, session_data, "roadmap_draft", "roadmap_content"
    )


def _merge_verification_draft_into_session_data(idea_dir: Path, session_data: dict) -> bool:
    """If ``verification_draft.md`` + ``verification_draft.done`` exist and disk text differs from session, sync.

    Thin wrapper over :func:`_merge_draft_into_session_data`. Used by the
    salvage path on ``GET /api/ideas/{id}/session`` when the converter wrote
    its artefacts to disk after the ``/convert`` call had already returned
    (e.g. API timeout, but agent finished). Returns True iff ``session_data``
    was mutated.
    """
    return _merge_draft_into_session_data(
        idea_dir, session_data, "verification_draft", "verification_content"
    )


def _ideas_persist_data_image_to_inbound(openclaw_root: str, data_uri: str, _orig_filename: str) -> str:
    """Decode ``data:image/...;base64,...``, write under ``media/inbound``, return marker line."""
    if ";base64," not in data_uri:
        raise ValueError("expected base64 data URI")
    b64 = data_uri.split(";base64,", 1)[1].strip()
    raw = base64.b64decode(b64, validate=True)
    root = Path(os.path.expanduser(openclaw_root))
    dest_dir = root / "media" / "inbound"
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = ".png"
    m = re.match(r"data:image/([^;]+);", data_uri, re.I)
    if m:
        subtype = m.group(1).lower()
        if subtype in ("jpeg", "pjpeg"):
            ext = ".jpg"
        elif subtype == "gif":
            ext = ".gif"
        elif subtype == "webp":
            ext = ".webp"
    fname = f"{uuid.uuid4().hex}{ext}"
    (dest_dir / fname).write_bytes(raw)
    return f"[media attached: media://inbound/{fname}]"


def _mark_last_pending_assistant_error(session_path: os.PathLike | str, error_message: str) -> None:
    """Mark the last pending assistant message in session.json as failed (atomic write)."""
    sp = os.fspath(session_path)
    data = _read_json_file(sp) or {}
    msgs = data.get("messages", [])
    for _m in reversed(msgs):
        if _m.get("pending") and _m.get("role") == "assistant":
            _m["pending"] = False
            _m["error"] = True
            _m["content"] = error_message
            break
    data["messages"] = msgs
    _atomic_write_json_file(sp, data)


def _rollback_last_turn_pair(session_path: os.PathLike | str) -> None:
    """Remove the trailing user + pending-assistant pair from a session file.

    Called when the webhook POST fails (502/503) after pre-save.  Strips the
    last two messages only when they match the pre-save shape: user (with
    ``ideas_turn``) followed by a pending assistant placeholder.  If the shape
    doesn't match, the file is left unchanged (defensive — don't corrupt
    earlier history).
    """
    sp = os.fspath(session_path)
    data = _read_json_file(sp) or {}
    msgs = data.get("messages", [])
    if len(msgs) >= 2:
        asst = msgs[-1]
        user = msgs[-2]
        if (
            asst.get("role") == "assistant"
            and asst.get("pending")
            and user.get("role") == "user"
        ):
            data["messages"] = msgs[:-2]
            _atomic_write_json_file(sp, data)


async def _post_agent_webhook(hooks_url: str, hooks_token: str, webhook_payload: dict) -> None:
    """POST to OpenClaw agent hook. Raises HTTPException 503 on any aiohttp client
    error (connect failure, server timeout, or a body truncated mid-stream —
    ``ClientPayloadError``) or an asyncio timeout, and 502 on a non-2xx response.

    ``aiohttp.ClientError`` is the superclass of ``ClientConnectionError``,
    ``ServerTimeoutError`` and ``ClientPayloadError``, so catching it keeps a
    truncated-response error (gateway dies after headers) from escaping as an
    uncaught 500 that strands the "Working on your request…" placeholder."""
    headers = {"Authorization": f"Bearer {hooks_token}"}
    try:
        async with aiohttp.ClientSession(timeout=IDEAS_WEBHOOK_POST_TIMEOUT) as session:
            resp = await session.post(hooks_url, json=webhook_payload, headers=headers)
            await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Webhook connection failed: {exc}",
        ) from exc
    if not (200 <= resp.status < 300):
        raise HTTPException(
            status_code=502,
            detail=f"Webhook returned {resp.status}",
        )


def _ideas_turn_output_contract_footer(idea_id: str, turn_n: int) -> str:
    """Short per-turn reminder appended to Ideas conversational webhook bodies."""
    return (
        "\n\n[OUTPUT CONTRACT — THIS TURN]\n"
        f"- `ideas/{idea_id}/turns/{turn_n}.md` first: concise chat prose only (never the full PRD).\n"
        f"- `ideas/{idea_id}/prd_draft.md` second: full PRD or partial PRD edit.\n"
        f"- `ideas/{idea_id}/turns/{turn_n}.done` LAST, content exactly `done` (server waits on this file)."
    )


# Bounded conversation-history window injected per Ideas chat turn.
# Each new turn spawns a fresh OpenClaw session, so history must be re-injected
# inline — but unbounded injection overflows the model's input budget on long
# threads. Cap at IDEAS_HISTORY_WINDOW_TURNS pairs and a hard character budget;
# older pairs become a one-line pointer to ``conversation_log.md`` (server
# writes that file after every successful turn; agent reads it on demand).
IDEAS_HISTORY_WINDOW_TURNS = 3
IDEAS_HISTORY_TRUNCATION_MARKER = "[…truncated…]"


def _ideas_history_char_budget() -> int:
    """Read AUTODEV_IDEAS_HISTORY_CHAR_BUDGET at call time so tests can monkeypatch."""
    raw = os.environ.get("AUTODEV_IDEAS_HISTORY_CHAR_BUDGET", "").strip()
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return 20000


def _complete_pairs(prior_messages: list) -> list:
    """Walk messages, return ordered list of (user_msg, assistant_msg) tuples.

    Skips:
      - user messages flagged ``error: True`` (failed 408 timeouts)
      - assistant messages flagged ``error: True``
      - orphaned user messages with no immediate assistant follow-up
    """
    pairs: list = []
    j = 0
    n = len(prior_messages)
    while j < n:
        msg = prior_messages[j]
        if msg.get("role") == "user":
            if msg.get("error"):
                j += 1
                continue
            nxt = prior_messages[j + 1] if j + 1 < n else None
            if nxt and nxt.get("role") == "assistant" and not nxt.get("error"):
                pairs.append((msg, nxt))
                j += 2
            else:
                j += 1  # orphan
        else:
            j += 1
    return pairs


def _truncate_to_budget(text: str, budget: int) -> str:
    """Truncate ``text`` so it fits within ``budget`` chars, inserting a marker."""
    if budget <= 0 or len(text) <= budget:
        return text
    marker = f"\n{IDEAS_HISTORY_TRUNCATION_MARKER}\n"
    if budget <= len(marker):
        return text[: max(0, budget)]
    keep = budget - len(marker)
    head = keep // 2
    tail = keep - head
    return text[:head] + marker + text[-tail:] if tail else text[:head] + marker


def _append_conversation_log(
    idea_dir: Path, turn_n: int, user_content: str, agent_response: str
) -> None:
    """Append one completed (user, assistant) pair to conversation_log.md atomically.

    Server is the sole writer; the prd-creator agent only reads this file.
    Idempotent: if a ``## Turn {turn_n}`` marker already exists, the call is a no-op.
    """
    log_path = idea_dir / "conversation_log.md"
    marker = f"\n## Turn {turn_n}\n"
    existing = log_path.read_text() if log_path.exists() else ""
    if marker in existing:
        return
    block = (
        f"{marker}"
        f"### User\n{(user_content or '').strip()}\n\n"
        f"### Assistant\n{(agent_response or '').strip()}\n"
    )
    new_content = existing + block
    idea_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = mkstemp(dir=str(idea_dir), prefix=".conv_log_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(new_content)
        os.replace(tmp, str(log_path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _ensure_conversation_log_exists(idea_dir: Path, prior_messages: list) -> None:
    """Bootstrap conversation_log.md from session.json complete pairs (no-op if log exists)."""
    log_path = idea_dir / "conversation_log.md"
    if log_path.exists():
        return
    pairs = _complete_pairs(prior_messages)
    if not pairs:
        return
    parts: list = []
    for idx, (u, a) in enumerate(pairs, start=1):
        parts.append(f"\n## Turn {idx}\n")
        parts.append(f"### User\n{(u.get('content') or '').strip()}\n\n")
        parts.append(f"### Assistant\n{(a.get('content') or '').strip()}\n")
    idea_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = mkstemp(dir=str(idea_dir), prefix=".conv_log_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("".join(parts))
        os.replace(tmp, str(log_path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _build_ideas_history_block(prior_messages: list, idea_id: str) -> str:
    """Return the bounded ``[CONVERSATION HISTORY]`` block (or empty string)."""
    pairs = _complete_pairs(prior_messages)
    if not pairs:
        return ""

    omitted = 0
    if len(pairs) > IDEAS_HISTORY_WINDOW_TURNS:
        omitted = len(pairs) - IDEAS_HISTORY_WINDOW_TURNS
        pairs = pairs[-IDEAS_HISTORY_WINDOW_TURNS:]

    start_idx = omitted + 1

    def render(idx: int, u: dict, a: dict) -> str:
        return (
            f"\n[Turn {idx}]\n"
            f"User:\n{(u.get('content') or '').strip()}\n\n"
            f"Assistant:\n{(a.get('content') or '').strip()}\n"
        )

    rendered = [render(start_idx + i, u, a) for i, (u, a) in enumerate(pairs)]
    budget = _ideas_history_char_budget()
    total = sum(len(s) for s in rendered)
    while total > budget and len(rendered) > 1:
        dropped = rendered.pop(0)
        omitted += 1
        start_idx += 1
        total -= len(dropped)
    if rendered and total > budget:
        rendered[0] = _truncate_to_budget(rendered[0], budget)

    lines = ["[CONVERSATION HISTORY]"]
    if omitted > 0:
        lines.append(
            f"[NOTE] {omitted} earlier turn(s) omitted from this prompt. "
            f"Use the Read tool on ~/.openclaw/ideas/{idea_id}/conversation_log.md "
            f"if you need older context."
        )
    lines.extend(rendered)
    lines.append("\n[/CONVERSATION HISTORY]")
    return "\n".join(lines) + "\n\n"


async def _poll_sentinel_with_idle_detect(
    done_path: Path,
    stamp_path: Path,
    attempt_start_wall: float,
    poll_timeout: float,
    poll_interval: float,
    stall_threshold: float,
    startup_grace: float | None,
    rescue_stranded_reply_md: bool = True,
    extra_done_paths: tuple[Path, ...] = (),
) -> PollResult:
    """Poll for the turn ``.done`` sentinel, governed by the Tier A activity stamp.

    Mirrors the pipeline's :func:`poll_for_sentinel` two-knob design (see
    ``autodev/pipeline/sentinel_poller.py``).  The OpenClaw plugin's
    ``recordPipelineActivity`` (``autodev/plugin/src/stall-detector.ts``) touches
    ``prd_creator_activity.stamp`` on every ``model_call_started`` /
    ``model_call_ended`` / ``after_tool_call`` — its mtime is the single source
    of truth for "is the agent doing anything right now".

    Only stamp mtimes ``>= attempt_start_wall`` count as fresh activity; an
    older mtime is the residue of a prior turn and is treated as "first
    activity has not yet arrived" so a stale stamp cannot fire ``"stalled"``
    on the first poll iteration.

    Returns a :class:`PollResult` with one of four reasons:

    * ``"succeeded"``         — ``.done`` observed; happy path.
    * ``"timeout"``           — ``poll_timeout`` infrastructure backstop fired
      (gateway unreachable, plugin missing, …).
    * ``"no_first_activity"`` — ``startup_grace`` elapsed without ever seeing
      a fresh stamp.  Cold OpenClaw session failures land here.  Pass
      ``startup_grace=None`` to disable this early-fail (the Ideas chat send
      does, so a slow cold start is never reported as a premature timeout —
      only the definitive ``stalled`` / ``timeout`` verdicts can fire).
    * ``"stalled"``           — fresh stamp was seen at least once, then went
      silent for ``stall_threshold`` seconds.  This is the mid-turn-death
      signal (CORE-E6 pattern as it would manifest in Ideas).

    Two opt-in knobs let the roadmap/clarity flows reuse this helper instead of
    duplicating the poll logic (the only behaviour they need to vary):

    * ``rescue_stranded_reply_md`` (default ``True``) — when ``True`` a fresh
      ``done_path.with_suffix(".md")`` at the stall/timeout exits is surfaced as
      ``"succeeded"`` (the agent wrote the reply but not the sentinel).  This is
      valid ONLY when the ``.md`` is authored solely by the agent (the chat
      ``turns/{n}.md``).  ``post_ideas_fix_roadmap_format`` PRE-WRITES the
      malformed ``roadmap_draft.md`` before the agent runs, so it (and the other
      roadmap flows) pass ``False`` to avoid surfacing that server-co-authored
      pre-write as a completed reply.
    * ``extra_done_paths`` (default ``()``) — additional sentinels that must
      ALSO exist before ``"succeeded"`` is returned.  ``post_ideas_convert``
      produces two artefacts and passes ``(verification_draft.done,)`` so the
      poll cannot report success until both the roadmap AND verification
      sentinels have landed.

    Caller side: ``PollResult.__bool__`` delegates to ``success``, so existing
    ``if not sentinel_found:`` checks continue working unchanged.
    """
    start_mono = time.monotonic()
    attempt_start_wall_ns = int(attempt_start_wall * 1e9)
    last_fresh_mtime_ns: int | None = None  # last stamp mtime >= attempt_start_wall

    def _fresh_reply_md_present() -> bool:
        # The agent wrote turns/{n}.md (the reply) but no .done sentinel — a
        # stranded completion. Only meaningful at the stalled/timeout exits,
        # where the agent has demonstrably stopped, so the .md is final.
        md = Path(done_path).with_suffix(".md")
        try:
            return md.stat().st_mtime >= attempt_start_wall - IDEAS_LATE_DONE_MTIME_SLACK_SEC
        except OSError:
            return False

    while True:
        if Path(done_path).exists() and all(
            Path(p).exists() for p in extra_done_paths
        ):
            last_mtime_s = (
                last_fresh_mtime_ns / 1e9 if last_fresh_mtime_ns is not None else None
            )
            return PollResult(True, "succeeded", last_mtime_s)

        elapsed = time.monotonic() - start_mono

        if elapsed >= poll_timeout:
            last_mtime_s = (
                last_fresh_mtime_ns / 1e9 if last_fresh_mtime_ns is not None else None
            )
            # Backstop hit without a .done — but if the agent left a fresh reply
            # .md, surface it rather than failing the turn.  Skipped when the
            # caller co-authors the .md (rescue_stranded_reply_md=False).
            if rescue_stranded_reply_md and _fresh_reply_md_present():
                return PollResult(True, "succeeded", last_mtime_s)
            return PollResult(False, "timeout", last_mtime_s)

        # Probe the activity stamp.  Use ``st_mtime_ns`` so rapid same-second
        # touches register — the plugin's atomic tmp+rename means we can see
        # multiple writes within a single second on fast filesystems.
        try:
            cur_mtime_ns: int | None = stamp_path.stat().st_mtime_ns
        except OSError:
            cur_mtime_ns = None

        if cur_mtime_ns is not None and cur_mtime_ns >= attempt_start_wall_ns:
            if last_fresh_mtime_ns is None or cur_mtime_ns > last_fresh_mtime_ns:
                last_fresh_mtime_ns = cur_mtime_ns

        if last_fresh_mtime_ns is None:
            # Pre-first-activity window: tolerate a non-advancing stamp until
            # ``startup_grace`` elapses. ``startup_grace=None`` disables this
            # early-fail entirely — the chat send opts out so a slow cold start
            # is never declared a premature timeout; it waits for the definitive
            # stall/backstop verdict instead.
            if startup_grace is not None and elapsed >= startup_grace:
                return PollResult(False, "no_first_activity", None)
        else:
            # Post-first-activity window: ``stall_threshold`` governs.
            silence_seconds = time.time() - (last_fresh_mtime_ns / 1e9)
            if silence_seconds >= stall_threshold:
                # The agent has gone silent. If it left a fresh reply .md
                # (written but .done never landed), that output is final now —
                # surface it instead of declaring a stall.  Skipped when the
                # caller co-authors the .md (rescue_stranded_reply_md=False).
                if rescue_stranded_reply_md and _fresh_reply_md_present():
                    return PollResult(True, "succeeded", last_fresh_mtime_ns / 1e9)
                return PollResult(
                    False, "stalled", last_fresh_mtime_ns / 1e9
                )

        await asyncio.sleep(poll_interval)


def _parse_agent_response(content: str) -> dict:
    """Parse agent response content into structured components.

    QUESTIONS block: accepts ``QUESTIONS`` / ``QUESTIONS:`` and common markdown variants
    (``## QUESTIONS``, ``**QUESTIONS**``); supports ``[SINGLE]``/``[MULTI]``,
    numbered questions (``1. ...`` or ``**1. ...``), implicit question lines, and ``- `` / ``* `` options.
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
        _qhead_core = re.sub(r"^[#*\s]+|[*:\s]+$", "", qhead)

        if _qhead_core == "QUESTIONS":
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
            else:
                _numbered = stripped.lstrip("*")
                if re.match(r"^\d+[\.\)]\s+", _numbered):
                    _flush_question()
                    qtext = re.sub(r"^\d+[\.\)]\s+", "", _numbered).strip().strip("*")
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

        if not in_questions_block and drafting is None and stripped.startswith("DRAFTING:"):
            drafting = stripped[len("DRAFTING:") :].strip()
            break

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
    """Empty session schema for a brand-new idea.

    ``verification_content`` holds the project-level ``verification.md`` text
    produced by the roadmap-converter Mode 1 session alongside the roadmap.
    """
    return {
        "name": name,
        "messages": [],
        "prd_content": "",
        "roadmap_content": "",
        "verification_content": "",
        "created": None,
        "updated": None,
    }


def _iso_from_mtime(path: Path) -> str:
    return datetime.utcfromtimestamp(path.stat().st_mtime).isoformat() + "Z"


def _rehydrate_session_from_artifacts(idea_dir: Path, session_data: dict) -> tuple[dict, bool]:
    """Backfill empty session.json from ``turns/*.md``, ``prd_draft.md``, and
    ``verification_draft.md`` if available on disk.

    Returns ``(session_data, changed)``; ``changed`` is True if at least one
    field was populated from disk. Existing populated fields are never
    overwritten — backfill only fills empty slots.
    """
    if not isinstance(session_data, dict):
        session_data = _default_idea_session()
    else:
        session_data.setdefault("name", "")
        session_data.setdefault("messages", [])
        session_data.setdefault("prd_content", "")
        session_data.setdefault("roadmap_content", "")
        session_data.setdefault("verification_content", "")
        session_data.setdefault("created", None)
        session_data.setdefault("updated", None)

    has_messages = bool(session_data.get("messages"))
    has_prd = bool((session_data.get("prd_content") or "").strip())
    has_verification = bool((session_data.get("verification_content") or "").strip())
    if has_messages and has_prd and has_verification:
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

    if not has_verification:
        verification_path = idea_dir / "verification_draft.md"
        verification_done = idea_dir / "verification_draft.done"
        # Sentinel-gated: the converter writes the doc and the sentinel in
        # sequence; the doc alone may be a partial write. Match the contract
        # of ``_merge_verification_draft_into_session_data``.
        if verification_path.exists() and verification_done.exists():
            session_data["verification_content"] = verification_path.read_text()
            changed = True

    if changed:
        ts_candidates = []
        if session_data.get("messages"):
            ts_candidates.extend([m.get("ts") for m in session_data["messages"] if m.get("ts")])
        if (idea_dir / "prd_draft.md").exists():
            ts_candidates.append(_iso_from_mtime(idea_dir / "prd_draft.md"))
        if (idea_dir / "verification_draft.md").exists():
            ts_candidates.append(_iso_from_mtime(idea_dir / "verification_draft.md"))
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


def _ideas_stranded_md_reply(
    idea_dir: Path,
    turn_int: int,
    attempt_start: float,
    quiet_secs: float,
) -> str | None:
    """Reply text for a turn whose ``turns/{n}.md`` was written but ``.done`` never landed.

    The completion contract normally hinges on the ``turns/{n}.done`` sentinel
    (written last by the agent, backstopped by the plugin's ``agent_end``). When
    a run is interrupted after writing the reply ``.md`` but before the
    sentinel, the reply is stranded on disk — the poll waits the full backstop
    and ``GET /session`` never resolves the placeholder. This recovers it.

    Returns the ``.md`` content ONLY when the agent has demonstrably stopped:
    the activity stamp (``prd_creator_activity.stamp``) has been silent for at
    least ``quiet_secs``. That gate is essential — during a normal turn the
    agent writes the chat ``.md`` first, then works on ``prd_draft.md`` (a model
    call can run silently for ~2 min), then writes ``.done`` last. Surfacing on
    stamp-silence (not mere ``.md`` presence) means this never fires mid-turn
    and so cannot prematurely resolve a healthy long turn.

    Returns ``None`` when ``.done`` exists (the normal path owns that), when no
    fresh ``.md`` exists for this attempt, or when the stamp was touched within
    ``quiet_secs`` (agent may still be working).
    """
    turns_dir = idea_dir / "turns"
    if (turns_dir / f"{turn_int}.done").exists():
        return None
    md_path = turns_dir / f"{turn_int}.md"
    try:
        md_mtime = md_path.stat().st_mtime
    except OSError:
        return None
    # Stale .md from a prior attempt — not this turn's output.
    if md_mtime < attempt_start - IDEAS_LATE_DONE_MTIME_SLACK_SEC:
        return None
    stamp = idea_dir / "prd_creator_activity.stamp"
    try:
        last_activity = stamp.stat().st_mtime
    except OSError:
        # No stamp at all — the .md write itself is the last known activity.
        last_activity = md_mtime
    if (time.time() - last_activity) < quiet_secs:
        return None  # agent may still be working
    text = md_path.read_text()
    return text if text.strip() else None


def _reconcile_ideas_session_after_late_done(
    idea_dir: Path, session_data: dict, quiet_secs: float = 300.0
) -> tuple[dict, bool]:
    """Heal an unresolved last turn (``pending`` or ``error``) once its output is on disk.

    Two recovery sources, in priority order:
      1. ``turns/{n}.done`` arrived (authoritative) — the original late-done case.
      2. ``turns/{n}.md`` is stranded (``.done`` missing) and the agent has gone
         quiet for ``quiet_secs`` (see :func:`_ideas_stranded_md_reply`).

    Uses user row ``ideas_turn`` + ``attempt_start_wall`` (post–post_ideas_message schema).
    """
    if not isinstance(session_data, dict):
        return session_data, False
    msgs = session_data.get("messages")
    if not isinstance(msgs, list) or len(msgs) < 2:
        return session_data, False

    asst_idx = None
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if m.get("role") == "assistant" and (m.get("pending") or m.get("error")):
            asst_idx = i
            break
    if asst_idx is None or asst_idx < 1:
        return session_data, False

    user = msgs[asst_idx - 1]
    if user.get("role") != "user":
        return session_data, False

    turn_n = user.get("ideas_turn")
    attempt_wall = user.get("attempt_start_wall")
    if turn_n is None or attempt_wall is None:
        return session_data, False
    try:
        turn_int = int(turn_n)
        attempt_start = float(attempt_wall)
    except (TypeError, ValueError):
        return session_data, False

    turns_dir = idea_dir / "turns"
    done_path = turns_dir / f"{turn_int}.done"
    md_path = turns_dir / f"{turn_int}.md"

    agent_response = None
    if done_path.exists():
        try:
            done_mtime = os.path.getmtime(done_path)
        except OSError:
            done_mtime = None
        if done_mtime is not None and done_mtime >= attempt_start - IDEAS_LATE_DONE_MTIME_SLACK_SEC:
            agent_response = md_path.read_text() if md_path.exists() else ""
    if agent_response is None:
        # No fresh .done — recover a stranded .md if the agent has stopped.
        agent_response = _ideas_stranded_md_reply(idea_dir, turn_int, attempt_start, quiet_secs)
    if agent_response is None:
        return session_data, False
    prd_draft_path = idea_dir / "prd_draft.md"
    prd_content = prd_draft_path.read_text() if prd_draft_path.exists() else ""

    parsed = _parse_agent_response(agent_response)
    now = datetime.utcnow().isoformat() + "Z"
    asst = msgs[asst_idx]
    asst["pending"] = False
    asst["error"] = False
    asst["content"] = agent_response
    asst["ts"] = now
    asst["parsed"] = parsed

    session_data["prd_content"] = prd_content
    session_data["updated"] = now
    session_data.pop("pending_system_events", None)

    sc_notes = (user.get("sent_context") or {}).get("notes") or []
    consume_ids = {n.get("id") for n in sc_notes if n.get("id")}
    if consume_ids:
        session_data["annotations"] = [
            a
            for a in (session_data.get("annotations") or [])
            if a.get("id") not in consume_ids
        ]

    nm = session_data.get("name", "")
    if nm in ("", "New Idea"):
        heading_name = _extract_first_h1_heading(prd_content)
        if heading_name:
            session_data["name"] = heading_name
        else:
            first_user = next(
                (m["content"] for m in msgs if m.get("role") == "user"),
                "",
            )
            if first_user.strip():
                session_data["name"] = first_user.strip()[:40].title()

    return session_data, True


@app.get("/api/ideas/{idea_id}/draft-sync-status")
def get_ideas_draft_sync_status(idea_id: str):
    """Compare PRD vs roadmap draft mtimes for staleness hints (``roadmap_behind_prd``).

    Returns the mtimes of ``prd_draft.md``, ``roadmap_draft.md``, and
    ``verification_draft.md`` so the Ideas-screen UI can flag any of the
    three documents as stale relative to the PRD.
    """
    config = load_config()
    ideas_dir = Path(config.get("ideas_dir") or "")
    idea_dir = ideas_dir / idea_id
    if not idea_dir.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    prd_p = idea_dir / "prd_draft.md"
    rm_p = idea_dir / "roadmap_draft.md"
    ver_p = idea_dir / "verification_draft.md"
    prd_mtime = None
    rm_mtime = None
    ver_mtime = None
    if prd_p.exists():
        try:
            prd_mtime = os.path.getmtime(prd_p)
        except OSError:
            prd_mtime = None
    if rm_p.exists():
        try:
            rm_mtime = os.path.getmtime(rm_p)
        except OSError:
            rm_mtime = None
    if ver_p.exists():
        try:
            ver_mtime = os.path.getmtime(ver_p)
        except OSError:
            ver_mtime = None

    behind = bool(
        prd_mtime is not None
        and rm_mtime is not None
        and prd_mtime > rm_mtime
    )
    body = {
        "roadmap_behind_prd": behind,
        "prd_draft_mtime": prd_mtime,
        "roadmap_draft_mtime": rm_mtime,
        "verification_draft_mtime": ver_mtime,
    }
    return JSONResponse(content=body, headers={"Cache-Control": "no-store"})


@app.get("/api/ideas/{idea_id}/session")
def get_ideas_session(idea_id: str):
    """Return the full session.json for an idea, or empty schema if not found.

    Sets ``Cache-Control: no-store`` — PRD/roadmap content can change independently
    of the chat (agent file writes, roadmap conversion sentinel) so caching would
    show stale data.
    """
    config = load_config()
    ideas_dir = Path(config.get("ideas_dir") or "")
    idea_dir = Path(ideas_dir) / idea_id
    session_path = idea_dir / "session.json"

    if not session_path.exists():
        return JSONResponse(content=_default_idea_session(), headers={"Cache-Control": "no-store"})

    session_data = _read_json_file(str(session_path))
    if session_data is None:
        return JSONResponse(content=_default_idea_session(), headers={"Cache-Control": "no-store"})
    session_data, changed = _rehydrate_session_from_artifacts(idea_dir, session_data)
    if changed:
        _atomic_write_json_file(session_path, session_data)
    session_data, late_changed = _reconcile_ideas_session_after_late_done(
        idea_dir, session_data, quiet_secs=float(config.get("ideas_idle_threshold", 300))
    )
    if late_changed:
        _atomic_write_json_file(session_path, session_data)

    # Post-timeout salvage: if /convert returned before the converter finished
    # writing one or both drafts to disk, surface them on the next session GET.
    # Each helper is sentinel-gated (writes only when ``*.done`` is present
    # alongside ``*.md``) and idempotent (no rewrite when stripped text matches).
    if _merge_roadmap_draft_into_session_data(idea_dir, session_data):
        _atomic_write_json_file(session_path, session_data)
    if _merge_verification_draft_into_session_data(idea_dir, session_data):
        _atomic_write_json_file(session_path, session_data)

    _enrich_assistant_messages_with_parsed(session_data)
    session_data.pop("alignment_report", None)
    session_data.pop("adversarial_report", None)
    return JSONResponse(content=session_data, headers={"Cache-Control": "no-store"})


def _build_ideas_sent_context(
    unsubmitted: list,
    attachment: dict | None,
) -> dict[str, Any]:
    """Structured traceability metadata persisted on the user message (not shown as transport syntax)."""
    out: dict[str, Any] = {}
    if unsubmitted:
        out["notes"] = [
            {
                "id": a.get("id"),
                "section": a.get("section", ""),
                "comment": a.get("comment", ""),
            }
            for a in unsubmitted
        ]
    if attachment and isinstance(attachment, dict):
        fn = attachment.get("filename")
        if fn:
            out["attachment"] = {"filename": fn}
    return out


def _strip_trailing_failed_pairs(messages: list[dict]) -> list[dict]:
    """Return ``messages`` with trailing failed-turn pairs removed.

    A "failed pair" is a user message followed immediately by an assistant
    message whose ``error`` field is truthy.  This mirrors the client-side
    filter at ``ui/index.html`` that drops ``_gatewayFailed`` rows from
    ``baseMsgs`` before sending a new message, so the persisted
    ``session.json`` matches what the user sees in the chat.

    Walks backward from the end and pops trailing failed pairs (and orphan
    trailing error-only assistant bubbles) until the list ends with either
    a non-error item or an in-progress user message with no following
    assistant.  Non-trailing failed pairs are preserved — by user policy,
    mid-conversation error history is kept so operators can audit it after
    the fact (see ``CHANGELOG.md`` entry).

    The helper is pure: it returns a new list and never mutates the input.

    Invoked by :func:`post_ideas_message` once per chat turn, between the
    ``pre_session`` read and the pre-save write, so the cleanup runs exactly
    when the user is about to add a new turn (which is the user-observable
    moment at which they expect the prior failure to disappear).
    """
    result = list(messages)
    while result:
        last = result[-1]
        if last.get("role") == "assistant" and last.get("error"):
            result.pop()
            if result and result[-1].get("role") == "user":
                result.pop()
            continue
        break
    return result


def _ideas_timeout_message(reason: str | None, poll_timeout: float) -> str:
    """Map a chat-poll ``PollResult.reason`` to user-facing, reason-specific copy.

    ``_poll_sentinel_with_idle_detect`` already knows WHY a turn failed; this
    turns that into honest guidance instead of a blanket "model may be slow".

    **Sole author of the message text.** The 408 response body, the persisted
    session placeholder, and (via the response body) the frontend all use this
    one string — the wording is not duplicated in ``ui/index.html``. This is the
    deliberate single-source design that avoids the dual-source drift which bit
    the timeout *values* (see ``tests/test_config_defaults_consistency.py``).

    The chat send waits for a DEFINITIVE verdict (it passes
    ``startup_grace=None``), so only these two reasons reach this mapper:

    * ``stalled`` — the agent was active then went silent past the stall
      threshold; the model most likely stalled mid-response.
    * ``timeout`` — the full ``poll_timeout`` infra backstop elapsed without the
      turn finishing (this also covers "never produced any activity"); the
      request may be too large or the model/gateway very slow.
    * anything else / ``None`` (incl. a legacy ``no_first_activity``) — fall
      back to the original generic copy.
    """
    if reason == "stalled":
        return (
            "The agent began working but went quiet partway through — the model "
            "likely stalled mid-response. Retrying usually clears it."
        )
    if reason == "timeout":
        minutes = max(1, int(poll_timeout) // 60)
        return (
            f"The agent ran for ~{minutes} min without finishing — the request "
            "may be too large or the model very slow. Try a shorter message or retry."
        )
    return "Agent timed out — the model may be slow. You can retry."


def _late_done_valid_for_attempt(done_path: Path | str, attempt_start_wall: float) -> bool:
    """True when the turn sentinel exists and was written at or after this attempt started.

    Uses ``IDEAS_LATE_DONE_MTIME_SLACK_SEC`` tolerance so coarse-grained filesystems
    (NFS, FAT32) don't reject a sentinel written at the same wall-clock instant.

    Used after poll timeout to avoid restoring draft annotations when the agent completed
    just after the idle/timeout window (race with late .done write).

    This is the **authoritative late-recovery path** for Ideas turns and is
    deliberately decoupled from the Tier A activity stamp polled by
    :func:`_poll_sentinel_with_idle_detect` — a late ``.done`` is enough on its
    own; a stale stamp from a prior turn cannot block reconciliation here.
    Do not consolidate this with the stamp poller in a future refactor.
    """
    p = Path(done_path)
    if not p.exists():
        return False
    try:
        return os.path.getmtime(p) >= (attempt_start_wall - IDEAS_LATE_DONE_MTIME_SLACK_SEC)
    except OSError:
        return False


async def _trigger_readiness_assessment(idea_id: str, config: dict) -> None:
    """Fire non-blocking readiness webhook; deletes prior readiness.done first."""
    _active_readiness_jobs.add(idea_id)
    _readiness_job_started_at[idea_id] = datetime.utcnow().timestamp()
    logger.info(f"[READINESS] Triggering assessment for idea {idea_id}")
    try:
        ideas_dir = Path(config.get("ideas_dir") or "")
        sentinel = ideas_dir / idea_id / "readiness.done"
        sentinel.unlink(missing_ok=True)
        # Clear a stale error artifact from a prior failed run so it cannot shadow
        # this fresh attempt's in-progress / success state.
        (ideas_dir / idea_id / "readiness_error.json").unlink(missing_ok=True)
        hooks_url = config.get("hooks_url", "http://localhost:18789/hooks/agent")
        hooks_token = config.get("hooks_token", "")
        ip = _idea_paths_for_messages(config, idea_id)
        payload = {
            "agentId": WEBHOOK_AGENT_ID,
            "sessionKey": f"ideas:{idea_id}:readiness",
            "wakeMode": "now",
            # File-only run: the agent writes readiness.json/.done. Without this the
            # gateway tries to deliver the reply to the bound Signal channel and the
            # run is marked errored ("Delivering to Signal requires target").
            "deliver": False,
            "message": (
                f"[SESSION] ideas:{idea_id}:readiness\n\n"
                f"A new PRD draft is available. Read {ip['prd_draft']} and produce an "
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
            if resp.status >= 400:
                error_path = ideas_dir / idea_id / "readiness_error.json"
                _atomic_write_json_file(
                    str(error_path),
                    {"error": f"webhook returned {resp.status}", "idea_id": idea_id},
                )
                return
    except Exception as exc:
        if isinstance(exc, asyncio.TimeoutError):
            logger.warning(f"[READINESS] Assessment timed out for {idea_id}")
        else:
            logger.error(f"[READINESS] Webhook failed for {idea_id}: {exc}")
        # Mirror the HTTP>=400 branch on a connection-level failure so the readiness
        # panel can distinguish "assessment infra unavailable" from "no assessment
        # yet" / "PRD not ready". Without this, a gateway-down readiness run (auto-
        # fired every chat turn) only logs and GET /readiness reads as "unavailable".
        try:
            error_path = ideas_dir / idea_id / "readiness_error.json"
            _atomic_write_json_file(
                str(error_path),
                {"error": f"webhook connection failed: {exc}", "idea_id": idea_id},
            )
        except Exception as werr:
            logger.error(f"[READINESS] Failed to write readiness_error.json for {idea_id}: {werr}")
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

    ideas_dir = Path(config.get("ideas_dir") or "")
    idea_dir = Path(ideas_dir) / idea_id
    hooks_url = config.get("hooks_url", "http://localhost:18789/hooks/agent")
    hooks_token = config.get("hooks_token", "")
    openclaw_root = os.path.expanduser(config.get("openclaw_root", "~/.openclaw"))

    # Optional attachment: size cap; data:image URIs → OPENCLAW_ROOT/media/inbound + marker
    message_content = content
    if attachment and isinstance(attachment, dict):
        fcontent = attachment.get("content", "") or ""
        if not isinstance(fcontent, str):
            fcontent = str(fcontent)
        if len(fcontent) > IDEAS_ATTACHMENT_MAX_BYTES:
            raise HTTPException(
                status_code=422,
                detail=f"Attachment exceeds {IDEAS_ATTACHMENT_MAX_BYTES} byte limit",
            )
        ft = fcontent.strip()
        if ft.startswith("data:image/") and ";base64," in ft:
            try:
                orig_fn = attachment.get("filename", "image.png") or "image.png"
                marker = _ideas_persist_data_image_to_inbound(openclaw_root, ft, orig_fn)
                message_content = f"{marker}\n\n{content}"
            except (ValueError, OSError, binascii.Error):
                raise HTTPException(status_code=422, detail="Invalid image attachment") from None
        else:
            fname = attachment.get("filename", "attachment.md")
            message_content = f"[ATTACHMENT: {fname}]\n{fcontent}\n[/ATTACHMENT]\n\n{content}"

    # Load existing session data early — used for annotations and conversation history
    idea_dir = Path(ideas_dir) / idea_id
    session_path_pre = idea_dir / "session.json"
    pre_session: dict = {}
    if session_path_pre.exists():
        pre_session = _read_json_file(str(session_path_pre)) or {}

    # Drop trailing failed-turn pairs (user + assistant-with-error) before
    # appending the new turn — keeps session.json in sync with what the
    # client already shows after its `_gatewayFailed` filter, so a browser
    # refresh after a 408/502/503 retry does not re-surface stacked red
    # bubbles.  Non-trailing errors are preserved as conversation history.
    if pre_session.get("messages"):
        pre_session["messages"] = _strip_trailing_failed_pairs(pre_session["messages"])

    # Inject unsubmitted annotations into message context
    pending_annotation_ids: list[str] = []
    unsubmitted = [a for a in pre_session.get("annotations", []) if not a.get("submitted")]
    if unsubmitted:
        ann_lines = "\n".join(f'Section "{a["section"]}": "{a["comment"]}"' for a in unsubmitted)
        message_content = f"[USER ANNOTATIONS]\n{ann_lines}\n[/USER ANNOTATIONS]\n\n{message_content}"
        pending_annotation_ids = [a["id"] for a in unsubmitted]

    sent_context = _build_ideas_sent_context(unsubmitted, attachment)

    # Conversation history: bounded sliding window prepended to the prompt,
    # plus a server-maintained ``conversation_log.md`` the agent can Read on
    # demand for older context. Older pairs collapse to one [NOTE] line.
    prior_messages = pre_session.get("messages", [])
    _ensure_conversation_log_exists(idea_dir, prior_messages)
    history_block = _build_ideas_history_block(prior_messages, idea_id)

    # Consume any pending system events (alignment/adversarial check results)
    # stored by previous check endpoints. Injected here so the PRD agent sees
    # them in context without requiring a competing session-1 webhook.
    pending_events = pre_session.get("pending_system_events", [])
    system_events_block = ""
    if pending_events:
        lines = ["[SYSTEM EVENTS]"]
        lines.extend(pending_events)
        lines.append("[/SYSTEM EVENTS]")
        system_events_block = "\n".join(lines) + "\n\n"

    # Build session key: ideas:{id}:session-{n}
    session_key = f"ideas:{idea_id}:session-{turn_n}"

    # Webhook payload — first line MUST be [SESSION] for agent output path parsing (AGENTS.md)
    _contract_footer = _ideas_turn_output_contract_footer(idea_id, int(turn_n))
    webhook_payload = {
        "agentId": WEBHOOK_AGENT_ID,
        "sessionKey": session_key,
        "wakeMode": "now",
        # File-only run; reply is read from the workspace, never delivered to Signal.
        "deliver": False,
        "message": (
            f"[SESSION] ideas:{idea_id}:session-{turn_n}\n\n"
            f"{history_block}{system_events_block}{message_content}{_contract_footer}"
        ),
    }

    # Wall-clock start of this attempt — compared to .done mtime after poll timeout
    # to detect late completion (agent wrote sentinel just after idle/timeout).
    _attempt_start_wall = time.time()

    # Pre-save user message to session.json BEFORE sending webhook.
    # This ensures the user's message survives even if the poll times out (408).
    # On refresh, the UI will show the user's message with an error placeholder
    # instead of losing it entirely.
    session_path = idea_dir / "session.json"
    _pre_save_ts = datetime.utcnow().isoformat() + "Z"
    _pre_save_data = dict(pre_session)
    _pre_save_data.setdefault("messages", [])
    _user_pre_row: dict[str, Any] = {
        "role": "user",
        "content": content,
        "ts": _pre_save_ts,
        "ideas_turn": turn_n,
        "attempt_start_wall": _attempt_start_wall,
    }
    if sent_context:
        _user_pre_row["sent_context"] = sent_context
    _pre_save_data["messages"] = list(_pre_save_data["messages"]) + [
        _user_pre_row,
        {"role": "assistant", "content": "Working on your request...", "ts": _pre_save_ts, "pending": True},
    ]
    _pre_save_data["updated"] = _pre_save_ts
    if _pre_save_data.get("created") is None:
        _pre_save_data["created"] = _pre_save_ts
    _atomic_write_json_file(str(session_path), _pre_save_data)

    _snapshot_prd_draft_before_agent_write(idea_dir)

    poll_timeout = float(config.get("poll_timeout", POLL_TIMEOUT))
    poll_interval = float(config.get("poll_interval", POLL_INTERVAL))
    idle_threshold = float(config.get("ideas_idle_threshold", 300))

    try:
        await _post_agent_webhook(hooks_url, hooks_token, webhook_payload)
    except HTTPException as exc:
        # Webhook failed before the agent saw the message — roll back the
        # pre-saved user + pending-assistant pair so the session stays clean.
        # The user can retry without a ghost "Working on your request..." row.
        if exc.status_code in (502, 503):
            _rollback_last_turn_pair(session_path)
        raise

    turns_dir = idea_dir / "turns"
    # Sentinel paths per ~/.openclaw/workspace-prd-creator/AGENTS.md: turns/{n}.md / turns/{n}.done
    done_path = turns_dir / f"{turn_n}.done"
    md_path = turns_dir / f"{turn_n}.md"
    prd_draft_path = idea_dir / "prd_draft.md"

    sentinel_found = await _poll_sentinel_with_idle_detect(
        done_path=done_path,
        stamp_path=idea_dir / "prd_creator_activity.stamp",
        attempt_start_wall=_attempt_start_wall,
        poll_timeout=poll_timeout,
        poll_interval=poll_interval,
        stall_threshold=idle_threshold,
        # The chat send waits for a DEFINITIVE timeout signal — a mid-response
        # ``stalled`` (after first activity) or the hard ``poll_timeout``
        # backstop. It deliberately does NOT fast-fail on startup grace: a 408
        # only reaches the client after the webhook POST was accepted, so "no
        # activity stamp yet" is almost always a slow cold start, not a dead
        # turn. ``startup_grace=None`` disables the premature ``no_first_activity``.
        startup_grace=None,
    )
    if not sentinel_found and _late_done_valid_for_attempt(done_path, _attempt_start_wall):
        sentinel_found = True

    if not sentinel_found:
        # Timed out / agent idle — surface WHY (PollResult.reason) instead of a
        # blanket message. The same string lands in the persisted placeholder
        # (shown on refresh) and the 408 body (shown immediately), so the UI
        # renders one authoritative message — see _ideas_timeout_message.
        _timeout_reason = getattr(sentinel_found, "reason", None)
        _timeout_msg = _ideas_timeout_message(_timeout_reason, poll_timeout)
        _timeout_data = _read_json_file(str(session_path)) or _pre_save_data
        _timeout_msgs = _timeout_data.get("messages", [])
        for _m in reversed(_timeout_msgs):
            if _m.get("pending"):
                _m["pending"] = False
                _m["error"] = True
                _m["content"] = _timeout_msg
                break
        _timeout_data["messages"] = _timeout_msgs
        _atomic_write_json_file(str(session_path), _timeout_data)
        raise HTTPException(
            status_code=408,
            detail={"reason": _timeout_reason or "timeout", "message": _timeout_msg},
        )

    # Read agent response
    agent_response = ""
    if md_path.exists():
        agent_response = md_path.read_text()

    # Read updated prd_content from prd_draft.md
    prd_content = ""
    if prd_draft_path.exists():
        prd_content = prd_draft_path.read_text()

    # Re-read session.json (may have been updated by readiness job between pre-save and now).
    # The pre-saved messages already include the user message and a pending assistant placeholder.
    # Replace the pending placeholder with the real agent response.
    if session_path.exists():
        session_data = _read_json_file(str(session_path)) or dict(_pre_save_data)
    else:
        session_data = dict(_pre_save_data)

    parsed = _parse_agent_response(agent_response)

    # Replace the pending assistant placeholder with the real response.
    # If no placeholder found (unexpected), append a new assistant entry.
    now = datetime.utcnow().isoformat() + "Z"
    session_data.setdefault("messages", [])
    replaced = False
    for _m in reversed(session_data["messages"]):
        if _m.get("pending") and _m.get("role") == "assistant":
            _m["pending"] = False
            _m["content"] = agent_response
            _m["ts"] = now
            _m["parsed"] = parsed
            replaced = True
            break
    if not replaced:
        # Fallback: append both messages (handles case where pre-save was skipped/lost)
        _ufb: dict[str, Any] = {
            "role": "user",
            "content": content,
            "ts": now,
            "ideas_turn": turn_n,
            "attempt_start_wall": _attempt_start_wall,
        }
        if sent_context:
            _ufb["sent_context"] = sent_context
        session_data["messages"].append(_ufb)
        session_data["messages"].append(
            {"role": "assistant", "content": agent_response, "ts": now, "parsed": parsed}
        )

    session_data["prd_content"] = prd_content
    session_data["updated"] = now
    if session_data.get("created") is None:
        session_data["created"] = now

    # Clear consumed system events so they aren't re-injected on subsequent turns
    session_data.pop("pending_system_events", None)

    # Remove consumed draft annotations (fresh notes can be added for the same section later)
    if pending_annotation_ids:
        session_data["annotations"] = [
            a
            for a in (session_data.get("annotations") or [])
            if a.get("id") not in pending_annotation_ids
        ]

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

    # post_ideas_message has prior unconditional mutations (assistant response,
    # possible name extraction above) so session.json must be written below
    # regardless of whether the roadmap merge fires. Bool return intentionally
    # ignored. See plans/upcomming/FUTURE-ENHANCEMENTS.md →
    # "Audit _merge_*_into_session_data bool-return contract" for the L3
    # API-design question deferred from P0 Stage I.
    _merge_roadmap_draft_into_session_data(idea_dir, session_data)

    _atomic_write_json_file(str(session_path), session_data)

    # Append this completed turn to the server-owned conversation log so the
    # agent can Read it on future turns when older context is needed. Best
    # effort: log failure must not turn a successful turn into an HTTP error.
    try:
        _append_conversation_log(idea_dir, int(turn_n), content, agent_response)
    except OSError:
        logger.warning("conversation_log append failed for idea=%s turn=%s",
                       idea_id, turn_n, exc_info=True)

    _readiness_job_started_at[idea_id] = datetime.utcnow().timestamp()
    asyncio.create_task(_trigger_readiness_assessment(idea_id, config))

    out_body: dict[str, Any] = {
        "response": agent_response,
        "prd_content": prd_content,
        "parsed": parsed,
    }
    if sent_context:
        out_body["sent_context"] = sent_context
    return out_body


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
    ideas_dir = Path(config.get("ideas_dir") or "")
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
    ideas_dir = Path(config.get("ideas_dir") or "")
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
    ideas_dir = Path(config.get("ideas_dir") or "")
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
    ideas_dir = Path(config.get("ideas_dir") or "")
    idea_dir = Path(ideas_dir) / idea_id
    if not idea_dir.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    session_data = _load_session_for_idea(idea_dir)
    return session_data.get("annotations", [])


@app.get("/api/ideas/{idea_id}/prd-section-diff")
def get_ideas_prd_section_diff(idea_id: str):
    """Section-level diff between prd_draft.md and prd_draft.previous.md."""
    config = load_config()
    ideas_dir = Path(config.get("ideas_dir") or "")
    idea_dir = Path(ideas_dir) / idea_id
    if not idea_dir.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    cur_path = idea_dir / "prd_draft.md"
    prev_path = idea_dir / "prd_draft.previous.md"
    if not cur_path.exists() and not prev_path.exists():
        return {"sections": {}}

    cur_text = cur_path.read_text(encoding="utf-8") if cur_path.exists() else ""
    prev_text: Optional[str]
    if prev_path.exists():
        prev_text = prev_path.read_text(encoding="utf-8")
    else:
        prev_text = None

    return _build_prd_section_diff_payload(cur_text, prev_text)


@app.post("/api/ideas/{idea_id}/prd-section-revert")
async def post_ideas_prd_section_revert(idea_id: str, request: Request):
    """Swap one section between prd_draft.md and prd_draft.previous.md (one-level revert)."""
    config = load_config()
    body = await request.json()
    section_key = (body.get("section_key") or "").strip()
    if not section_key:
        raise HTTPException(status_code=422, detail="Body must contain {section_key: str}")

    title = _PRD_SLUG_TO_TITLE.get(section_key)
    if title is None:
        raise HTTPException(status_code=422, detail="Unknown section_key")

    ideas_dir = Path(config.get("ideas_dir") or "")
    idea_dir = Path(ideas_dir) / idea_id
    if not idea_dir.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    cur_path = idea_dir / "prd_draft.md"
    prev_path = idea_dir / "prd_draft.previous.md"
    if not cur_path.exists() or not prev_path.exists():
        raise HTTPException(
            status_code=404,
            detail="prd_draft.md and prd_draft.previous.md are both required for revert",
        )

    cur_doc = cur_path.read_text(encoding="utf-8")
    prev_doc = prev_path.read_text(encoding="utf-8")
    cur_parsed = _parse_prd_sections(cur_doc)
    prev_parsed = _parse_prd_sections(prev_doc)
    cur_body = cur_parsed.get(title) or ""
    prev_body = prev_parsed.get(title) or ""

    new_cur = _replace_prd_section_body(cur_doc, title, prev_body)
    new_prev = _replace_prd_section_body(prev_doc, title, cur_body)

    _atomic_write_file(str(cur_path), new_cur)
    _atomic_write_file(str(prev_path), new_prev)

    session_path = idea_dir / "session.json"
    if session_path.exists():
        session_data = _read_json_file(str(session_path)) or {}
        session_data["prd_content"] = new_cur
        session_data["updated"] = datetime.utcnow().isoformat() + "Z"
        _atomic_write_json_file(str(session_path), session_data)

    prev_trim = prev_body.strip()
    return {"section_key": section_key, "content": prev_trim}


@app.get("/api/ideas/operation-metrics")
def get_ideas_operation_metrics():
    """Return average duration and sample count per operation type.

    Returns a dict of operation_name -> {"avg_seconds": float, "sample_count": int}.
    Operations with no history are absent from the response.
    """
    config = load_config()
    return _get_operation_metrics(config)


@app.get("/api/ideas")
def get_ideas():
    """List all idea documents.

    Returns:
        JSON array of {id, name, summary, updated, readiness_score, has_prd,
        has_roadmap} objects, sorted newest-first.
        Returns [] if ideas_dir is absent or empty.
    """
    config = load_config()
    ideas_dir = Path(config.get("ideas_dir") or "")
    ideas_path = Path(ideas_dir)

    if not ideas_path.exists():
        return JSONResponse(content=[], headers={"Cache-Control": "no-store"})

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
        if _merge_roadmap_draft_into_session_data(subdir, session_data):
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

        readiness_score = None
        readiness_path = subdir / "readiness.json"
        if readiness_path.exists():
            try:
                rd = _read_json_file(str(readiness_path))
                if isinstance(rd, dict):
                    v = rd.get("score")
                    if isinstance(v, (int, float)):
                        readiness_score = int(v)
            except Exception:
                pass

        has_prd = bool((session_data.get("prd_content") or "").strip())
        has_roadmap = bool((session_data.get("roadmap_content") or "").strip())

        ideas.append({
            "id": subdir.name,
            "name": name,
            "summary": summary,
            "updated": updated,
            "readiness_score": readiness_score,
            "has_prd": has_prd,
            "has_roadmap": has_roadmap,
        })

    # Sort newest first by updated timestamp
    ideas.sort(key=lambda x: x.get("updated") or "", reverse=True)
    return JSONResponse(content=ideas, headers={"Cache-Control": "no-store"})


@app.post("/api/ideas")
def post_ideas():
    """Create a new idea document.

    Creates {ideas_dir}/{uuid}/session.json with empty schema.
    Returns {"id": <uuid>}.
    """
    config = load_config()
    ideas_dir = Path(config.get("ideas_dir") or "")
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


class RenameIdeaRequest(BaseModel):
    name: str


@app.patch("/api/ideas/{idea_id}")
def patch_ideas(idea_id: str, body: RenameIdeaRequest):
    """Rename an idea document.

    Body: {"name": str}
    Returns 404 if idea does not exist, 400 for empty/whitespace-only names.
    """
    config = load_config()
    ideas_dir = Path(config.get("ideas_dir") or "")
    idea_path = Path(ideas_dir) / idea_id
    session_path = idea_path / "session.json"

    if not idea_path.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    new_name = (body.name or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Idea name cannot be empty")

    session_data = _read_json_file(str(session_path)) if session_path.exists() else _default_idea_session()
    session_data["name"] = new_name
    session_data["updated"] = datetime.utcnow().isoformat() + "Z"
    _atomic_write_json_file(session_path, session_data)
    return {"ok": True, "id": idea_id, "name": new_name}


@app.delete("/api/ideas/{idea_id}")
def delete_ideas(idea_id: str):
    """Delete an idea document and all its contents.

    Returns 404 if the idea directory does not exist.
    """
    import shutil
    config = load_config()
    ideas_dir = Path(config.get("ideas_dir") or "")
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
    ideas_dir = Path(config.get("ideas_dir") or "")
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
            # Sanitize: replace spaces with hyphens, strip non-ASCII/non-safe characters
            # HTTP header values must be latin-1 encodable
            import re as _re
            filename = heading.replace(" ", "-")
            filename = _re.sub(r"[^\w\-.]", "", filename)
            break

    filename = (filename or idea_id) + "-prd.md"

    from fastapi.responses import Response
    return Response(
        content=prd_content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/ideas/{idea_id}/clarity-check")
async def post_ideas_clarity_check(idea_id: str):
    """Trigger the PRD clarity check agent and poll for its result.

    Reads current prd_content from session.json, sends a webhook POST to
    hooks_url, then waits via the shared idle-detection poll
    (``_poll_sentinel_with_idle_detect`` on ``prd_creator_activity.stamp``):
    ``CLARITY_TIMEOUT`` infra backstop, ``ideas_idle_threshold`` stall window.
    Returns the contents of clarity_result.json on success, 504 on a stall or
    backstop. The result is ephemeral (not persisted), so a late result is
    simply re-run by the caller.
    """
    config = load_config()
    ideas_dir = Path(config.get("ideas_dir") or "")
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

    ip = _idea_paths_for_messages(config, idea_id)
    # Build webhook payload
    timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
    session_key = f"ideas:{idea_id}:clarity-{timestamp_ms}"
    webhook_payload = {
        "agentId": WEBHOOK_AGENT_ID,
        "sessionKey": session_key,
        "wakeMode": "now",
        # File-only run; reply is read from the workspace, never delivered to Signal.
        "deliver": False,
        "message": (
            "Review the following PRD for clarity and completeness. "
            "Do not write or modify any files other than clarity_result.json and clarity_result.done listed below. "
            "Analyze whether all essential sections are present and well-formed. "
            f"Write a JSON object to {ip['clarity_result']} with schema "
            '{"pass": bool, "missing_sections": [str], "issues": [str]}, '
            f"then create {ip['clarity_done']}.\n\n"
            f"PRD CONTENT:\n{prd_content}"
        ),
    }

    # Send webhook POST
    _attempt_start_wall = time.time()
    headers = {"Authorization": f"Bearer {hooks_token}"}
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            hooks_url, json=webhook_payload, headers=headers,
            timeout=IDEAS_GATEWAY_POST_TIMEOUT,
        )
        if resp.status >= 400:
            raise HTTPException(status_code=502, detail=f"Webhook returned {resp.status}")

    # Idle-detection poll on the shared activity stamp — same machinery as the
    # chat send (replaces the prior 60 s hard cap, which sat below the measured
    # 118 s single-call floor and could false-time-out a healthy run).
    # clarity_result is JSON with no sibling .md, so the stranded-.md rescue is
    # moot but disabled explicitly. ``startup_grace=None`` waits for a definitive
    # stall/backstop verdict. The result is ephemeral (returned inline, not
    # persisted), so a late result is simply re-run by the caller — there is no
    # durable artefact to salvage, unlike the roadmap flows.
    done_path = idea_dir / "clarity_result.done"
    result_path = idea_dir / "clarity_result.json"
    idle_threshold = float(config.get("ideas_idle_threshold", 300))
    poll_result = await _poll_sentinel_with_idle_detect(
        done_path=done_path,
        stamp_path=idea_dir / "prd_creator_activity.stamp",
        attempt_start_wall=_attempt_start_wall,
        poll_timeout=CLARITY_TIMEOUT,
        poll_interval=CLARITY_POLL_INTERVAL,
        stall_threshold=idle_threshold,
        startup_grace=None,
        rescue_stranded_reply_md=False,
    )
    if not poll_result:
        raise HTTPException(
            status_code=504,
            detail=f"Clarity check timed out after {CLARITY_TIMEOUT}s",
        )

    if not result_path.exists():
        raise HTTPException(status_code=500, detail="clarity_result.done exists but clarity_result.json is missing")

    result_data = _read_json_file(str(result_path))
    if result_data is None:
        raise HTTPException(status_code=500, detail="clarity_result.json is not valid JSON")

    return result_data


CONVERT_TIMEOUT = 480   # seconds; patchable in tests. Bumped from 300 in P0
                        # Stage B9 — the converter now produces both
                        # roadmap_draft.md AND verification_draft.md in the
                        # same session, which needs ~60% more headroom.
CONVERT_POLL_INTERVAL = 2  # seconds between sentinel checks
FORMAT_CORRECTION_TIMEOUT = 600  # infra backstop (s); patchable in tests. Was a
                        # 180 s hard cap; now the idle-detection stall threshold
                        # (ideas_idle_threshold) is the primary failure signal and
                        # this is only the gateway-dead failsafe, so it must clear
                        # one stall window comfortably (mirrors POLL_TIMEOUT).
FORMAT_CORRECTION_POLL_INTERVAL = 2  # seconds between sentinel checks
CLARITY_TIMEOUT = 600   # infra backstop (s); patchable in tests. Was a 60 s hard
                        # cap — below the measured 118 s single-call floor, so it
                        # could false-time-out a healthy clarity run. Idle
                        # detection (ideas_idle_threshold) is now the primary
                        # signal; this is only the gateway-dead failsafe.
CLARITY_POLL_INTERVAL = 2  # seconds between sentinel checks


@app.get("/api/ideas/{idea_id}/readiness")
def get_idea_readiness(idea_id: str):
    """Serve agent-written readiness.json; status reflects sentinel + JSON validity."""
    config = load_config()
    ideas_dir = Path(config.get("ideas_dir") or "")
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

    # No success sentinel — if the last run failed at the infra level it left a
    # readiness_error.json (T2.6). A fresh run clears that file first, so its
    # presence here means the most recent attempt failed and is done; surface a
    # distinct "error" state so the UI can say "assessment infra unavailable"
    # rather than the ambiguous "unavailable".
    error_path = idea_dir / "readiness_error.json"
    if error_path.exists():
        _active_readiness_jobs.discard(idea_id)
        _readiness_job_started_at.pop(idea_id, None)
        err = _read_json_file(str(error_path)) or {}
        logger.debug(f"[READINESS] Status for {idea_id}: error")
        return {"status": "error", "data": None, "error": err.get("error")}

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
    """Lightweight poll for readiness.done sentinel.

    Also reports ``error: True`` when the run failed at the infra level
    (``readiness_error.json`` present, T2.6) so the frontend treats it as a
    terminal outcome — stop polling and fetch ``GET /readiness`` for the error
    status — exactly as it does on ``done``."""
    config = load_config()
    ideas_dir = Path(config.get("ideas_dir") or "")
    idea_dir = ideas_dir / idea_id
    if not idea_dir.exists():
        raise HTTPException(status_code=404, detail="Idea not found")
    return {
        "done": (idea_dir / "readiness.done").exists(),
        "error": (idea_dir / "readiness_error.json").exists(),
    }


@app.post("/api/ideas/{idea_id}/convert")
async def post_ideas_convert(idea_id: str):
    """Trigger PRD-to-roadmap conversion.

    Injects the roadmap-generation skill, then sends a webhook to the
    roadmap-converter agent. The agent produces TWO artefacts in the same
    session: ``roadmap_draft.md`` and ``verification_draft.md`` (the
    project-level Verification document). This endpoint waits via the shared
    idle-detection poll (``_poll_sentinel_with_idle_detect``) requiring BOTH
    sentinels — ``roadmap_draft.done`` AND ``verification_draft.done`` — via
    ``extra_done_paths``: ``ideas_idle_threshold`` is the stall window and
    ``CONVERT_TIMEOUT`` the infra backstop. On success it atomically stores both
    contents in session.json and returns them. A late pair (agent finishes after
    a stall/backstop) is salvaged on the next ``GET /session`` and the frontend
    recovery poll, so output is never lost.

    Returns 404 if the idea is not found.
    Returns 422 if prd_content is empty.
    Returns 408 if polling times out (either sentinel missing).
    Returns 200 with {"roadmap_content": str, "verification_content": str} on success.
    """
    config = load_config()
    ideas_dir = Path(config.get("ideas_dir", ""))
    hooks_url = config.get("hooks_url", "http://localhost:18789/hooks/agent")
    hooks_token = config.get("hooks_token", "")

    idea_dir = ideas_dir / idea_id
    if not idea_dir.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    session_path = idea_dir / "session.json"
    session_data = _read_json_file(str(session_path)) or {}
    prd_content = session_data.get("prd_content", "") or ""

    if not prd_content:
        raise HTTPException(status_code=422, detail="No prd_content to convert")

    conversion_prompt = _read_conversion_prompt_text(config)

    # Inject roadmap-generation skill before webhook POST
    _inject_converter_skill("roadmap-generation", config)

    ip = _idea_paths_for_messages(config, idea_id)
    # Build webhook payload
    timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
    session_key = f"ideas:{idea_id}:convert-{timestamp_ms}"
    webhook_payload = {
        "agentId": ROADMAP_CONVERTER_AGENT_ID,
        "sessionKey": session_key,
        "wakeMode": "now",
        # File-only run; reply is read from the workspace, never delivered to Signal.
        "deliver": False,
        "message": (
            f"{conversion_prompt.strip()}\n\n"
            f"---\n\n"
            f"{prd_content}\n\n"
            f"Write the resulting roadmap.md content to {ip['roadmap_draft']}.\n"
            f"Write the project-level verification.md content to {ip['verification_draft']}.\n"
            f"Then create {ip['verification_done']} (verification sentinel) FIRST.\n"
            f"Then create {ip['roadmap_done']} (roadmap sentinel) LAST.\n"
        ),
    }

    # Send webhook POST
    _attempt_start_wall = time.time()
    headers = {"Authorization": f"Bearer {hooks_token}"}
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            hooks_url, json=webhook_payload, headers=headers,
            timeout=IDEAS_GATEWAY_POST_TIMEOUT,
        )
        if resp.status >= 400:
            raise HTTPException(status_code=502, detail=f"Webhook returned {resp.status}")

    done_path = idea_dir / "roadmap_draft.done"
    verification_done_path = idea_dir / "verification_draft.done"
    # Scrub stale sentinels from a prior run so the poll's existence checks below
    # cannot latch onto them — this unlink subsumes the freshness guard the prior
    # loop used (same role as the orchestrator's min_sentinel_mtime capture).
    done_path.unlink(missing_ok=True)
    verification_done_path.unlink(missing_ok=True)

    # Idle-detection poll on the shared activity stamp — same machinery as the
    # chat send. The converter writes TWO artefacts, so success requires BOTH
    # sentinels (``extra_done_paths``); the agent co-authors roadmap_draft.md so
    # the stranded-.md rescue is disabled. ``startup_grace=None`` waits for a
    # definitive verdict; CONVERT_TIMEOUT is the infra backstop and
    # ideas_idle_threshold the primary stall signal. A late pair after a
    # stall/backstop is salvaged on GET /session (the _merge_*_draft helpers) and
    # surfaced by the frontend recovery poll, so late output is never lost.
    idle_threshold = float(config.get("ideas_idle_threshold", 300))
    poll_result = await _poll_sentinel_with_idle_detect(
        done_path=done_path,
        stamp_path=idea_dir / "prd_creator_activity.stamp",
        attempt_start_wall=_attempt_start_wall,
        poll_timeout=CONVERT_TIMEOUT,
        poll_interval=CONVERT_POLL_INTERVAL,
        stall_threshold=idle_threshold,
        startup_grace=None,
        rescue_stranded_reply_md=False,
        extra_done_paths=(verification_done_path,),
    )
    if not poll_result:
        raise HTTPException(
            status_code=408,
            detail=f"Conversion timed out after {CONVERT_TIMEOUT}s"
        )

    _record_operation_metric("roadmap_generation", time.time() - _attempt_start_wall, config)

    # Read both contents
    roadmap_draft_path = idea_dir / "roadmap_draft.md"
    verification_draft_path = idea_dir / "verification_draft.md"
    roadmap_content = roadmap_draft_path.read_text() if roadmap_draft_path.exists() else ""
    verification_content = (
        verification_draft_path.read_text() if verification_draft_path.exists() else ""
    )

    # Atomically store both fields in session.json
    session_data["roadmap_content"] = roadmap_content
    session_data["verification_content"] = verification_content
    session_data["updated"] = datetime.utcnow().isoformat() + "Z"
    tmp_path = str(session_path) + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(session_data, f)
    os.replace(tmp_path, session_path)

    return {
        "roadmap_content": roadmap_content,
        "verification_content": verification_content,
    }




class FixRoadmapFormatRequest(BaseModel):
    roadmap_content: Optional[str] = None


@app.post("/api/ideas/{idea_id}/fix-roadmap-format")
async def post_ideas_fix_roadmap_format(idea_id: str, body: FixRoadmapFormatRequest = None):
    """Correct the structural format of a roadmap using the format-correction skill.

    Accepts an optional roadmap_content in the request body (for the preflight case
    where content is passed directly). Falls back to session.json roadmap_content.

    Injects the format-correction skill, sends a webhook to the roadmap-converter
    agent, then waits via the shared idle-detection poll
    (``_poll_sentinel_with_idle_detect`` on ``prd_creator_activity.stamp``):
    ``ideas_idle_threshold`` stall window, ``FORMAT_CORRECTION_TIMEOUT`` infra
    backstop. The malformed input is pre-written to ``roadmap_draft.md``, so the
    stranded-``.md`` rescue is disabled (``rescue_stranded_reply_md=False``) —
    completion hinges on ``roadmap_draft.done``. On success it reads the corrected
    content and stores it in session.json. On a 408 the corrected roadmap is
    salvaged on the next ``GET /session`` and the frontend recovery poll picks it
    up, so a slow correction's output is never lost.

    Returns 404 if the idea is not found or session.json is missing.
    Returns 422 if no roadmap content is available to correct.
    Returns 408 on a stall or the infra backstop (the frontend then recovers).
    Returns 200 with {"roadmap_content": str} on success.
    """
    config = load_config()
    ideas_dir = Path(config.get("ideas_dir") or "")
    hooks_url = config.get("hooks_url", "http://localhost:18789/hooks/agent")
    hooks_token = config.get("hooks_token", "")

    idea_dir = Path(ideas_dir) / idea_id
    if not idea_dir.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    session_path = idea_dir / "session.json"
    session_data = _read_json_file(str(session_path))
    if session_data is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Determine roadmap content: prefer request body, fall back to session
    roadmap_content = None
    if body and body.roadmap_content:
        roadmap_content = body.roadmap_content
    else:
        roadmap_content = session_data.get("roadmap_content") or ""

    if not roadmap_content.strip():
        raise HTTPException(
            status_code=422,
            detail="No roadmap content available to correct. Provide roadmap_content in the request body or generate a roadmap first.",
        )

    # Write the malformed roadmap as input for the agent
    roadmap_draft_path = idea_dir / "roadmap_draft.md"
    roadmap_draft_path.write_text(roadmap_content)

    # Remove stale sentinel so polling loop doesn't find an old one
    done_path = idea_dir / "roadmap_draft.done"
    if done_path.exists():
        done_path.unlink()

    # Inject format-correction skill before webhook POST
    _inject_converter_skill("format-correction", config)

    ip = _idea_paths_for_messages(config, idea_id)
    # Build webhook payload
    timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
    session_key = f"ideas:{idea_id}:format-correction-{timestamp_ms}"
    webhook_payload = {
        "agentId": ROADMAP_CONVERTER_AGENT_ID,
        "sessionKey": session_key,
        "wakeMode": "now",
        # File-only run; reply is read from the workspace, never delivered to Signal.
        "deliver": False,
        "message": (
            f"[SESSION] {session_key}\n\n"
            f"Format-correct the following roadmap for idea {idea_id}.\n\n"
            f"Apply the format-correction skill from your workspace.\n\n"
            f"The roadmap content to correct:\n\n"
            f"{roadmap_content}\n\n"
            f"Write the corrected roadmap to {ip['roadmap_draft']}.\n"
            f"Write {ip['roadmap_done']} last."
        ),
    }

    # Send webhook POST
    _attempt_start_wall = time.time()
    headers = {"Authorization": f"Bearer {hooks_token}"}
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            hooks_url, json=webhook_payload, headers=headers,
            timeout=IDEAS_GATEWAY_POST_TIMEOUT,
        )
        if resp.status >= 400:
            raise HTTPException(status_code=502, detail=f"Webhook returned {resp.status}")

    # Idle-detection poll on the shared activity stamp — same machinery as the
    # chat send. ``roadmap_draft.md`` is server-pre-written above, so the
    # stranded-``.md`` rescue is disabled (``rescue_stranded_reply_md=False``);
    # completion hinges on ``roadmap_draft.done``. ``startup_grace=None`` waits
    # for a definitive stall/backstop verdict rather than fast-failing a slow
    # cold start. A late ``.done`` after a stall/backstop is salvaged on
    # GET /session (``_merge_roadmap_draft_into_session_data``) and surfaced by
    # the frontend recovery poll, so output generated late is never lost.
    idle_threshold = float(config.get("ideas_idle_threshold", 300))
    poll_result = await _poll_sentinel_with_idle_detect(
        done_path=done_path,
        stamp_path=idea_dir / "prd_creator_activity.stamp",
        attempt_start_wall=_attempt_start_wall,
        poll_timeout=FORMAT_CORRECTION_TIMEOUT,
        poll_interval=FORMAT_CORRECTION_POLL_INTERVAL,
        stall_threshold=idle_threshold,
        startup_grace=None,
        rescue_stranded_reply_md=False,
    )
    if not poll_result:
        _reason = getattr(poll_result, "reason", None) or "timeout"
        raise HTTPException(
            status_code=408,
            detail=f"Format correction {_reason} after {FORMAT_CORRECTION_TIMEOUT}s",
        )

    _record_operation_metric("format_correction", time.time() - _attempt_start_wall, config)

    # Read corrected roadmap
    corrected_content = roadmap_draft_path.read_text() if roadmap_draft_path.exists() else roadmap_content

    # Store in session.json
    updated_session = dict(session_data)
    updated_session["roadmap_content"] = corrected_content
    updated_session["updated"] = datetime.utcnow().isoformat() + "Z"
    _atomic_write_json_file(session_path, updated_session)

    return {"roadmap_content": corrected_content}


@app.get("/api/ideas/{idea_id}/download-verification")
def get_ideas_download_verification(idea_id: str):
    """Download the verification_content from session.json as a markdown file.

    Filename is derived from the first ``# heading`` in ``prd_content``,
    or falls back to the idea id. Suffix is always ``-verification.md``.
    Returns 404 if the idea is not found or verification_content is empty.
    """
    config = load_config()
    ideas_dir = Path(config.get("ideas_dir") or "")
    session_path = Path(ideas_dir) / idea_id / "session.json"

    if not session_path.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    session_data = _read_json_file(str(session_path)) or {}
    verification_content = session_data.get("verification_content", "") or ""

    if not verification_content:
        raise HTTPException(status_code=404, detail="No verification content available")

    # Derive filename from first # heading in prd_content, or fall back to id.
    import re as _re
    prd_content = session_data.get("prd_content", "") or ""
    filename = idea_id
    for line in prd_content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            heading = stripped[2:].strip()
            filename = heading.replace(" ", "-")
            filename = _re.sub(r"[^\w\-.]", "", filename)
            break

    filename = (filename or idea_id) + "-verification.md"

    from fastapi.responses import Response
    return Response(
        content=verification_content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/ideas/{idea_id}/download-roadmap")
def get_ideas_download_roadmap(idea_id: str):
    """Download the roadmap_content from session.json as a markdown file.

    Filename is derived from the first # heading in prd_content,
    or falls back to the idea id. Suffix is always "-roadmap.md".
    Returns 404 if the idea is not found or roadmap_content is empty.
    """
    config = load_config()
    ideas_dir = Path(config.get("ideas_dir") or "")
    session_path = Path(ideas_dir) / idea_id / "session.json"

    if not session_path.exists():
        raise HTTPException(status_code=404, detail="Idea not found")

    session_data = _read_json_file(str(session_path)) or {}
    roadmap_content = session_data.get("roadmap_content", "") or ""

    if not roadmap_content:
        raise HTTPException(status_code=404, detail="No roadmap content available")

    # Derive filename from first # heading in prd_content, or fall back to id
    import re as _re
    prd_content = session_data.get("prd_content", "") or ""
    filename = idea_id
    for line in prd_content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            heading = stripped[2:].strip()
            filename = heading.replace(" ", "-")
            filename = _re.sub(r"[^\w\-.]", "", filename)
            break

    filename = (filename or idea_id) + "-roadmap.md"

    from fastapi.responses import Response
    return Response(
        content=roadmap_content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
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

    - RUNNING / WAITING_FOR_SENTINEL / QUEUE_HALTED: writes ``pipeline_stop_requested`` under
      ``.autodev/pipeline/`` (the orchestrator consumes it at the top of its loop in all three
      states, so a genuinely-stuck queue can always be halted from the UI).
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

    if status in ("RUNNING", "WAITING_FOR_SENTINEL", "QUEUE_HALTED"):
        # QUEUE_HALTED (F1): the orchestrator stays alive in this state and calls
        # _check_stop_requested() at the top of every loop iteration, so a stop
        # sentinel written here IS consumed and the pipeline halts cleanly. Without
        # QUEUE_HALTED in this branch a genuinely-stuck queue (only BLOCKED / dead
        # DEPENDENCY_HOLD entries left) could not be halted from the UI.
        stop_file = Path(_pipeline_artifacts_dir(project_dir_path)) / "pipeline_stop_requested"
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
                "Orchestrator is not running — the STOP command is queued but won't be applied until "
                "the orchestrator starts. Use Resume in the header, or start it manually on the server."
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
    r"^- \[.\] `([A-Z]+-[A-Z]\d+)` \| (?:LOW|MEDIUM|HIGH|CRITICAL) \| .+",
    re.MULTILINE,
)


def _roadmap_phase_checkbox_stats(content: str) -> tuple[int, int]:
    """Return (total_phase_lines, completed_phase_lines) for roadmap markdown."""
    all_phases = _PHASE_LINE_RE.findall(content or "")
    completed_re = re.compile(r"^- \[[xX]\] ", re.MULTILINE)
    completed = len(completed_re.findall(content or ""))
    return len(all_phases), completed


# Behavioral Verification block enforcement (Stage C, P0 §2.2).
# Strict — no legacy opt-out per operator decision in §2.9.
_BV_BLOCK_HEADER_RE = re.compile(r"^\s*\*\*Behavioral Verification:\*\*\s*$")
_BV_SUBBULLETS = {
    "user_observable": re.compile(
        r"^\s*-\s+\*\*User-observable:\*\*\s+(.+)$"
    ),
    "how_to_check": re.compile(
        r"^\s*-\s+\*\*How we'll check:\*\*\s+(.+)$"
    ),
    "failure_language": re.compile(
        r"^\s*-\s+\*\*If this fails, the user sees:\*\*\s+(.+)$"
    ),
}
_BV_SUBBULLET_LABELS = {
    "user_observable": "User-observable",
    "how_to_check": "How we'll check",
    "failure_language": "If this fails, the user sees",
}


def _validate_roadmap_content(content: str) -> dict:
    """Validate roadmap content format.

    Checks:
    1. Phase lines match the required format.
    2. Each phase has a '> Test:' line within 10 lines.
    3. Each phase has a Behavioral Verification block within its own section
       (phase header → next phase header / EOF), with three sub-bullets
       (User-observable / How we'll check / If this fails, the user sees), each
       non-empty. Strict — no opt-out per P0 §2.9.
    4. No duplicate phase IDs.
    5. At least one phase line exists.

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

    # Check the Behavioral Verification block + its three sub-bullets within each
    # phase's own section (phase header → next phase header, or EOF for the last
    # phase). The block is canonically the LAST element of a phase (after
    # Entry/Exit/TDD/Done Criteria — see roadmap-generation SKILL.md), so its
    # distance from the header scales with the phase's length; bounding by the
    # phase section (not a fixed line window) accepts a complete block at any depth
    # while still rejecting a missing/incomplete one, and prevents a neighbouring
    # phase's block from satisfying this one. Strict per §2.9.
    phase_start_lines = [pm[0] for pm in phase_matches]
    for idx, (line_num, phase_id, line_content) in enumerate(phase_matches):
        section_end = (
            phase_start_lines[idx + 1] - 1
            if idx + 1 < len(phase_start_lines)
            else len(lines)
        )
        block_line = None
        for j in range(line_num, section_end + 1):
            if _BV_BLOCK_HEADER_RE.match(lines[j - 1]):
                block_line = j
                break
        if block_line is None:
            errors.append({
                "line": line_num,
                "content": line_content,
                "message": (
                    f"Phase {phase_id} (line {line_num}) is missing the "
                    "**Behavioral Verification:** block"
                ),
            })
            continue
        found = {key: False for key in _BV_SUBBULLETS}
        for j in range(block_line + 1, section_end + 1):
            for key, pat in _BV_SUBBULLETS.items():
                m = pat.match(lines[j - 1])
                if m and m.group(1).strip():
                    found[key] = True
        for key, present in found.items():
            if not present:
                label = _BV_SUBBULLET_LABELS[key]
                errors.append({
                    "line": block_line,
                    "content": line_content,
                    "message": (
                        f"Phase {phase_id} (line {line_num}) is missing "
                        f"Behavioral Verification sub-bullet: {label}"
                    ),
                })

    # Check for duplicate phase IDs — only examine phase header lines, not body text.
    # Using re.findall() on the full document would false-positive on phase IDs that
    # appear in Entry/Exit Criteria references (e.g. "`CORE-E1` complete").
    seen: dict = {}
    for _, pid, _ in phase_matches:
        seen[pid] = seen.get(pid, 0) + 1
    for pid, count in seen.items():
        if count > 1:
            errors.append({
                "line": 0,
                "content": pid,
                "message": f"Duplicate phase ID: {pid} appears {count} times",
            })

    return {"valid": len(errors) == 0, "errors": errors}


_VERIFICATION_SECTIONS = [
    ("# Verification", "Verification"),
    ("## Project type", "Project type"),
    ("## Entry point", "Entry point"),
    ("## Public surface", "Public surface"),
    ("## Verification stack", "Verification stack"),
]
_VERIFICATION_CANONICAL_TYPES = {
    "web-app", "http-api", "cli", "library", "data-pipeline",
    "game", "automation", "desktop-app", "mobile-app",
}
_VERIFICATION_DOC_MAX_LINES = 80
_VERIFICATION_TYPE_MAX_LEN = 40
_VERIFICATION_TYPE_BAD_CHARS = set("*_`#[]")


def _validate_verification_content(content: str) -> dict:
    """Validate project-level ``verification.md`` content.

    Schema (from the roadmap-generation skill's "Verification Document
    Output" section): required headings in order — ``# Verification``,
    ``## Project type``, ``## Entry point``, ``## Public surface``,
    ``## Verification stack``. Each section's body must be non-empty.

    Project type body: single line, <= 40 chars, no markdown formatting
    characters, and must match one of the canonical types
    (P0 §2.2 strict mode, user decision #1).

    Total doc length capped at 80 lines (skill rule).

    Returns the same shape as :func:`_validate_roadmap_content`:
    ``{"valid": bool, "errors": [{"line": int, "content": str, "message": str}]}``.
    """
    errors = []
    raw = content or ""
    lines = raw.splitlines()
    line_count = raw.count("\n") + (0 if raw.endswith("\n") or not raw else 1)
    # ``splitlines()`` does not preserve a trailing newline; use raw count to enforce cap.

    if line_count > _VERIFICATION_DOC_MAX_LINES:
        errors.append({
            "line": line_count,
            "content": "",
            "message": (
                f"verification.md exceeds the {_VERIFICATION_DOC_MAX_LINES}-line "
                f"length cap (got {line_count} lines)."
            ),
        })

    # Find each required section in order — out-of-order placement fails.
    cursor = 0
    section_spans = []  # (key, label, header_line, body_start, body_end)
    section_keys = []
    for idx, (heading, label) in enumerate(_VERIFICATION_SECTIONS):
        found_at = None
        for j in range(cursor, len(lines)):
            if lines[j].strip() == heading:
                found_at = j
                break
        if found_at is None:
            errors.append({
                "line": cursor + 1 if cursor < len(lines) else max(line_count, 1),
                "content": heading,
                "message": (
                    f"Missing required section: {heading}"
                    + ("" if idx == 0 else f" (expected after {_VERIFICATION_SECTIONS[idx - 1][0]!r})")
                ),
            })
            # Continue cursor — keep scanning so we can report all missing.
            continue
        # Detect out-of-order: if a *later* required section appears before
        # this one was found, that's an order violation.
        if section_spans and found_at < section_spans[-1][2]:
            errors.append({
                "line": found_at + 1,
                "content": heading,
                "message": (
                    f"Section {heading!r} is out of canonical order; expected after "
                    f"{_VERIFICATION_SECTIONS[idx - 1][0]!r}."
                ),
            })
        section_spans.append((heading, label, found_at, found_at + 1, None))
        section_keys.append(idx)
        cursor = found_at + 1

    # Fill in body_end as the start of the next section (or EOF).
    for span_idx, (heading, label, header_line, body_start, _end) in enumerate(section_spans):
        if span_idx + 1 < len(section_spans):
            next_header_line = section_spans[span_idx + 1][2]
        else:
            next_header_line = len(lines)
        # Mutate the tuple (rebuild because tuples are immutable).
        section_spans[span_idx] = (heading, label, header_line, body_start, next_header_line)

    # Each `## ...` section's body must be non-empty. The top-level
    # `# Verification` heading is a document title; it has no body of
    # its own (the four `##` sections sit below it).
    for heading, label, header_line, body_start, body_end in section_spans:
        if heading == "# Verification":
            continue
        body_lines = lines[body_start:body_end]
        if not any(line.strip() for line in body_lines):
            errors.append({
                "line": header_line + 1,
                "content": heading,
                "message": f"Section {heading!r} has empty body — fill in {label}.",
            })

    # Project type body strictness (decision #1 + #2).
    for heading, label, header_line, body_start, body_end in section_spans:
        if heading != "## Project type":
            continue
        body_lines = [lines[i] for i in range(body_start, body_end)]
        non_blank = [ln for ln in body_lines if ln.strip()]
        if not non_blank:
            # Already flagged as empty body above; do not double-report.
            break
        if len(non_blank) > 1:
            errors.append({
                "line": header_line + 1,
                "content": heading,
                "message": (
                    "Project type body must be a single line "
                    f"(got {len(non_blank)} non-blank lines)."
                ),
            })
            break
        token = non_blank[0].strip()
        if len(token) > _VERIFICATION_TYPE_MAX_LEN:
            errors.append({
                "line": header_line + 1,
                "content": token,
                "message": (
                    f"Project type token is too long "
                    f"({len(token)} chars; max {_VERIFICATION_TYPE_MAX_LEN})."
                ),
            })
            break
        if any(ch in token for ch in _VERIFICATION_TYPE_BAD_CHARS):
            errors.append({
                "line": header_line + 1,
                "content": token,
                "message": (
                    "Project type body must not contain markdown formatting characters "
                    f"({''.join(sorted(_VERIFICATION_TYPE_BAD_CHARS))})."
                ),
            })
            break
        if token not in _VERIFICATION_CANONICAL_TYPES:
            errors.append({
                "line": header_line + 1,
                "content": token,
                "message": (
                    f"Project type {token!r} is unknown — must be one of: "
                    + ", ".join(sorted(_VERIFICATION_CANONICAL_TYPES))
                ),
            })
        break

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


@app.post("/api/setup/repo-roadmap-hint")
async def post_setup_repo_roadmap_hint(request: Request):
    """Inspect a repository path and return roadmap auto-fill hints for setup UI."""
    body = await request.json()
    raw = body.get("path", "")
    try:
        path = _normalize_setup_repo_path(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid path: {exc}") from exc

    if not path.is_dir():
        raise HTTPException(status_code=422, detail="path does not exist or is not a directory")

    repo_abs = os.path.realpath(str(path))
    roadmap_paths = _glob_project_roadmap_paths(repo_abs)
    if not roadmap_paths:
        return {"found": False}

    if len(roadmap_paths) > 1:
        return {
            "found": True,
            "ambiguous": True,
            "roadmap_files": [os.path.basename(p) for p in roadmap_paths],
        }

    selected = roadmap_paths[0]
    try:
        with open(selected, "r", errors="replace") as f:
            content = f.read()
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Could not read roadmap file: {exc}") from exc

    phases_total, phases_complete = _roadmap_phase_checkbox_stats(content)
    return {
        "found": True,
        "ambiguous": False,
        "filename": os.path.basename(selected),
        "content": content,
        "all_phases_complete": bool(phases_total > 0 and phases_complete >= phases_total),
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
            "error": "Use an absolute path starting with / (e.g. /path/to/your-project/my-app)",
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


@app.post("/api/setup/validate-verification")
async def post_setup_validate_verification(request: Request):
    """Validate project-level ``verification.md`` content (Stage C).

    Body: ``{"content": str}``. No file writes. Returns the same shape as
    ``/api/setup/validate-roadmap``: ``{valid, errors[]}``.
    """
    body = await request.json()
    content = body.get("content", "")
    return _validate_verification_content(content)


# ─── Preflight checks ────────────────────────────────────────────────────────

_PIPELINE_GITIGNORE_ENTRIES = [".autodev/pipeline/"]
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


def _preflight_materialize(
    repo_path: str,
    roadmap_seed,
    prd_content,
    verification_content=None,
) -> list:
    """Write roadmap/prd/verification from preflight request when valid.

    ``verification_content`` is the project-level ``verification.md`` text
    (Stage C). When provided non-empty, it is validated via
    :func:`_validate_verification_content`, conflict-checked against any
    on-disk ``verification.md`` (same pattern as ``prd.md``), and written
    atomically. Returns the list of extra check rows.
    """
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

    vc = verification_content if verification_content is not None else ""
    vc = vc.strip()
    if vc:
        val = _validate_verification_content(vc)
        if not val["valid"]:
            em = "; ".join(e["message"] for e in val["errors"][:3])
            checks.append({
                "check": "verification doc",
                "status": "fail",
                "message": f"Invalid verification.md format: {em}",
            })
            return checks
        ver_path = os.path.join(repo_path, "verification.md")
        if os.path.exists(ver_path):
            try:
                existing = Path(ver_path).read_text()
            except OSError as exc:
                checks.append({
                    "check": "verification conflict",
                    "status": "fail",
                    "message": f"Could not read verification.md: {exc}",
                })
                return checks
            if _normalize_doc_text_for_compare(existing) != _normalize_doc_text_for_compare(vc):
                checks.append({
                    "check": "verification conflict",
                    "status": "fail",
                    "message": (
                        "verification.md on disk does not match the doc staged in the UI."
                    ),
                })
                return checks
        else:
            try:
                _atomic_write_file(ver_path, vc)
            except OSError as exc:
                checks.append({
                    "check": "verification write",
                    "status": "fail",
                    "message": str(exc),
                })
                return checks
            checks.append({
                "check": "verification write",
                "status": "fixed",
                "message": "Wrote verification.md",
            })

    return checks


def _run_preflight_checks(repo_path: str, config: dict | None = None) -> list:
    """Run ordered preflight checks for a project directory.

    Auto-fixes symlink and .gitignore when possible. Returns list of
    {"check": str, "status": str, "message": str} with status pass|fail|warn|fixed.
    """
    import subprocess
    import glob as glob_mod

    repo_path = os.path.realpath(os.path.expanduser(repo_path))
    if config is None:
        config = load_config()
    openclaw_dir = os.path.expanduser(config.get("openclaw_root") or "~/.openclaw")
    symlink_path = os.path.expanduser(config.get("project_dir_path") or "")
    if not symlink_path:
        symlink_path = os.path.join(openclaw_dir, "pipeline-project")
    checks = []

    # 1. Symlink — create or repair pipeline-project (repo-local or legacy OpenClaw path) → repo_path
    try:
        sym_parent = os.path.dirname(symlink_path)
        if sym_parent:
            os.makedirs(sym_parent, exist_ok=True)
        ok = os.path.lexists(symlink_path) and os.path.realpath(symlink_path) == repo_path
        if ok:
            checks.append({
                "check": "symlink",
                "status": "pass",
                "message": f"Symlink points to {repo_path}",
            })
        else:
            # Guard: don't repoint if the orchestrator is mid-run on a DIFFERENT project.
            # Repointing during an active poll redirects the sentinel path and breaks
            # completion detection (the .done file ends up in the wrong directory).
            _lock_path = _expand_lock_path(config)
            _orch_running = bool(_lock_path and _check_orchestrator_liveness(_lock_path))
            _running_project = None
            if _orch_running:
                _ps_path = config.get("pipeline_state_path")
                if _ps_path:
                    try:
                        _ps = _read_json_file(os.path.expanduser(_ps_path))
                        _pp = (_ps or {}).get("project_path", "")
                        _running_project = os.path.realpath(_pp) if _pp else None
                    except Exception:
                        pass
            if _orch_running and _running_project and _running_project != repo_path:
                checks.append({
                    "check": "symlink",
                    "status": "warn",
                    "message": (
                        f"Symlink points to {_running_project!r} (active run). "
                        f"Not repointing to {repo_path!r} while orchestrator holds the lock."
                    ),
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
                f"ln -sfn {repo_path} {symlink_path}"
            ),
        }        )

    # 1b. Pipeline artifact directory + optional legacy migration from repo root
    art = _pipeline_artifacts_dir(repo_path)
    try:
        os.makedirs(art, exist_ok=True)
        mig_msgs = _migrate_legacy_pipeline_artifacts(repo_path)
    except OSError as exc:
        checks.append({
            "check": "pipeline artifacts dir",
            "status": "fail",
            "message": f"Could not create {art}: {exc}",
        })
    else:
        if mig_msgs:
            checks.append({
                "check": "pipeline artifacts migration",
                "status": "fixed",
                "message": "; ".join(mig_msgs),
            })
        checks.append({
            "check": "pipeline artifacts dir",
            "status": "pass",
            "message": f"Pipeline artifacts directory ready ({art})",
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
            # Fresh init has no objects yet — HEAD does not exist until the first commit.
            # Executor gate reads phase_base_commit from pipeline_state (filled by orchestrator
            # from git rev-parse HEAD); without any commit, ERR_MISSING_BASE_COMMIT breaks the
            # phase. Previously we skipped the HEAD verification block below because
            # did_fresh_init is True — so preflight could report success with zero commits.
            _head_ok = subprocess.run(
                ["git", "-C", repo_path, "rev-parse", "--verify", "HEAD"],
                capture_output=True,
                text=True,
            )
            if _head_ok.returncode != 0:
                subprocess.run(
                    ["git", "-C", repo_path, "add", "-A"],
                    capture_output=True,
                    text=True,
                )
                _cmt = subprocess.run(
                    ["git", "-C", repo_path, "commit", "-m", "preflight: initial commit"],
                    capture_output=True,
                    text=True,
                )
                if _cmt.returncode != 0:
                    _cmt = subprocess.run(
                        [
                            "git",
                            "-C",
                            repo_path,
                            "commit",
                            "--allow-empty",
                            "-m",
                            "preflight: initial empty commit",
                        ],
                        capture_output=True,
                        text=True,
                    )
                if _cmt.returncode == 0:
                    checks.append({
                        "check": "git initial commit",
                        "status": "fixed",
                        "message": (
                            "Created initial commit so git HEAD exists "
                            "(required for executor gate / phase_base_commit)."
                        ),
                    })
                else:
                    checks.append({
                        "check": "git initial commit",
                        "status": "fail",
                        "message": (
                            "Git repo has no commits; executor gate will fail. "
                            f"stderr: {(_cmt.stderr or '')[:300]}"
                        ),
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
            # No branch named main/master — mid-pipeline repos often use phase/* only.
            head_rev = subprocess.run(
                ["git", "-C", repo_path, "rev-parse", "--verify", "HEAD"],
                capture_output=True, text=True,
            )
            if head_rev.returncode == 0 and (head_rev.stdout or "").strip():
                abbr = subprocess.run(
                    ["git", "-C", repo_path, "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True, text=True,
                )
                branch_label = (abbr.stdout or "").strip() or "unknown"
                if branch_label == "HEAD":
                    short_sha = (head_rev.stdout or "").strip()[:7]
                    branch_label = f"detached ({short_sha})" if short_sha else "detached"
                checks.append({
                    "check": "git repo",
                    "status": "warn",
                    "message": (
                        f"No main or master branch; current HEAD on '{branch_label}'. "
                        "Pipeline will use phase branches; consider creating main for integration."
                    ),
                })
            else:
                sym = subprocess.run(
                    ["git", "-C", repo_path, "symbolic-ref", "--short", "HEAD"],
                    capture_output=True, text=True,
                )
                current_branch = (sym.stdout or "").strip()
                if current_branch in ("main", "master"):
                    # ISSUE-5: existing repo on main/master but no commits yet.
                    # Attempt to create an initial commit so HEAD resolves and
                    # phase_base_commit can be set by the orchestrator on startup.
                    # Mirrors the fresh-init commit block above.
                    subprocess.run(
                        ["git", "-C", repo_path, "add", "-A"],
                        capture_output=True, text=True,
                    )
                    _cmt = subprocess.run(
                        ["git", "-C", repo_path, "commit",
                         "-m", "preflight: initial commit"],
                        capture_output=True, text=True,
                    )
                    if _cmt.returncode != 0:
                        _cmt = subprocess.run(
                            ["git", "-C", repo_path, "commit",
                             "--allow-empty",
                             "-m", "preflight: initial empty commit"],
                            capture_output=True, text=True,
                        )
                    if _cmt.returncode == 0:
                        checks.append({
                            "check": "git initial commit",
                            "status": "fixed",
                            "message": (
                                "Created initial commit so git HEAD exists "
                                "(required for executor gate / phase_base_commit)."
                            ),
                        })
                    else:
                        checks.append({
                            "check": "git initial commit",
                            "status": "fail",
                            "message": (
                                "Git repo has no commits and commit attempt failed; "
                                "executor gate will fail with ERR_MISSING_BASE_COMMIT. "
                                f"stderr: {(_cmt.stderr or '')[:300]}"
                            ),
                        })
                else:
                    checks.append({"check": "git repo", "status": "fail",
                                    "message": (
                                        "No main or master branch and no commits on HEAD. "
                                        f"Run: git -C {repo_path} add -A && git -C {repo_path} commit -m 'init' "
                                        "or create main/master."
                                    )})

    # 5. Workspace directories and docs (under OPENCLAW_ROOT — install docs use this name)
    _workspaces_under_openclaw = " (under OPENCLAW_ROOT)"
    for agent in _WORKSPACE_AGENTS:
        ws_dir = os.path.join(openclaw_dir, f"workspace-{agent}")
        if not os.path.isdir(ws_dir):
            checks.append({"check": f"workspace-{agent}", "status": "fail",
                            "message": f"workspace-{agent} directory missing{_workspaces_under_openclaw}"})
        else:
            for doc in _WORKSPACE_DOCS:
                doc_path = os.path.join(ws_dir, doc)
                if not os.path.exists(doc_path):
                    checks.append({"check": f"workspace-{agent}/{doc}", "status": "fail",
                                    "message": (
                                        f"workspace-{agent}/{doc} missing — operator must install this file."
                                        f"{_workspaces_under_openclaw}"
                                    )})
            # Only add pass if no failures for this workspace
            missing_docs = [d for d in _WORKSPACE_DOCS if not os.path.exists(os.path.join(ws_dir, d))]
            if not missing_docs:
                checks.append({"check": f"workspace-{agent}", "status": "pass",
                                "message": (
                                    f"workspace-{agent} present with all required docs"
                                    f"{_workspaces_under_openclaw}"
                                )})

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

    # 7. Verification doc on disk (Stage C) — fail if missing or malformed.
    # Strict per §2.9: a project without a valid verification.md cannot run.
    ver_path = os.path.join(repo_path, "verification.md")
    if not os.path.exists(ver_path):
        checks.append({
            "check": "verification doc",
            "status": "fail",
            "message": (
                "verification.md is missing — re-run conversion from the Ideas screen "
                "to generate it."
            ),
        })
    else:
        try:
            ver_text = Path(ver_path).read_text()
        except OSError as exc:
            checks.append({
                "check": "verification doc",
                "status": "fail",
                "message": f"Could not read verification.md: {exc}",
            })
        else:
            ver_val = _validate_verification_content(ver_text)
            if ver_val["valid"]:
                checks.append({
                    "check": "verification doc",
                    "status": "pass",
                    "message": "verification.md present and valid",
                })
            else:
                em = "; ".join(e["message"] for e in ver_val["errors"][:3])
                checks.append({
                    "check": "verification doc",
                    "status": "fail",
                    "message": (
                        f"verification.md is invalid: {em}. "
                        "Re-run conversion from the Ideas screen to regenerate it."
                    ),
                })

    return checks


@app.post("/api/setup/preflight")
async def post_setup_preflight(request: Request):
    """Run preflight validation checks for a project directory.

    Body: {"repo_path": str, "roadmap_seed": optional, "prd_content": optional,
           "verification_content": optional, "confirm_roadmap_archive": optional bool,
           "keep_filename": optional str}
    When multiple *oadmap*.md exist, returns roadmap_ambiguous until confirm_roadmap_archive.
    Returns: {"checks": [...], optional roadmap_ambiguous, roadmap_files, recommended_keep}
    """
    body = await request.json()
    repo_path = body.get("repo_path", "")
    roadmap_seed = body.get("roadmap_seed")
    prd_content = body.get("prd_content")
    verification_content = body.get("verification_content")
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

    mat = _preflight_materialize(repo_abs, roadmap_seed, prd_content, verification_content)
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


@app.delete("/api/setup/recent-projects")
async def delete_setup_recent_project(request: Request):
    """Remove a single entry from the recent-projects list by absolute path.

    Body: ``{"path": "<absolute path>"}``. The path is canonicalized via
    ``os.path.realpath(os.path.expanduser(...))`` — the same normalization
    :func:`append_recent_project` applies before storing — so callers may
    submit denormalized forms (e.g. ``~/foo`` or ``/a/./b``) and still
    target the stored entry.

    Idempotent: removing a path not in the list returns ``removed=False``
    with status 200, not an error.
    """
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="JSON body object required")
    raw = body.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(status_code=422, detail="path is required (non-empty string)")
    target = os.path.realpath(os.path.expanduser(raw.strip()))
    entries = _read_recent_projects()
    kept = [
        e for e in entries
        if not (isinstance(e, dict) and e.get("path") == target)
    ]
    removed = len(kept) != len(entries)
    if removed:
        _write_recent_projects_atomic(kept)
    return {"removed": removed, "projects": kept}


@app.post("/api/setup/recent-projects/prune")
def post_setup_recent_projects_prune():
    """Remove every recent-projects entry whose ``path`` is no longer a directory on disk.

    Returns ``{"removed_count": int, "removed_paths": [str, ...], "projects": [entry, ...]}``.

    Used by tests in tear-down (after the test's tmpdir is gone) so test runs
    self-clean instead of accumulating stale entries, and exposed for ad-hoc
    operator cleanup of accumulated dead entries from manually deleted
    projects. Malformed entries (non-dict, missing/empty ``path``) are also
    treated as dead and removed.
    """
    entries = _read_recent_projects()
    kept: list = []
    removed_paths: list[str] = []
    for e in entries:
        if not isinstance(e, dict):
            removed_paths.append(repr(e))
            continue
        p = e.get("path")
        if isinstance(p, str) and p and os.path.isdir(p):
            kept.append(e)
        else:
            removed_paths.append(p if isinstance(p, str) else repr(p))
    if removed_paths:
        _write_recent_projects_atomic(kept)
    return {
        "removed_count": len(removed_paths),
        "removed_paths": removed_paths,
        "projects": kept,
    }


# ---------------------------------------------------------------------------
# Queue endpoints
# IMPORTANT: Fixed-path routes (status, trigger-next, mode) MUST be registered
# before parameterized routes ({entry_id}) to avoid FastAPI treating "status"
# as an entry_id parameter.
# ---------------------------------------------------------------------------

@app.get("/api/queue/status")
def get_queue_status():
    """Summary counts for the Pipeline Monitor header integration."""
    config = load_config()
    q = _read_queue_file(config)
    entries = q.get("queue", [])
    pipeline_state = _read_json_file(os.path.expanduser(config.get("pipeline_state_path") or "")) or {}
    return {
        "queue_length": len(entries),
        "ready_count": sum(1 for e in entries if e["state"] == "READY"),
        "blocked_count": sum(1 for e in entries if e["state"] in ("BLOCKED", "ESCALATION")),
        "completed_count": sum(1 for e in entries if e["state"] == "COMPLETED"),
        "queue_mode": q.get("queue_mode", "auto"),
        "queue_halted": pipeline_state.get("pipeline_status") == "QUEUE_HALTED",
    }


def _entry_has_banked_answer(entry: dict) -> bool:
    """True if a parked escalation entry has a banked (deferred) operator command waiting.

    Read-only probe of the per-project ``pending_escalation_command.json`` — never writes the queue.
    The orchestrator only promotes ESCALATION -> ESCALATION_ANSWERED on its next selection, so while
    it is dead (e.g. QUEUE_HALTED) a banked answer leaves the row in ESCALATION; this lets the queue
    surface (``has_banked_answer``) and the trigger-next halt-reason treat that row as recoverable.
    Returns False for any non-escalation state.
    """
    if entry.get("state") not in ("ESCALATION", ESCALATION_ANSWERED):
        return False
    ep = entry.get("project_path", "")
    if not ep:
        return False
    try:
        pend = os.path.join(
            _pipeline_artifacts_dir(os.path.realpath(os.path.expanduser(ep))),
            "pending_escalation_command.json",
        )
        return os.path.exists(pend)
    except OSError:
        return False


def _queue_trigger_next_halted_reason(entries: list) -> str:
    """Why POST /api/queue/trigger-next found no runnable row (orchestrator halt buckets, L-07)."""
    non_terminal = [e for e in entries if e.get("state") not in ("COMPLETED", "FAILED")]
    if not non_terminal:
        return "all_completed"
    # P1 Stage H — recoverable, not a dead stall: a parked ESCALATION_ANSWERED entry, OR an
    # ESCALATION row with a banked answer the orchestrator has not promoted yet (it is dead, e.g.
    # QUEUE_HALTED). Both are resolved by Resume/relaunch. Report distinctly (before all_blocked)
    # so the toast matches the UI's has_banked_answer Resume affordance.
    if any(e.get("state") == ESCALATION_ANSWERED or _entry_has_banked_answer(e) for e in non_terminal):
        return "answered_pending_revival"
    states = [e.get("state") for e in non_terminal]
    parked = frozenset({"BLOCKED", "ESCALATION"})
    if all(s in parked for s in states):
        return "all_blocked"
    if all(s == "DEPENDENCY_HOLD" for s in states):
        return "all_dependency_hold"
    return "mixed"


def _queue_run_trigger_next_logic(config: dict) -> dict:
    """Pick next READY/SKIPPED_PENDING row, preflight, set ACTIVE, spawn orchestrator.

    Same behavior as POST /api/queue/trigger-next body.

    Raises:
        HTTPException: 409 if any file-backed row is ACTIVE; 500 if spawn fails.
    Returns:
        {"ok": True, "started": name} or {"queue_halted": True, "error": str}.
    """
    q = _read_queue_file(config)
    entries = q.get("queue", [])

    if not entries:
        return {"ok": False, "reason": "queue_empty"}

    _queue_demote_stale_active_from_pipeline_state(config)
    q = _read_queue_file(config)
    entries = q.get("queue", [])

    if any(e["state"] == "ACTIVE" for e in entries):
        raise HTTPException(status_code=409, detail="A project is already ACTIVE in the queue")

    state_by_id = {e["id"]: e["state"] for e in entries}
    now = datetime.now(timezone.utc).isoformat()

    for entry in sorted(entries, key=lambda e: e["position"]):
        if entry["state"] not in ("READY", "SKIPPED_PENDING"):
            continue
        if entry.get("parent_id") and state_by_id.get(entry["parent_id"]) != "COMPLETED":
            continue
        checks = _run_preflight_checks(entry["project_path"])
        if any(c.get("status") == "fail" for c in checks):
            # F9 — CAS the skip-and-requeue by id on fresh data (no lost update).
            def _skip(qd, _eid=entry["id"]):
                es = qd.get("queue", [])
                t = next((e for e in es if e["id"] == _eid), None)
                if t is None:
                    raise QueueAbort()
                t["state"] = "SKIPPED_PENDING"
                t["skip_count"] = t.get("skip_count", 0) + 1
                desc_ids = _get_all_descendants(es, _eid)
                for desc_id in desc_ids:
                    desc = next((e for e in es if e["id"] == desc_id), None)
                    if desc and desc["state"] not in ("ACTIVE", "COMPLETED"):
                        desc["state"] = "SKIPPED_PENDING"
                        desc["skip_count"] = desc.get("skip_count", 0) + 1
                group_size = 1 + len(desc_ids)
                new_pos = min(t["position"] + group_size, len(es))
                _move_group_atomically(es, _eid, new_pos)
                return True
            _mutate_queue_file(config, _skip)
            entry["state"] = "SKIPPED_PENDING"  # keep the walk snapshot roughly aligned
            continue

        # F9 — commit ACTIVE via CAS, THEN spawn (exactly once, only after the commit lands).
        def _activate(qd, _eid=entry["id"]):
            t = next((e for e in qd.get("queue", []) if e["id"] == _eid), None)
            if t is None or t.get("state") not in ("READY", "SKIPPED_PENDING"):
                raise QueueAbort()  # the picked row changed under us
            t["state"] = "ACTIVE"
            t["started_at"] = now
            return True
        if _mutate_queue_file(config, _activate) is None:
            continue  # re-pick on the next call
        result = _spawn_orchestrator(entry["project_path"], config)
        if not result.get("ok"):
            def _fail(qd, _eid=entry["id"]):
                t = next((e for e in qd.get("queue", []) if e["id"] == _eid), None)
                if t is None:
                    raise QueueAbort()
                t["state"] = "FAILED"
                return True
            _mutate_queue_file(config, _fail)
            raise HTTPException(status_code=500, detail=result.get("error", "Failed to spawn orchestrator"))
        return {"ok": True, "started": entry["name"]}

    halted_reason = _queue_trigger_next_halted_reason(entries)
    return {
        "queue_halted": True,
        "error": "all projects blocked or in dependency hold",
        "queue_halted_reason": halted_reason,
    }


def _maybe_autostart_queue(config: dict) -> dict:
    """Start the next eligible queue project when ``queue_mode="auto"`` and the pipeline is idle.

    Shared by the manual→auto mode toggle and the three READY-making endpoints
    (``add`` / ``parent``-clear / ``revalidate``) so an eligible project starts without a
    manual ``POST /api/queue/trigger-next`` or a mode toggle. Self-guarding and
    **non-raising**: it returns a status dict and never propagates an ``HTTPException``, so
    callers can surface the result as an additive ``auto_start`` field without risking the
    originating request.

    Returns ``{"attempted": False, "reason": ...}`` when it does nothing —
    ``not_auto_mode`` (queue not in auto), ``orchestrator_lock_held`` (a live orchestrator
    holds pipeline.lock), ``pipeline_status_busy`` (an agent is mid-run), or
    ``queue_has_active`` (a project is already ACTIVE). On an attempt it returns
    ``{"attempted": True, **_queue_run_trigger_next_logic(...)}`` (carrying ``ok``/``started``
    or ``queue_halted``), or, if the start raised, ``{"attempted": True, "ok": False,
    "reason": "already_active"|"spawn_failed"|"start_error", "error": ...}``.
    """
    if _read_queue_file(config).get("queue_mode") != "auto":
        return {"attempted": False, "reason": "not_auto_mode"}
    lp = _expand_lock_path(config)
    if lp and _check_orchestrator_liveness(lp):
        return {"attempted": False, "reason": "orchestrator_lock_held"}
    ps_path = os.path.expanduser(config.get("pipeline_state_path") or "")
    ps = _read_json_file(ps_path) if os.path.exists(ps_path) else {}
    st = (ps.get("pipeline_status") or "").strip()
    if st in ("RUNNING", "WAITING_FOR_SENTINEL"):
        return {"attempted": False, "reason": "pipeline_status_busy"}
    _queue_demote_stale_active_from_pipeline_state(config)
    q = _read_queue_file(config)
    if any(e["state"] == "ACTIVE" for e in q.get("queue", [])):
        return {"attempted": False, "reason": "queue_has_active"}
    try:
        out = _queue_run_trigger_next_logic(config)
    except HTTPException as e:
        reason = {409: "already_active", 500: "spawn_failed"}.get(e.status_code, "start_error")
        return {"attempted": True, "ok": False, "reason": reason, "error": e.detail}
    except QueueVersionConflict as e:
        # F9 — a CAS exhaustion during best-effort autostart must NOT 503 the originating
        # request (whose own write already committed). Surface it in the additive field instead.
        return {"attempted": True, "ok": False, "reason": "queue_busy", "error": str(e)}
    return {"attempted": True, **out}


@app.post("/api/queue/trigger-next")
async def post_queue_trigger_next():
    """Manually trigger the next project in the queue (manual mode).

    Returns 409 if a project is currently ACTIVE.
    Calls _run_preflight_checks (which auto-repairs the symlink) then spawns orchestrator.
    """
    config = load_config()
    return _queue_run_trigger_next_logic(config)


@app.patch("/api/queue/mode")
async def patch_queue_mode(request: Request):
    """Toggle queue_mode between 'auto' and 'manual'.

    When switching **manual → auto**, if the orchestrator is not holding the lock and the
    pipeline is not mid-agent, runs the same start-next logic as **Trigger next** once
    (eligible READY row, preflight, symlink repair, spawn).
    """
    body = await request.json()
    mode = body.get("queue_mode")
    if mode not in ("auto", "manual"):
        raise HTTPException(status_code=422, detail="queue_mode must be 'auto' or 'manual'")
    config = load_config()

    def _apply(q):
        prev = q.get("queue_mode", "auto")
        q["queue_mode"] = mode
        return prev
    prev_mode = _mutate_queue_file(config, _apply)
    response: dict = {"ok": True, "queue_mode": mode}
    # Transition guard stays: only a manual→auto switch kicks (auto→auto must not re-kick).
    # The helper additionally self-gates on queue_mode=="auto" for the other call sites.
    if mode == "auto" and prev_mode == "manual":
        response["auto_advance"] = _maybe_autostart_queue(config)
    return response


@app.get("/api/queue")
def get_queue():
    """Full queue with computed dependency_tree and next_eligible.

    Each entry may include ``live_pipeline_status`` when its ``project_path``
    (realpath) matches the global ``pipeline_state.json`` ``project_path`` —
    same rule as ``GET /api/queue/{entry_id}/snapshot``. Other entries set
    ``live_pipeline_status`` to null. When the paths match, ``live_current_agent``
    is also set from pipeline state (for ``WAITING_FOR_SENTINEL`` “Running {agent}”
    queue pills); otherwise null.
    """
    config = load_config()
    q = _read_queue_file(config)
    entries = q.get("queue", [])
    ordered = sorted(entries, key=lambda e: e["position"])

    ps_path = os.path.expanduser(config.get("pipeline_state_path") or "")
    ps = _read_json_file(ps_path) if os.path.exists(ps_path) else None
    ps_project = ps.get("project_path", "") if ps else ""
    try:
        ps_real = os.path.realpath(ps_project) if ps_project else ""
    except OSError:
        ps_real = ""

    merged, _ingested = _merge_ingested_active_project(ordered, ps)
    # Sort after merge so synthetic ``ingest-*`` rows (active project not in queue file) are first too.
    merged = _queue_entries_active_first_by_pipeline_state(merged, ps_real)

    enriched = []
    for e in merged:
        entry = dict(e)
        if ps and ps_real:
            ep = e.get("project_path", "")
            try:
                er = os.path.realpath(ep) if ep else ""
            except OSError:
                er = ""
            if er and er == ps_real:
                entry["live_pipeline_status"] = ps.get("pipeline_status")
                # UI: WAITING_FOR_SENTINEL pill — "Running {agent}" (see formatWaitForSentinelLabel in index.html)
                entry["live_current_agent"] = (ps.get("current_agent") or None)
            elif entry.get("parked_pipeline_status"):
                entry["live_pipeline_status"] = entry["parked_pipeline_status"]
                entry["live_current_agent"] = None
            else:
                entry["live_pipeline_status"] = None
                entry["live_current_agent"] = None
        else:
            if entry.get("parked_pipeline_status"):
                entry["live_pipeline_status"] = entry["parked_pipeline_status"]
                entry["live_current_agent"] = None
            else:
                entry["live_pipeline_status"] = None
                entry["live_current_agent"] = None

        # P1 Stage H follow-up (B3ii): expose whether a parked escalation entry already has a
        # banked answer so the UI can surface the "Answer banked" pill + Resume affordance even
        # before the (possibly dead) orchestrator promotes ESCALATION -> ESCALATION_ANSWERED.
        # Shared with the trigger-next halt-reason via _entry_has_banked_answer (read-only).
        entry["has_banked_answer"] = _entry_has_banked_answer(entry)

        # W3-B: enrich ACTIVE entries with live roadmap phase counts
        if entry.get("state") == "ACTIVE":
            _proj = entry.get("project_path", "")
            if _proj:
                try:
                    import glob as _glob_mod
                    _rm_candidates = _glob_mod.glob(os.path.join(_proj, "*oadmap*.md"))
                    if _rm_candidates:
                        with open(_rm_candidates[0], "r", errors="replace") as _rf:
                            _rc = _rf.read()
                        _pt, _pc = _roadmap_phase_checkbox_stats(_rc)
                        entry["phases_total"] = _pt
                        entry["phases_complete"] = _pc
                except Exception:
                    pass  # non-fatal — queue entry returned without phase counts

        enriched.append(entry)

    return {
        "queue": enriched,
        "queue_mode": q.get("queue_mode", "auto"),
        "last_updated": q.get("last_updated", ""),
        "dependency_tree": _compute_dependency_tree(merged),
        "next_eligible": _find_next_eligible(merged),
        "display_ranks": _compute_display_ranks(merged),
    }


@app.put("/api/queue/order")
async def put_queue_order(request: Request):
    """Replace queue order atomically. Body: {"entry_ids": [uuid, ...]} — full permutation of current entries."""
    body = await request.json()
    entry_ids = body.get("entry_ids")
    if not isinstance(entry_ids, list):
        raise HTTPException(status_code=422, detail="entry_ids must be an array")

    config = load_config()

    def _apply(q):
        entries = q.get("queue", [])
        # Re-validate against the fresh queue: if a concurrent add/remove changed the set, the
        # client's permutation is stale -> 400 (re-fetch and retry).
        err = _validate_queue_entry_ids_order(entries, entry_ids)
        if err:
            raise HTTPException(status_code=400, detail=err)
        by_id = {e["id"]: e for e in entries}
        new_queue = [by_id[uid] for uid in entry_ids]
        for i, e in enumerate(new_queue, 1):
            e["position"] = i
        q["queue"] = new_queue
        return True
    _mutate_queue_file(config, _apply)
    return {"ok": True}


@app.post("/api/queue/add")
async def post_queue_add(request: Request):
    """Add a project to the queue after preflight materialize + checks.

    Runs ``_preflight_materialize`` (no seed/PRD from this endpoint) then
    ``_run_preflight_checks`` (symlink, gitignore, git init, workspaces, roadmap).
    On success, appends the project to recent-projects.

    Body: {"project_path": str, "idea_id": str|null, "parent_id": str|null}
    Returns 400 with validation_errors if preflight fails.
    Returns 422 if project_path is invalid.
    Returns 409 if the same project path (realpath) is already queued in a non-terminal state
    (terminal: COMPLETED, FAILED only).
    """
    import uuid as _uuid
    from datetime import datetime, timezone as tz

    body = await request.json()
    project_path = body.get("project_path", "").strip()
    idea_id = body.get("idea_id")
    parent_id = body.get("parent_id")
    completion_review = bool(body.get("completion_review", False))

    if not project_path:
        raise HTTPException(status_code=422, detail="project_path is required")
    if not os.path.isabs(project_path):
        raise HTTPException(status_code=422, detail="project_path must be absolute")
    if not os.path.isdir(project_path):
        raise HTTPException(status_code=422, detail="project_path does not exist or is not a directory")

    repo_abs = os.path.realpath(os.path.expanduser(project_path))
    mat = _preflight_materialize(repo_abs, None, None)
    if any(c.get("status") == "fail" for c in mat):
        return JSONResponse(status_code=400, content={"validation_errors": mat})

    checks = _run_preflight_checks(repo_abs)
    if any(c.get("status") == "fail" for c in checks):
        return JSONResponse(
            status_code=400,
            content={"validation_errors": mat + checks},
        )

    append_recent_project(repo_abs)

    config = load_config()
    new_real = repo_abs
    _terminal_queue_states = frozenset({"COMPLETED", "FAILED"})

    def _apply(q):
        entries = q.get("queue", [])
        # Re-derive all queue-level validation on the FRESH queue so a concurrent add of the
        # same project (409) or a parent state change is honoured, not lost.
        for e in entries:
            ep = (e.get("project_path") or "").strip()
            if not ep:
                continue
            try:
                existing_real = os.path.realpath(os.path.expanduser(ep))
            except OSError:
                continue
            if existing_real != new_real:
                continue
            if e.get("state") not in _terminal_queue_states:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Project already in queue ({e.get('name', 'unknown')}, {e.get('state')}). "
                        "Remove it or wait until COMPLETED/FAILED before adding again."
                    ),
                )

        # Circular dependency check
        if parent_id and _detect_circular_dependency(entries, None, parent_id):
            raise HTTPException(status_code=400, detail="Circular dependency detected")

        # Initial state: DEPENDENCY_HOLD only when parent is in a blocking queue state
        initial_state = "READY"
        if parent_id:
            parent_entry = next((e for e in entries if e["id"] == parent_id), None)
            if parent_entry is None:
                raise HTTPException(status_code=400, detail="parent_id does not reference an existing queue entry")
            if parent_entry.get("state") != "COMPLETED" and parent_blocks_child(parent_entry.get("state")):
                initial_state = "DEPENDENCY_HOLD"

        now = datetime.now(tz.utc).isoformat()
        new_entry = {
            "id": str(_uuid.uuid4()),
            "project_path": repo_abs,
            "idea_id": idea_id,
            "name": os.path.basename(project_path.rstrip("/")) or project_path,
            "state": initial_state,
            "position": len(entries) + 1,
            "parent_id": parent_id,
            "added_at": now,
            "started_at": None,
            "completed_at": None,
            "blocked_at": None,
            "skip_count": 0,
            "preflight_validated_at": now,
            "completion_review": completion_review,
            "notes": "",
        }
        entries.append(new_entry)
        q["queue"] = entries
        return new_entry

    entry = _mutate_queue_file(config, _apply)
    # Auto-start the next eligible project when the queue is in auto mode and the pipeline
    # is idle (server-owned; replaces the former client-side post-add trigger shim).
    # Best-effort and non-raising — a failed start surfaces in auto_start, never 500s the add.
    auto_start = _maybe_autostart_queue(config)
    # Re-read so the returned state is truthful (ACTIVE if this row started, READY if an
    # earlier-position row started or nothing did, FAILED on spawn failure). Entry fields
    # stay top-level (callers read addD.id); only auto_start is additive.
    refreshed = next(
        (e for e in _read_queue_file(config).get("queue", []) if e.get("id") == entry["id"]),
        entry,
    )
    return {**refreshed, "auto_start": auto_start}


@app.delete("/api/queue/{entry_id}")
def delete_queue_entry(entry_id: str):
    """Remove an entry from the queue.

    ACTIVE rows are rejected only when global pipeline_state targets this entry's
    project (realpath) and pipeline_status is mid-flight (RUNNING or WAITING_FOR_SENTINEL).

    ``ingest-*`` ids are synthetic rows from ``GET /api/queue`` when ``pipeline_state``
    references a project not present in the persisted queue file; they cannot be deleted
    via this endpoint.
    """
    if entry_id.startswith("ingest-"):
        raise HTTPException(
            status_code=422,
            detail=(
                "Synthetic queue row (from pipeline_state, not in pipeline_queue.json). "
                "Align pipeline_state with the queue, reset pipeline state, or remove a "
                "persisted entry instead."
            ),
        )
    config = load_config()
    ps_path = os.path.expanduser(config.get("pipeline_state_path") or "")

    def _apply(q):
        entries = q.get("queue", [])
        target = next((e for e in entries if e["id"] == entry_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Queue entry not found")
        if target["state"] == "ACTIVE":
            ps = _read_json_file(ps_path) if os.path.exists(ps_path) else None
            if ps:
                try:
                    ps_real = os.path.realpath(ps.get("project_path", "") or "")
                    ep_real = os.path.realpath((target.get("project_path") or "").strip() or "")
                except OSError:
                    ps_real, ep_real = "", ""
                if ps_real and ep_real and ps_real == ep_real:
                    pst = ps.get("pipeline_status")
                    if pst in ("RUNNING", "WAITING_FOR_SENTINEL"):
                        raise HTTPException(
                            status_code=409,
                            detail="Cannot remove: pipeline is mid-flight for this project",
                        )
        new_entries = [e for e in entries if e["id"] != entry_id]
        _resequence_positions(new_entries)
        q["queue"] = new_entries
        return True
    _mutate_queue_file(config, _apply)
    return {"ok": True}


@app.post("/api/queue/clear")
async def post_queue_clear(request: Request):
    """Remove all queue entries. Preserves queue_mode.

    Body (optional): ``{"force": true}`` to clear even when an entry is ACTIVE
    (operator escape hatch for stuck rows).
    """
    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}
    force = bool(body.get("force"))

    config = load_config()

    def _apply(q):
        entries = q.get("queue", [])
        if any(e.get("state") == "ACTIVE" for e in entries) and not force:
            raise HTTPException(
                status_code=409,
                detail='Queue has an ACTIVE entry; pass {"force": true} to clear anyway.',
            )
        n = len(entries)
        q["queue"] = []
        return n
    cleared = _mutate_queue_file(config, _apply)
    return {"ok": True, "cleared": cleared}


@app.patch("/api/queue/{entry_id}/position")
async def patch_queue_position(entry_id: str, request: Request):
    """Reorder a queue entry to the specified position."""
    body = await request.json()
    new_pos = body.get("position")
    if new_pos is None:
        raise HTTPException(status_code=422, detail="position is required")

    config = load_config()

    def _apply(q):
        entries = q.get("queue", [])
        target = next((e for e in entries if e["id"] == entry_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Queue entry not found")
        if target["state"] in ("ACTIVE", "COMPLETED"):
            raise HTTPException(status_code=409, detail=f"Cannot reorder a {target['state']} entry")
        if target.get("parent_id"):
            raise HTTPException(
                status_code=409,
                detail="Child projects cannot be repositioned independently. Move the parent project instead.",
            )

        # Clamp position to the fresh range.
        np = max(1, min(int(new_pos), len(entries)))
        old_pos = target["position"]
        if np == old_pos:
            raise QueueAbort()  # no-op move — commit nothing

        # If this entry has dependents, move the entire group atomically
        if _get_all_descendants(entries, entry_id):
            _move_group_atomically(entries, entry_id, np)
        else:
            # Shift entries between old and new positions (single-entry move)
            if np < old_pos:
                for e in entries:
                    if np <= e["position"] < old_pos and e["id"] != entry_id:
                        e["position"] += 1
            else:
                for e in entries:
                    if old_pos < e["position"] <= np and e["id"] != entry_id:
                        e["position"] -= 1
            target["position"] = np
            _resequence_positions(entries)

        q["queue"] = entries
        return True
    _mutate_queue_file(config, _apply)
    return {"ok": True}


@app.patch("/api/queue/{entry_id}/parent")
async def patch_queue_parent(entry_id: str, request: Request):
    """Set or clear parent dependency for a queue entry."""
    body = await request.json()
    parent_id = body.get("parent_id")  # None to clear

    config = load_config()

    def _apply(q):
        entries = q.get("queue", [])
        target = next((e for e in entries if e["id"] == entry_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Queue entry not found")

        if _detect_circular_dependency(entries, entry_id, parent_id):
            raise HTTPException(status_code=400, detail="Circular dependency detected")

        target["parent_id"] = parent_id

        cleared_to_ready = False
        if parent_id is None:
            # Clearing parent: restore to READY if currently in DEPENDENCY_HOLD
            if target.get("state") == "DEPENDENCY_HOLD":
                target["state"] = "READY"
                cleared_to_ready = True
        else:
            # Setting parent: hold only if parent is in a blocking state
            parent_entry = next((e for e in entries if e["id"] == parent_id), None)
            if parent_entry and parent_entry.get("state") != "COMPLETED":
                target["state"] = (
                    "DEPENDENCY_HOLD"
                    if parent_blocks_child(parent_entry.get("state"))
                    else "READY"
                )

            # Auto-reposition: place child immediately after parent's last existing sibling
            parent_pos = next((e["position"] for e in entries if e["id"] == parent_id), None)
            if parent_pos is not None:
                siblings = [e for e in entries if e.get("parent_id") == parent_id and e["id"] != entry_id]
                max_sibling_pos = max((e["position"] for e in siblings), default=parent_pos)
                new_child_pos = max_sibling_pos + 1
                _move_group_atomically(entries, entry_id, new_child_pos)
                _resequence_positions(entries)

        q["queue"] = entries
        return {"target": dict(target), "cleared_to_ready": cleared_to_ready}

    result = _mutate_queue_file(config, _apply)
    target = result["target"]
    # Auto-start only when a parent-clear just made this row READY (scope: clear→READY);
    # the set-parent branch never autostarts. Additive auto_start; target fields stay top-level.
    auto_start = (
        _maybe_autostart_queue(config)
        if result["cleared_to_ready"]
        else {"attempted": False, "reason": "not_ready_transition"}
    )
    return {**target, "auto_start": auto_start}


@app.get("/api/queue/{entry_id}/snapshot")
def get_queue_entry_snapshot(entry_id: str):
    """Return project snapshot for a queue entry.

    Reads the global pipeline_state.json (matches by project_path) and the
    project's roadmap.md to build a combined progress snapshot. All fields
    are optional — returns partial data gracefully when files are missing.
    """
    config = load_config()
    q = _read_queue_file(config)
    entry = next((e for e in q.get("queue", []) if e["id"] == entry_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="Queue entry not found")

    project_path = entry.get("project_path", "")

    # Run metrics from global pipeline_state.json (only if project_path matches)
    pipeline_status = None
    current_phase = None
    current_phase_raw_id = None
    current_agent = None
    planner_retries = None
    executor_retries = None
    reviewer_retries = None
    last_action_timestamp = None
    last_action = None
    sentinel_wait_started_at = None
    is_active_project = False

    ps_path = os.path.expanduser(config.get("pipeline_state_path") or "")
    ps = _read_json_file(ps_path) if os.path.exists(ps_path) else None
    if ps and os.path.realpath(ps.get("project_path", "")) == os.path.realpath(project_path):
        is_active_project = True
        pipeline_status = ps.get("pipeline_status")
        current_phase = ps.get("current_phase")
        current_phase_raw_id = ps.get("current_phase_raw_id")
        current_agent = ps.get("current_agent")
        planner_retries = ps.get("planner_retries", 0)
        executor_retries = ps.get("executor_retries", 0)
        reviewer_retries = ps.get("reviewer_retries", 0)
        last_action_timestamp = ps.get("last_action_timestamp")
        last_action = ps.get("last_action")
        sentinel_wait_started_at = ps.get("sentinel_wait_started_at")

    # Orchestrator liveness
    lock_path = _expand_lock_path(config)
    orchestrator_alive = False
    if lock_path:
        try:
            orchestrator_alive = _check_orchestrator_liveness(lock_path)
        except Exception:
            orchestrator_alive = False

    # Roadmap phase counts
    phases_total = 0
    phases_complete = 0
    current_phase_desc = None

    import glob as glob_mod
    roadmap_candidates = glob_mod.glob(os.path.join(project_path, "*oadmap*.md"))
    if roadmap_candidates:
        try:
            with open(roadmap_candidates[0], "r", errors="replace") as f:
                roadmap_content = f.read()
            phases_total, phases_complete = _roadmap_phase_checkbox_stats(roadmap_content)
            # Extract description for current phase
            if current_phase_raw_id:
                for line in roadmap_content.splitlines():
                    if f"`{current_phase_raw_id}`" in line:
                        parts = line.split(" | ")
                        if len(parts) >= 3:
                            desc = parts[-1].strip()
                            current_phase_desc = desc
                        break
        except Exception:
            pass

    # Per-entry escalation/advisory view + eligibility probes, computed by the SHARED
    # _compute_escalation_view helper against THIS entry's project (not the active one).
    # This is the fix: a parked ESCALATION entry now describes its OWN project — the same
    # project a command dispatched from the Queue targets. The merge/branch probes need
    # the entry's OWN phase id: the global pipeline_state.current_phase_raw_id is the
    # active project's, so a parked entry resolves its id from its own artifacts (else the
    # probes skip gracefully → Mark Complete stays disabled, conservative by design).
    _probe_raw_id = current_phase_raw_id if is_active_project else _resolve_entry_raw_id(project_path)
    _view = _compute_escalation_view(
        project_path,
        phase_state_path=None,        # derive from project_path → the entry's OWN phase_state
        queue_halted_reason=None,     # halt state is the active run's property, not a parked entry's
        current_phase_raw_id=_probe_raw_id,
    )
    # Apply the snapshot's existing default conventions at the return-dict layer:
    # escalation_resets → 0; the other present-only fields → None.
    escalation_resets = _view.get("escalation_resets", 0)
    # P1 Stage G2 — the snapshot return dict cherry-picks from _view (it does NOT spread it
    # like /api/state), so nuclear_resets must be lifted out explicitly or the Queue panel's
    # nuclear-button gate never sees it (and would never hide at the cap). Default 0, matching
    # escalation_resets, so the Queue gate reads a number, not undefined.
    nuclear_resets = _view.get("nuclear_resets", 0)
    last_error_code = _view.get("last_error_code")
    escalation_message = _view.get("escalation_message")
    escalation_trigger_reason = _view.get("escalation_trigger_reason")
    escalation_headline = _view.get("escalation_headline")
    escalation_advisory_status = _view.get("escalation_advisory_status")
    escalation_recommended_action = _view.get("escalation_recommended_action")
    skill_injected = _view.get("skill_injected")
    skill_agent = _view.get("skill_agent")
    waiting_for_human_at = _view.get("waiting_for_human_at")
    executor_output_exists = _view.get("executor_output_exists", False)
    planner_output_exists = _view.get("planner_output_exists", False)
    phase_branch_exists = _view.get("phase_branch_exists", False)
    merge_probe_passed = _view.get("merge_probe_passed", False)

    return {
        "id": entry["id"],
        "name": entry.get("name"),
        "project_path": project_path,
        "state": entry.get("state"),
        "started_at": entry.get("started_at"),
        "preflight_validated_at": entry.get("preflight_validated_at"),
        "phases_total": phases_total,
        "phases_complete": phases_complete,
        "current_phase_raw_id": current_phase_raw_id,
        "current_phase_desc": current_phase_desc,
        "current_phase": current_phase,
        "current_agent": current_agent,
        "planner_retries": planner_retries,
        "executor_retries": executor_retries,
        "reviewer_retries": reviewer_retries,
        "last_action_timestamp": last_action_timestamp,
        "last_action": last_action,
        "sentinel_wait_started_at": sentinel_wait_started_at,
        "pipeline_status": pipeline_status,
        "orchestrator_alive": orchestrator_alive,
        "is_active_project": is_active_project,
        "escalation_resets": escalation_resets,
        "nuclear_resets": nuclear_resets,
        "last_error_code": last_error_code,
        "escalation_message": escalation_message,
        "escalation_trigger_reason": escalation_trigger_reason,
        "escalation_headline": escalation_headline,
        "escalation_advisory_status": escalation_advisory_status,
        "escalation_recommended_action": escalation_recommended_action,
        "skill_injected": skill_injected,
        "skill_agent": skill_agent,
        "waiting_for_human_at": waiting_for_human_at,
        "executor_output_exists": executor_output_exists,
        "planner_output_exists": planner_output_exists,
        "phase_branch_exists": phase_branch_exists,
        "merge_probe_passed": merge_probe_passed,
    }


@app.post("/api/queue/{entry_id}/relaunch")
def post_queue_entry_relaunch(entry_id: str):
    """Spawn orchestrator for an existing queue entry without resetting pipeline state.

    Used when the orchestrator process has died and needs to be restarted (e.g. the
    dashboard 'Resume banked answer' control for a parked escalation). Does NOT call
    _run_init_project or reset pipeline_state.json. Spawns with ``--revive <entry_id>`` (F2)
    so a parked entry resumes its ESCALATED phase and applies any banked command — instead of
    the phase-0 reset ``--project-path`` alone would trigger, which orphaned the banked
    command. Returns 409 if orchestrator is already alive.
    """
    config = load_config()
    q = _read_queue_file(config)
    entry = next((e for e in q.get("queue", []) if e["id"] == entry_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="Queue entry not found")

    lock_path = _expand_lock_path(config)
    if lock_path:
        try:
            if _check_orchestrator_liveness(lock_path):
                raise HTTPException(status_code=409, detail="Orchestrator is already running")
        except HTTPException:
            raise
        except Exception:
            pass

    result = _spawn_orchestrator(entry["project_path"], config, revive_entry_id=entry_id)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to spawn orchestrator"))
    return {"ok": True}


@app.post("/api/queue/{entry_id}/revalidate")
async def post_queue_entry_revalidate(entry_id: str):
    """Re-run preflight checks for a queue entry and update its state.

    Updates preflight_validated_at always. Transitions SKIPPED_PENDING → READY
    if all checks pass, or READY → SKIPPED_PENDING if any check fails.
    Returns {"ok": bool, "checks": [...], "entry": {...}}.
    """
    from datetime import datetime, timezone as tz

    config = load_config()
    # Resolve the target's project path once for the (expensive) preflight — project_path is
    # immutable after add, so this read need not be inside the CAS loop.
    target0 = next((e for e in _read_queue_file(config).get("queue", []) if e["id"] == entry_id), None)
    if target0 is None:
        raise HTTPException(status_code=404, detail="Queue entry not found")

    checks = _run_preflight_checks(target0["project_path"])
    has_fail = any(c.get("status") == "fail" for c in checks)
    now = datetime.now(tz.utc).isoformat()

    def _apply(q):
        target = next((e for e in q.get("queue", []) if e["id"] == entry_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Queue entry not found")
        target["preflight_validated_at"] = now
        if has_fail and target.get("state") == "READY":
            target["state"] = "SKIPPED_PENDING"
        elif not has_fail and target.get("state") == "SKIPPED_PENDING":
            target["state"] = "READY"
        return dict(target)

    target = _mutate_queue_file(config, _apply)
    # Auto-start if this revalidation left an eligible READY row in an idle auto queue
    # (no-op when the row stayed SKIPPED_PENDING / not in auto mode). Non-raising; additive.
    auto_start = _maybe_autostart_queue(config)
    return {"ok": True, "checks": checks, "entry": target, "auto_start": auto_start}


# Agent IDs and relative paths kept in sync with install.sh (agent workspace deploy step).
_WORKSPACE_SYNC_AGENT_IDS = (
    "planner",
    "executor",
    "reviewer",
    "escalation",
    "prd-creator",
    "roadmap-converter",
)
_WORKSPACE_SYNC_CORE_DOCS = ("IDENTITY.md", "SOUL.md", "TOOLS.md", "AGENTS.md", "USER.md")


def _sync_agent_workspaces(config: dict) -> dict:
    """Copy agent guidance from the repo into OpenClaw workspace dirs (install.sh semantics).

    Copies when the repository file is newer than the workspace copy, or the workspace
    file is missing. Skips when the destination is newer (operator-local customization).

    Returns:
        {"synced": int, "skipped": int, "errors": list[str]}
    """
    synced = 0
    skipped = 0
    errors: list[str] = []
    log = logging.getLogger("autodev.workspace_sync")

    try:
        repo = os.path.expanduser(str(config.get("autodev_repo_path") or _AUTODEV_UI_ROOT))
        openclaw = os.path.expanduser(str(config.get("openclaw_root") or resolve_openclaw_root()))
    except Exception as e:
        return {"synced": 0, "skipped": 0, "errors": [f"resolve paths: {e}"]}

    def _copy_if_newer(src: str, dst: str, label: str) -> None:
        nonlocal synced, skipped
        if not os.path.isfile(src):
            return
        _parent = os.path.dirname(dst)
        if _parent:
            try:
                os.makedirs(_parent, exist_ok=True)
            except OSError as e:
                errors.append(f"{label}: makedirs {e}")
                return
        if os.path.isfile(dst) and os.path.getmtime(src) <= os.path.getmtime(dst):
            skipped += 1
            log.debug("[WORKSPACE-SYNC] skip %s (dest newer)", label)
            return
        try:
            shutil.copy2(src, dst)
            synced += 1
            log.info("[WORKSPACE-SYNC] synced %s", label)
        except OSError as e:
            errors.append(f"{label}: {e}")

    for agent in _WORKSPACE_SYNC_AGENT_IDS:
        src_dir = os.path.join(repo, "autodev", "agents", agent)
        if not os.path.isdir(src_dir):
            continue
        dst_dir = os.path.join(openclaw, f"workspace-{agent}")
        try:
            os.makedirs(dst_dir, exist_ok=True)
        except OSError as e:
            errors.append(f"workspace-{agent}: mkdir {e}")
            continue

        for doc in _WORKSPACE_SYNC_CORE_DOCS:
            _copy_if_newer(
                os.path.join(src_dir, doc),
                os.path.join(dst_dir, doc),
                f"{agent}/{doc}",
            )

        if agent in ("planner", "executor", "reviewer"):
            _copy_if_newer(
                os.path.join(src_dir, "HEARTBEAT.md"),
                os.path.join(dst_dir, "HEARTBEAT.md"),
                f"{agent}/HEARTBEAT.md",
            )

        if agent in ("prd-creator", "roadmap-converter", "escalation"):
            try:
                os.makedirs(os.path.join(dst_dir, "skills"), exist_ok=True)
            except OSError as e:
                errors.append(f"workspace-{agent}/skills: {e}")

        if agent == "prd-creator":
            prd_src = os.path.join(
                repo, "autodev", "skill-library", "prd-creator", "readiness-reviewer", "SKILL.md"
            )
            prd_dst = os.path.join(
                dst_dir, "skills", "readiness-reviewer", "SKILL.md"
            )
            if os.path.isfile(prd_src):
                try:
                    os.makedirs(os.path.join(dst_dir, "skills", "readiness-reviewer"), exist_ok=True)
                except OSError as e:
                    errors.append(f"prd-creator/readiness-reviewer: makedirs {e}")
                _copy_if_newer(prd_src, prd_dst, "prd-creator/skills/readiness-reviewer/SKILL.md")

        if agent == "escalation":
            esc_src = os.path.join(
                repo, "autodev", "agents", "escalation", "skills", "escalation-summary", "SKILL.md"
            )
            esc_dst = os.path.join(
                dst_dir, "skills", "escalation-summary", "SKILL.md"
            )
            if os.path.isfile(esc_src):
                try:
                    os.makedirs(os.path.join(dst_dir, "skills", "escalation-summary"), exist_ok=True)
                except OSError as e:
                    errors.append(f"escalation/escalation-summary: makedirs {e}")
                _copy_if_newer(esc_src, esc_dst, "escalation/skills/escalation-summary/SKILL.md")

        if agent == "roadmap-converter":
            for skill in ("roadmap-generation",):
                ssrc = os.path.join(
                    repo, "autodev", "skill-library", "roadmap-converter", skill, "SKILL.md"
                )
                sdst = os.path.join(dst_dir, "skills", skill, "SKILL.md")
                if os.path.isfile(ssrc):
                    try:
                        os.makedirs(
                            os.path.join(dst_dir, "skills", skill), exist_ok=True
                        )
                    except OSError as e:
                        errors.append(f"roadmap-converter/{skill}: makedirs {e}")
                    _copy_if_newer(
                        ssrc, sdst, f"roadmap-converter/skills/{skill}/SKILL.md"
                    )

    return {"synced": synced, "skipped": skipped, "errors": errors}


def _check_installer_status(config: dict) -> dict:
    """Run read-only installer health checks and return status dict.

    Checks (all read-only, no writes):
    - OpenClaw root directory exists
    - openclaw.json exists and is parseable
    - All six pipeline agents registered in openclaw.json agents.list
    - Agent workspace directories for planner, executor, reviewer, escalation, prd-creator, roadmap-converter
    - Conversion prompt file exists
    - exec-approvals.json stale path detection

    Returns:
        {
            "setup_complete": bool,  # True iff marker exists AND missing_items is empty
            "missing_items": [str, ...]
        }
    """
    missing_items = []

    # OpenClaw install lives under ~/.openclaw (OPENCLAW_ROOT), not the git repo path.
    openclaw_root = config.get("openclaw_root") or os.path.expanduser("~/.openclaw")
    repo_path = config.get("autodev_repo_path") or os.path.expanduser("~/.openclaw")
    openclaw_json_path = os.path.join(openclaw_root, "openclaw.json")
    exec_approvals_path = os.path.join(openclaw_root, "exec-approvals.json")

    # 1. OpenClaw root directory
    if not os.path.isdir(openclaw_root):
        missing_items.append("openclaw_root")

    # 2. openclaw.json existence
    if not os.path.isfile(openclaw_json_path):
        missing_items.append("openclaw_json")
    else:
        # 3. Pipeline agents in openclaw.json (install.sh step 8 registers these)
        _pipeline_agent_ids = (
            "planner",
            "executor",
            "reviewer",
            "escalation",
            "prd-creator",
            "roadmap-converter",
        )
        try:
            with open(openclaw_json_path, "r", encoding="utf-8") as f:
                oc_data = json.load(f)
            agents_list = oc_data.get("agents", {}).get("list", [])
            ids = {a.get("id") for a in agents_list if isinstance(a, dict)}
            for _aid in _pipeline_agent_ids:
                if _aid not in ids:
                    missing_items.append(f"openclaw_agent_{_aid.replace('-', '_')}")
        except Exception:
            for _aid in _pipeline_agent_ids:
                missing_items.append(f"openclaw_agent_{_aid.replace('-', '_')}")

    # 4. Agent workspace directories
    for agent in (
        "planner",
        "executor",
        "reviewer",
        "escalation",
        "prd-creator",
        "roadmap-converter",
    ):
        ws = os.path.join(openclaw_root, f"workspace-{agent}")
        if not os.path.isdir(ws):
            missing_items.append(f"workspace-{agent}")

    # 5. Conversion prompt file (config path or bundled repo default)
    conversion_prompt = config.get("conversion_prompt_path", "")
    if conversion_prompt:
        conversion_prompt = os.path.expanduser(conversion_prompt)
    bundled_prompt = os.path.join(
        _AUTODEV_UI_ROOT, "autodev", "prompts", "prd-to-roadmap-conversion.txt"
    )
    if (not conversion_prompt or not os.path.isfile(conversion_prompt)) and not os.path.isfile(
        bundled_prompt
    ):
        missing_items.append("conversion_prompt")

    # 6. exec-approvals.json stale path detection (read-only, report only)
    if os.path.isfile(exec_approvals_path):
        try:
            with open(exec_approvals_path, "r", encoding="utf-8") as f:
                raw = f.read()
            # Any gate_scripts path not under autodev_repo_path (repo root) is stale
            import re as _re
            gate_paths = _re.findall(r'"([^"]*gate_scripts[^"]*)"', raw)
            stale = [p for p in gate_paths if not p.startswith(repo_path)]
            if stale:
                missing_items.append("exec_approvals_stale_paths")
        except Exception:
            pass

    setup_marker = os.path.expanduser("~/.autodev_setup_complete")
    marker_exists = os.path.exists(setup_marker)
    setup_complete = marker_exists and len(missing_items) == 0

    return {"setup_complete": setup_complete, "missing_items": missing_items}


@app.get("/api/setup/status")
def get_setup_status():
    """Read-only installer health check.

    Returns:
        {
            "setup_complete": bool,
            "missing_items": [str, ...]  — empty if all checks pass
        }

    missing_items values:
        "openclaw_root"             — OPENCLAW_ROOT directory not found
        "openclaw_json"             — openclaw.json missing from OPENCLAW_ROOT
        "openclaw_agent_<id>"       — pipeline agent missing from openclaw.json agents.list
                                    (id uses underscores, e.g. openclaw_agent_prd_creator)
        "workspace-{agent}"         — agent workspace directory missing
        "conversion_prompt"         — PRD-to-roadmap conversion prompt file missing
        "exec_approvals_stale_paths" — exec-approvals.json has outdated gate script paths
    """
    config = load_config()
    return _check_installer_status(config)


@app.post("/api/setup/switch-project")
async def post_setup_switch_project(request: Request):
    """Validate and optionally switch active project (pipeline must be STOPPED).

    Body: repo_path, optional roadmap_seed/prd_content, confirm_roadmap_archive, keep_filename,
          confirm_destructive (list of basenames), start_orchestrator (bool).
    """
    allowed, cur_status = _project_switch_allowed()
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
    verification_content = body.get("verification_content")
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

    mat = _preflight_materialize(repo_abs, roadmap_seed, prd_content, verification_content)
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

    spawned = _spawn_orchestrator(repo_abs, config)
    if not spawned.get("ok"):
        return {
            "ok": False,
            "checks": all_pre,
            "coherence": {"ok": True, "issues": []},
            "error": spawned.get("error") or "Failed to spawn orchestrator",
        }

    try:
        _write_json_atomic(pipeline_state_path, _clean_pipeline_state_for_project(repo_abs))
    except OSError as exc:
        return {
            "ok": False,
            "checks": all_pre,
            "coherence": {"ok": True, "issues": []},
            "error": f"Could not write pipeline_state.json: {exc}",
        }

    try:
        _queue_mark_matching_entry_active(config, repo_abs)
    except Exception:
        pass

    return {
        "ok": True,
        "checks": all_pre,
        "coherence": {"ok": True, "issues": []},
        "started": True,
    }


# ─── Launch sequence ──────────────────────────────────────────────────────────

def _run_init_project(
    repo_path: str,
    roadmap_seed: str,
    prd_content=None,
    verification_content=None,
) -> dict:
    """Initialize a project directory (Mode A: new repo, Mode B: existing repo).

    Mode A: .git does NOT exist → create full structure, git init, initial commit.
    Mode B: .git exists → create only missing files, append missing gitignore entries.

    ``verification_content`` (Stage C): if provided, written to
    ``<repo>/verification.md`` after PRD. If absent AND no verification.md
    exists on disk, init refuses with a hint to re-run conversion from the
    Ideas screen. Strict per §2.9 — no project can be staged without a
    verification doc.

    Returns {"ok": bool, "error": str|null}
    """
    import subprocess
    import shutil

    repo_path = os.path.expanduser(repo_path)
    name = os.path.basename(repo_path.rstrip("/"))
    now = datetime.utcnow().isoformat() + "Z"
    mode = "B" if os.path.exists(os.path.join(repo_path, ".git")) else "A"

    # Strict check before any filesystem writes: if no verification doc is
    # available (neither in the request body nor already on disk), refuse.
    _ver_text = (verification_content or "").strip()
    _existing_ver = os.path.join(repo_path, "verification.md")
    if not _ver_text and not os.path.exists(_existing_ver):
        return {
            "ok": False,
            "error": (
                "verification.md is required — re-run conversion from the Ideas "
                "screen to generate it."
            ),
        }
    if _ver_text:
        _ver_val = _validate_verification_content(_ver_text)
        if not _ver_val["valid"]:
            errs = "; ".join(e["message"] for e in _ver_val["errors"][:3])
            return {"ok": False, "error": f"verification.md invalid: {errs}"}

    def atomic_write(path: str, content: str):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(content)
        os.replace(tmp, path)

    try:
        if mode == "A":
            # Step 1: directory structure (pipeline artifacts under .autodev/pipeline/)
            art = os.path.join(repo_path, ".autodev", "pipeline")
            os.makedirs(os.path.join(art, "phases"), exist_ok=True)
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
            atomic_write(os.path.join(art, "pipeline.json"), json.dumps(pipeline, indent=2))

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
            # Step 5b: verification.md (Stage C). Strict — already validated above.
            ver_path = os.path.join(repo_path, "verification.md")
            if _ver_text:
                atomic_write(ver_path, _ver_text + ("\n" if not _ver_text.endswith("\n") else ""))
            lessons_path = os.path.join(art, "lessons.md")
            if not os.path.exists(lessons_path):
                atomic_write(lessons_path, "# Lessons\n\n_Hard-won insights go here._\n")
            metrics_path = os.path.join(art, "metrics.jsonl")
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
            # Create only missing structure (pipeline artifacts under .autodev/pipeline/)
            art = os.path.join(repo_path, ".autodev", "pipeline")
            os.makedirs(os.path.join(art, "phases"), exist_ok=True)
            os.makedirs(os.path.join(repo_path, "tests"), exist_ok=True)
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
                ("lessons.md", "# Lessons\n\n_Hard-won insights go here._\n"),
            ]:
                path = os.path.join(art, fname)
                if not os.path.exists(path):
                    atomic_write(path, content)

            roadmap_path_b = os.path.join(repo_path, "roadmap.md")
            if not os.path.exists(roadmap_path_b):
                atomic_write(roadmap_path_b, roadmap_seed)

            prd_path = os.path.join(repo_path, "prd.md")
            if prd_content and str(prd_content).strip():
                atomic_write(prd_path, str(prd_content))
            elif not os.path.exists(prd_path):
                atomic_write(prd_path, "# PRD\n\n_To be completed._\n")

            # Stage C: verification.md — write when provided. On-disk doc is
            # left untouched in Mode B (re-running launch on an existing repo
            # should not silently overwrite).
            ver_path_b = os.path.join(repo_path, "verification.md")
            if _ver_text and not os.path.exists(ver_path_b):
                atomic_write(ver_path_b, _ver_text + ("\n" if not _ver_text.endswith("\n") else ""))

            metrics_path = os.path.join(art, "metrics.jsonl")
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

        # Step 8 (both modes): set pipeline-project symlink (repo-local runtime by default)
        _cfg = load_config()
        symlink_path = os.path.expanduser(_cfg.get("project_dir_path") or "")
        if not symlink_path:
            symlink_path = os.path.join(
                os.path.expanduser(_cfg.get("openclaw_root") or "~/.openclaw"),
                "pipeline-project",
            )
        sym_parent = os.path.dirname(symlink_path)
        if sym_parent:
            os.makedirs(sym_parent, exist_ok=True)
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

    Body: {"repo_path": str, "roadmap_seed": str, "prd_content": optional,
           "verification_content": optional, "completion_review": bool}
    Returns: {"ok": bool, "error": str|null}; 409 with code orchestrator_running if lock held.
    """
    body = await request.json()
    repo_path = body.get("repo_path", "")
    roadmap_seed = body.get("roadmap_seed", "")
    prd_content = body.get("prd_content")
    verification_content = body.get("verification_content")
    completion_review = bool(body.get("completion_review", False))
    if not repo_path:
        raise HTTPException(status_code=422, detail="repo_path is required")

    result = _run_init_project(repo_path, roadmap_seed, prd_content, verification_content)
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

    spawned = _spawn_orchestrator(project_real, config)
    if not spawned.get("ok"):
        return {"ok": False, "error": spawned.get("error") or "Failed to spawn orchestrator"}

    try:
        _write_json_atomic(pipeline_state_path, _clean_pipeline_state_for_project(project_real))
    except OSError as exc:
        return {"ok": False, "error": f"Could not write pipeline_state.json: {exc}"}

    try:
        _queue_mark_matching_entry_active(config, project_real)
    except Exception:
        pass

    # W5-G: ensure queue entry exists with completion_review flag.
    # _queue_mark_matching_entry_active early-returns when the queue is empty
    # (Launch Now with no prior Add to Queue call), leaving no entry for the
    # orchestrator to read the flag from. Synthesize a minimal entry in that case.
    if os.path.expanduser(config.get("pipeline_queue_path") or ""):
        try:
            import uuid as _uuid_mod
            from datetime import datetime, timezone as _tz

            def _apply(_q):
                _entries = _q.get("queue", [])
                _has_active = any(
                    e.get("state") == "ACTIVE"
                    and os.path.realpath(os.path.expanduser(e.get("project_path", ""))) == project_real
                    for e in _entries
                )
                if not _has_active:
                    _now = datetime.now(_tz.utc).isoformat()
                    _entries.append({
                        "id": str(_uuid_mod.uuid4()),
                        "project_path": project_real,
                        "idea_id": None,
                        "name": os.path.basename(project_real),
                        "state": "ACTIVE",
                        "position": len(_entries) + 1,
                        "parent_id": None,
                        "added_at": _now,
                        "started_at": _now,
                        "completed_at": None,
                        "blocked_at": None,
                        "skip_count": 0,
                        "preflight_validated_at": _now,
                        "completion_review": completion_review,
                        "notes": "",
                    })
                else:
                    # Entry exists — update the completion_review flag on it
                    for _e in _entries:
                        if (
                            _e.get("state") == "ACTIVE"
                            and os.path.realpath(os.path.expanduser(_e.get("project_path", ""))) == project_real
                        ):
                            _e["completion_review"] = completion_review
                _q["queue"] = _entries
                return True

            # Best-effort (a CAS exhaustion is swallowed like any other error below).
            _mutate_queue_file(config, _apply)
        except Exception:
            pass

    return {"ok": True, "error": None}


# ---------------------------------------------------------------------------
# W5-C: GET /api/completion-report
# ---------------------------------------------------------------------------

@app.get("/api/completion-report")
async def get_completion_report():
    """Return the completion_report.md content for the active project.

    Returns {"found": bool, "content": str, "mtime": float|null}.
    mtime is epoch seconds so the UI can detect "from previous run" staleness.
    """
    config = load_config()
    project_dir = _expand_project_dir_config(config)
    if not project_dir or not os.path.isdir(project_dir):
        return {"found": False, "content": "", "mtime": None}
    report_path = os.path.join(project_dir, "completion_report.md")
    if not os.path.exists(report_path):
        return {"found": False, "content": "", "mtime": None}
    mtime = os.path.getmtime(report_path)
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return {"found": False, "content": "", "mtime": None}
    return {"found": True, "content": content, "mtime": mtime}


# ---------------------------------------------------------------------------
# W5-E: POST /api/completion-review/{project}
# ---------------------------------------------------------------------------

@app.post("/api/completion-review/{project}")
async def post_completion_review_trigger(project: str):
    """On-demand completion review trigger for projects already in PIPELINE_COMPLETE.

    Acquires pipeline.lock before invoking the reviewer to prevent workspace
    contention with any running orchestrator. Returns 409 if lock held.

    Returns {"triggered": bool, "session_key": str}.
    """
    config = load_config()

    # Gate 1: reject if orchestrator is running (lock held)
    lock_path = _expand_lock_path(config)
    if lock_path and _check_orchestrator_liveness(lock_path):
        return JSONResponse(
            status_code=409,
            content={
                "error": "queue_active",
                "detail": "Stop or drain the queue before generating completion docs.",
            },
        )

    # Gate 2: project must be in PIPELINE_COMPLETE state
    ps_path = config.get("pipeline_state_path")
    ps_path = os.path.expanduser(ps_path) if ps_path else None
    pipeline_status = None
    if ps_path and os.path.exists(ps_path):
        try:
            with open(ps_path, "r", encoding="utf-8") as _f:
                _ps = json.load(_f)
            pipeline_status = _ps.get("pipeline_status")
        except Exception:
            pass
    if pipeline_status != "PIPELINE_COMPLETE":
        return JSONResponse(
            status_code=409,
            content={
                "error": "not_complete",
                "detail": "Project is not in PIPELINE_COMPLETE state.",
            },
        )

    session_key = f"pipeline:completion:{project}:reviewer"
    project_dir = _expand_project_dir_config(config)
    openclaw_root = os.path.expanduser(config.get("openclaw_root") or "~/.openclaw")

    # Fire-and-forget: inject skill, clean workspace, trigger webhook, return immediately.
    # The UI polls GET /api/completion-report on an interval to detect when the report appears.
    #
    # Walkthrough structure must stay in sync with orchestrator._run_completion_review
    # (autodev/pipeline/orchestrator.py). Both code paths are guarded by tests asserting
    # Open-Terminal / cd / fresh-terminal / per-command-fenced-block requirements.
    _p = "pipeline-project/.autodev/pipeline"
    _project_abs_path = os.path.realpath(project_dir) if project_dir else ""
    _completion_message = (
        f"Begin completion documentation. Read the project source and git diff to understand "
        f"what was built. Produce three artifacts at the project root: README.md updates, "
        f"a CHANGELOG.md entry, and completion_report.md.\n\n"
        f"completion_report.md must walk a non-technical user through running the project "
        f"from a fresh terminal with no prior context — assume they have not opened a shell "
        f"yet and are not in any particular directory. Structure it as:\n"
        f"  1. What was built (one short paragraph).\n"
        f"  2. How to run it — write this as numbered steps. Step 1 must be: "
        f"'Open Terminal (macOS/Linux) or PowerShell (Windows)'. Step 2 must be the command "
        f"`cd {_project_abs_path}` in its own fenced ``` code block (use the literal absolute "
        f"path shown — never substitute a generic placeholder for the real path). "
        f"Then one fenced code block per command: install dependencies, build, run, test. "
        f"Reference the actual scripts present in package.json / Makefile / pyproject.toml / "
        f"etc. — only commands a user can paste verbatim.\n"
        f"  3. Files changed (brief list).\n"
        f"  4. Suggested next steps (2–4 bullets).\n\n"
        f"Every shell command must live in its own ``` fenced code block so the UI can render "
        f"one Copy button per command. Do not group multiple commands in one block.\n\n"
        f"Then write {_p}/reviewer_output.done."
    )
    try:
        _artifacts_dir = os.path.join(project_dir, ".autodev", "pipeline") if project_dir else ""
        token = config.get("hooks_token") or os.environ.get("AUTODEV_HOOKS_TOKEN", "")

        _sm = SkillManager(openclaw_root)
        openclaw_cfg_path = os.path.join(openclaw_root, "openclaw.json")
        _oc_cfg = {}
        if os.path.exists(openclaw_cfg_path):
            try:
                with open(openclaw_cfg_path, "r", encoding="utf-8") as _f:
                    _oc_cfg = json.load(_f)
            except Exception:
                pass

        _sm.inject_skill("COMPLETE-R0", "reviewer", _oc_cfg)
        if _artifacts_dir:
            cleanup_output_files(_artifacts_dir, "reviewer")

        invoke_agent_webhook("reviewer", session_key, token, message=_completion_message)
    except Exception as _exc:
        print(f"[W5-E] Completion review invocation warning: {_exc}")

    return {"triggered": True, "session_key": session_key}