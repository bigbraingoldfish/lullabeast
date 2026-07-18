#!/usr/bin/env python3
"""Lullabeast dashboard MCP server (stdio transport).

Exposes the UI dashboard's control + observability HTTP API (``ui/server.py``)
as MCP tools, so an MCP client — Claude Code, an OpenClaw agent, or any other
agent runtime — gets full agentic control of and visibility into the pipeline:
live state, events, logs, roadmap, metrics, doctor health checks, the project
queue, escalation answers, stop/resume/launch, and a guarded read-only escape
hatch for every other ``GET /api/*`` endpoint.

Design constraints (mirroring the installer modules):

- **Stdlib-only.** The server may be spawned by an MCP client outside the
  repo's virtualenv, so it depends on nothing beyond the standard library.
  MCP's stdio transport is newline-delimited JSON-RPC 2.0, which needs no SDK.
- **Thin proxy, zero business logic.** Every tool maps 1:1 onto a dashboard
  endpoint; validation, liveness 409s, queue CAS, and token auth all stay
  server-side in ``ui/server.py``. This module never touches pipeline state
  files directly.
- **Same auth story as any other API client.** ``AUTODEV_UI_TOKEN`` (env, or
  ``ui/config.json`` ``ui_token``) rides as ``Authorization: Bearer``; with no
  token configured the dashboard's legacy loopback-open mode applies.

Run it with ``python3 -m autodev.mcp.dashboard_server`` (from the repo root) or
by absolute script path (a sys.path bootstrap below makes both work). Wiring
examples live in ``autodev/mcp/README.md`` and ``.mcp.json.example``.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# Repo root is three directories up (the same depth rule as autodev/pipeline/).
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Direct-script execution (`python3 /path/to/dashboard_server.py`) has no
# package context, so make the `autodev` package importable first.
if __package__ in (None, ""):  # pragma: no cover - exercised only as a script
    sys.path.insert(0, _REPO_ROOT)

from autodev.pipeline.env_resolvers import load_repo_env_file  # noqa: E402

SERVER_NAME = "lullabeast-dashboard"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-06-18"

DEFAULT_UI_PORT = 18790
DEFAULT_HTTP_TIMEOUT_SECONDS = 60.0

# Mirrors ui/server.py VALID_COMMANDS (the server re-validates; this copy only
# gives the agent a typed enum + a fast local error instead of a round-trip 400).
ESCALATION_COMMANDS = [
    "RETRY",
    "RESET_EXECUTION",
    "RESET_PHASE",
    "RESET_REVIEWER",
    "SKIP",
    "PROCEED",
    "STOP",
    "NUCLEAR_RESET",
]


class ToolError(Exception):
    """A tool-argument problem detected locally (never sent to the dashboard)."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def resolve_repo_root() -> str:
    """Repo root: ``AUTODEV_REPO_PATH`` env wins, else module-relative."""
    env = (os.environ.get("AUTODEV_REPO_PATH") or "").strip()
    return os.path.expanduser(env) if env else _REPO_ROOT


def _read_ui_config(repo_root: str) -> dict:
    path = os.path.join(repo_root, "ui", "config.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_dashboard_config() -> dict:
    """Resolve ``{base_url, token, timeout}`` for dashboard HTTP calls.

    Resolution order mirrors the dashboard's own config doctrine:

    - base URL: ``AUTODEV_UI_URL`` env, else ``http://127.0.0.1:<port>`` with
      the port from ``AUTODEV_UI_PORT`` / ``UI_PORT`` env, else the
      ``ui/config.json`` ``port`` key, else 18790.
    - token: ``AUTODEV_UI_TOKEN`` env, else ``ui/config.json`` ``ui_token``.
    - timeout: ``AUTODEV_MCP_HTTP_TIMEOUT`` (seconds; garbage reads as the
      60 s default — launch/preflight endpoints run git operations).
    """
    repo_root = resolve_repo_root()
    ui_config = _read_ui_config(repo_root)

    base_url = (os.environ.get("AUTODEV_UI_URL") or "").strip()
    if not base_url:
        port = DEFAULT_UI_PORT
        for candidate in (
            os.environ.get("AUTODEV_UI_PORT"),
            os.environ.get("UI_PORT"),
            ui_config.get("port"),
        ):
            if candidate in (None, ""):
                continue
            try:
                port = int(str(candidate).strip())
                break
            except (TypeError, ValueError):
                continue
        base_url = "http://127.0.0.1:%d" % port

    token = (os.environ.get("AUTODEV_UI_TOKEN") or "").strip()
    if not token:
        token = str(ui_config.get("ui_token") or "").strip()

    timeout = DEFAULT_HTTP_TIMEOUT_SECONDS
    raw_timeout = (os.environ.get("AUTODEV_MCP_HTTP_TIMEOUT") or "").strip()
    if raw_timeout:
        try:
            parsed = float(raw_timeout)
            if parsed > 0:
                timeout = parsed
        except ValueError:
            pass

    return {"base_url": base_url.rstrip("/"), "token": token, "timeout": timeout}


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

def http_request(method: str, path: str, *, query: dict | None = None,
                 body: dict | None = None, config: dict | None = None):
    """Issue one dashboard API call; return ``(status_code, parsed_payload)``.

    HTTP error statuses are returned (with their JSON detail), never raised —
    a 409 liveness refusal is a meaningful answer for the agent, not a crash.
    Transport failures (connection refused, timeout) propagate to the caller.
    """
    config = config or resolve_dashboard_config()
    url = config["base_url"] + path
    if query:
        filtered = {k: v for k, v in query.items() if v is not None}
        if filtered:
            url += "?" + urllib.parse.urlencode(filtered)

    headers = {"Accept": "application/json"}
    if config.get("token"):
        headers["Authorization"] = "Bearer " + config["token"]

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=config["timeout"]) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as err:
        status = err.code
        raw = err.read()

    text = raw.decode("utf-8", "replace") if raw else ""
    if not text:
        return status, {}
    try:
        return status, json.loads(text)
    except ValueError:
        return status, {"raw": text[:8000]}


# ---------------------------------------------------------------------------
# Tool catalogue
# ---------------------------------------------------------------------------

def _tool(name, description, method, path, *, properties=None, required=(),
          path_params=(), query_params=(), read_only=False, destructive=False):
    return {
        "name": name,
        "description": description,
        "method": method,
        "path": path,
        "properties": properties or {},
        "required": list(required),
        "path_params": list(path_params),
        "query_params": list(query_params),
        "read_only": read_only,
        "destructive": destructive,
    }


_ENTRY_ID_PROP = {
    "entry_id": {
        "type": "string",
        "description": "Queue entry id (from queue_list). Synthetic 'ingest-*' rows are read-only.",
    },
}

TOOL_SPECS = [
    # -- Visibility -------------------------------------------------------
    _tool(
        "pipeline_state",
        "Live pipeline snapshot from GET /api/state: pipeline_status, current agent/phase, "
        "escalation view (reason, advisory, reset budgets), last poll/attempt outcome, live "
        "token/cost accumulators, agent activity age, run identity, and git-recover hints. "
        "The primary situational-awareness call — check it before any control action.",
        "GET", "/api/state", read_only=True,
    ),
    _tool(
        "pipeline_events",
        "Recent structured pipeline events (gate passes/fails, escalations, aborts, queue "
        "transitions, operator actions) from the durable pipeline_events.jsonl, newest-aware "
        "pagination via limit/offset.",
        "GET", "/api/events",
        properties={
            "limit": {"type": "integer", "description": "Max events to return (default 30)."},
            "offset": {"type": "integer", "description": "Events to skip (default 0)."},
        },
        query_params=("limit", "offset"), read_only=True,
    ),
    _tool(
        "pipeline_log_tail",
        "Tail of the orchestrator process log (stdout/stderr of the pipeline loop) — the "
        "full-detail diagnostic view behind the curated event feed.",
        "GET", "/api/log/tail",
        properties={
            "lines": {"type": "integer", "description": "Max lines from the end (default 500)."},
        },
        query_params=("lines",), read_only=True,
    ),
    _tool(
        "roadmap",
        "The active project's parsed roadmap: every phase with its checkbox state, so "
        "progress and the next pending phase are readable at a glance.",
        "GET", "/api/roadmap", read_only=True,
    ),
    _tool(
        "metrics_summary",
        "Per-phase and run-level metrics for the active project: durations, executor "
        "attempts, escalations, cost and token totals with per-role and token-class "
        "breakdowns, plus pain signals (gate warnings, resets, reachability).",
        "GET", "/api/metrics-summary", read_only=True,
    ),
    _tool(
        "doctor_report",
        "Run the read-only installer doctor (26 health checks: env paths, OpenClaw config, "
        "gateway, plugin, tokens, ports, models) and return the full check list with "
        "per-item fix hints. Never mutates anything.",
        "GET", "/api/doctor", read_only=True,
    ),
    _tool(
        "queue_list",
        "Full project queue with dependency tree, next-eligible entry, per-entry summary "
        "blocks (phase counts, cost/token totals, parked agent, banked-answer flag) and "
        "live status for the active entry.",
        "GET", "/api/queue", read_only=True,
    ),
    _tool(
        "queue_status",
        "Compact queue status: queue_mode (auto/manual), active entry, halted reason.",
        "GET", "/api/queue/status", read_only=True,
    ),
    _tool(
        "queue_snapshot",
        "Detailed snapshot of one queue entry: escalation view, preflight results, "
        "per-phase metrics projection (metrics_phases), cost/token totals.",
        "GET", "/api/queue/{entry_id}/snapshot",
        properties=dict(_ENTRY_ID_PROP), required=("entry_id",),
        path_params=("entry_id",), read_only=True,
    ),
    _tool(
        "queue_report",
        "Completed-project report for a queue entry: full metrics summary, "
        "completion_report.md content, and roadmap checkbox counts.",
        "GET", "/api/queue/{entry_id}/report",
        properties=dict(_ENTRY_ID_PROP), required=("entry_id",),
        path_params=("entry_id",), read_only=True,
    ),
    _tool(
        "completion_report",
        "The active project's completion_report.md (found flag, content, mtime).",
        "GET", "/api/completion-report", read_only=True,
    ),
    _tool(
        "dashboard_get",
        "Escape hatch: issue an arbitrary read-only GET against any dashboard /api/ "
        "endpoint not covered by a dedicated tool (e.g. /api/queue/status, "
        "/api/setup/status, /api/ideas, /api/metrics-global, /api/models/roles). "
        "The path must start with /api/.",
        "GET", "",
        properties={
            "path": {"type": "string",
                     "description": "Endpoint path starting with /api/, e.g. /api/setup/status."},
            "query": {"type": "object",
                      "description": "Optional query parameters as a flat key→value object."},
        },
        required=("path",), read_only=True,
    ),
    # -- Control ----------------------------------------------------------
    _tool(
        "answer_escalation",
        "Answer a pipeline escalation (POST /api/command) while the pipeline is "
        "WAITING_FOR_HUMAN — or bank the answer for a parked queue entry via "
        "target_project_path. RETRY resumes the in-flight agent; RESET_EXECUTION / "
        "RESET_PHASE / RESET_REVIEWER consume the capped reset budget; SKIP marks the "
        "phase skipped; PROCEED marks it complete; STOP halts. NUCLEAR_RESET is the "
        "destructive last resort (git reset --hard of the phase, capped at 2). "
        "Check pipeline_state first for the escalation reason and remaining budgets.",
        "POST", "/api/command",
        properties={
            "command": {"type": "string", "enum": ESCALATION_COMMANDS,
                        "description": "The recovery command to apply."},
            "target_project_path": {
                "type": "string",
                "description": "Optional absolute project path: bank the command for a "
                               "parked (non-active) queue entry instead of the live one.",
            },
        },
        required=("command",), destructive=True,
    ),
    _tool(
        "stop_pipeline",
        "Request a clean pipeline halt (POST /api/stop): writes the stop sentinel while "
        "agents are running (consumed at the loop top, so spend actually stops), or an "
        "escalation STOP when waiting for human input. 409 when not in a stoppable state.",
        "POST", "/api/stop", destructive=True,
    ),
    _tool(
        "resume_ready",
        "Recover from STOPPED or HALTED_SILENT (POST /api/resume-ready): transitions to "
        "WAITING_FOR_HUMAN so answer_escalation can be used (RETRY resumes the agent that "
        "was in flight at stop time). 409 when not in a resumable state.",
        "POST", "/api/resume-ready",
    ),
    _tool(
        "resume_orchestrator",
        "Spawn the orchestrator process for the current pipeline state (POST "
        "/api/resume-orchestrator), reconciling the project symlink first if needed. "
        "409 if an orchestrator is already running.",
        "POST", "/api/resume-orchestrator",
    ),
    _tool(
        "git_recover",
        "Heavy fallback recovery (POST /api/pipeline/git-recover): stashes (including "
        "untracked) and checks out the base branch in the active project. Refused (409) "
        "while an orchestrator is live. Prefer resume_ready + answer_escalation; this "
        "path abandons in-flight phase work.",
        "POST", "/api/pipeline/git-recover",
        properties={
            "base_branch": {"type": "string",
                            "description": "Optional branch override; defaults to config/auto-detect."},
        },
        destructive=True,
    ),
    _tool(
        "launch_project",
        "Initialize and launch a pipeline run (POST /api/setup/launch): prepares the "
        "project directory, writes the roadmap seed if given, repoints the symlink, "
        "writes fresh pipeline state, and spawns the orchestrator. 409 if an "
        "orchestrator is already running.",
        "POST", "/api/setup/launch",
        properties={
            "repo_path": {"type": "string", "description": "Absolute path to the project repo."},
            "roadmap_seed": {"type": "string",
                             "description": "Roadmap markdown to seed (optional when the repo already has one)."},
            "prd_content": {"type": "string", "description": "Optional prd.md content."},
            "verification_content": {"type": "string", "description": "Optional verification.md content."},
            "completion_review": {"type": "boolean",
                                  "description": "Run the completion reviewer at PIPELINE_COMPLETE."},
        },
        required=("repo_path",), destructive=True,
    ),
    _tool(
        "switch_project",
        "Switch the active project (POST /api/setup/switch-project); pipeline must be "
        "stopped. A parked-escalation target routes through revival automatically. Set "
        "start_orchestrator to also spawn the orchestrator.",
        "POST", "/api/setup/switch-project",
        properties={
            "repo_path": {"type": "string", "description": "Absolute path to the project repo."},
            "start_orchestrator": {"type": "boolean",
                                   "description": "Spawn the orchestrator after switching (default false)."},
        },
        required=("repo_path",), destructive=True,
    ),
    _tool(
        "queue_add",
        "Add a project to the queue (POST /api/queue/add) after preflight; in auto mode "
        "an idle pipeline may auto-start it (see the auto_start field of the response).",
        "POST", "/api/queue/add",
        properties={
            "project_path": {"type": "string", "description": "Absolute path to the project repo."},
            "parent_id": {"type": "string",
                          "description": "Optional parent queue entry id (dependency hold until it completes)."},
            "completion_review": {"type": "boolean",
                                  "description": "Run the completion reviewer for this entry."},
        },
        required=("project_path",),
    ),
    _tool(
        "queue_trigger_next",
        "Manually start the next eligible queue entry (POST /api/queue/trigger-next). "
        "409 while a project is ACTIVE; returns queue_halted_reason when nothing is startable.",
        "POST", "/api/queue/trigger-next",
    ),
    _tool(
        "queue_set_mode",
        "Set queue_mode (PATCH /api/queue/mode). Switching manual→auto kicks the "
        "start-next logic once if the pipeline is idle.",
        "PATCH", "/api/queue/mode",
        properties={
            "queue_mode": {"type": "string", "enum": ["auto", "manual"],
                           "description": "Target queue mode."},
        },
        required=("queue_mode",),
    ),
    _tool(
        "queue_relaunch",
        "Restart the orchestrator for an existing queue entry (POST "
        "/api/queue/{entry_id}/relaunch) without resetting pipeline state — the 'Resume "
        "banked answer' path: a parked escalation resumes its escalated phase and "
        "applies any banked command. 409 if an orchestrator is already alive.",
        "POST", "/api/queue/{entry_id}/relaunch",
        properties=dict(_ENTRY_ID_PROP), required=("entry_id",),
        path_params=("entry_id",),
    ),
    _tool(
        "queue_revalidate",
        "Re-run preflight for a queue entry (POST /api/queue/{entry_id}/revalidate); "
        "flips SKIPPED_PENDING→READY when all checks pass (and back on failure).",
        "POST", "/api/queue/{entry_id}/revalidate",
        properties=dict(_ENTRY_ID_PROP), required=("entry_id",),
        path_params=("entry_id",),
    ),
    _tool(
        "queue_delete",
        "Remove a queue entry (DELETE /api/queue/{entry_id}). A live ACTIVE row is "
        "refused; synthetic ingest-* rows cannot be deleted.",
        "DELETE", "/api/queue/{entry_id}",
        properties=dict(_ENTRY_ID_PROP), required=("entry_id",),
        path_params=("entry_id",), destructive=True,
    ),
]

TOOLS_BY_NAME = {spec["name"]: spec for spec in TOOL_SPECS}


def tool_listing() -> list:
    """The MCP ``tools/list`` payload derived from TOOL_SPECS."""
    tools = []
    for spec in TOOL_SPECS:
        tools.append({
            "name": spec["name"],
            "description": spec["description"],
            "inputSchema": {
                "type": "object",
                "properties": spec["properties"],
                "required": spec["required"],
                "additionalProperties": False,
            },
            "annotations": {
                "readOnlyHint": spec["read_only"],
                "destructiveHint": spec["destructive"],
            },
        })
    return tools


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

def _validate_arguments(spec: dict, arguments: dict) -> None:
    for key in spec["required"]:
        value = arguments.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ToolError("Missing required argument: %r" % key)
    for key, value in arguments.items():
        schema = spec["properties"].get(key)
        if schema is None:
            raise ToolError("Unknown argument %r for tool %r" % (key, spec["name"]))
        enum = schema.get("enum")
        if enum and value not in enum:
            raise ToolError("%r must be one of %s (got %r)" % (key, enum, value))


def build_http_call(spec: dict, arguments: dict):
    """Turn (tool spec, arguments) into ``(method, path, query, body)``."""
    _validate_arguments(spec, arguments)

    if spec["name"] == "dashboard_get":
        path = str(arguments.get("path", "")).strip()
        if not path.startswith("/api/") or ".." in path or "?" in path or "#" in path:
            raise ToolError(
                "dashboard_get path must be a bare endpoint path starting with /api/ "
                "(no query string, no '..'); pass query parameters via the 'query' object.")
        query = arguments.get("query") or {}
        if not isinstance(query, dict):
            raise ToolError("dashboard_get 'query' must be an object of key→value pairs.")
        return "GET", path, query, None

    path = spec["path"]
    for name in spec["path_params"]:
        value = str(arguments[name]).strip()
        if "/" in value or value in (".", ".."):
            raise ToolError("Invalid %s: %r" % (name, value))
        path = path.replace("{%s}" % name, urllib.parse.quote(value, safe=""))

    query = {name: arguments[name] for name in spec["query_params"] if name in arguments}

    body_keys = [
        key for key in spec["properties"]
        if key not in spec["path_params"] and key not in spec["query_params"]
    ]
    body = None
    if spec["method"] != "GET" and body_keys:
        # Endpoints declared with a JSON body always get one (possibly empty):
        # FastAPI `request: dict` handlers 422 on a missing body.
        body = {key: arguments[key] for key in body_keys
                if key in arguments and arguments[key] is not None}
    return spec["method"], path, query, body


def _text_result(text: str, *, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def call_tool(name: str, arguments: dict | None, config: dict | None = None) -> dict:
    """Execute one ``tools/call``; always returns an MCP tool result dict."""
    arguments = arguments or {}
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        return _text_result("Unknown tool: %r" % name, is_error=True)

    try:
        method, path, query, body = build_http_call(spec, arguments)
    except ToolError as err:
        return _text_result(str(err), is_error=True)

    resolved = config or resolve_dashboard_config()
    try:
        status, payload = http_request(method, path, query=query, body=body,
                                       config=resolved)
    except (urllib.error.URLError, OSError, TimeoutError) as err:
        return _text_result(
            "Dashboard unreachable at %s (%s). Is the Lullabeast UI server running "
            "(uvicorn ui.server / the autodev-ui service / the Docker container)?"
            % (resolved["base_url"], err),
            is_error=True,
        )

    rendered = json.dumps({"status": status, "body": payload}, indent=2,
                          ensure_ascii=False, default=str)
    return _text_result(rendered, is_error=status >= 400)


# ---------------------------------------------------------------------------
# JSON-RPC / MCP stdio loop
# ---------------------------------------------------------------------------

def _rpc_result(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_message(message: dict, config: dict | None = None):
    """Handle one decoded JSON-RPC message; return a response dict or None."""
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if isinstance(requested, str) and requested else PROTOCOL_VERSION
        return _rpc_result(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "ping":
        return _rpc_result(msg_id, {})
    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": tool_listing()})
    if method == "tools/call":
        result = call_tool(params.get("name"), params.get("arguments"), config)
        return _rpc_result(msg_id, result)
    if isinstance(method, str) and method.startswith("notifications/"):
        return None
    if msg_id is None:
        return None  # unknown notification — ignore per JSON-RPC
    return _rpc_error(msg_id, -32601, "Method not found: %r" % method)


def _write_message(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    # Entry-point-only .env self-load (same contract as the crons/orchestrator:
    # never at import time; an already-set env var always wins).
    load_repo_env_file()
    config = resolve_dashboard_config()
    print("[mcp] %s v%s ready (dashboard %s, token %s)"
          % (SERVER_NAME, SERVER_VERSION, config["base_url"],
             "set" if config["token"] else "not set"),
          file=sys.stderr, flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _write_message(_rpc_error(None, -32700, "Parse error: invalid JSON"))
            continue
        if not isinstance(message, dict):
            _write_message(_rpc_error(None, -32600, "Invalid request: expected an object"))
            continue
        response = handle_message(message)
        if response is not None:
            _write_message(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
