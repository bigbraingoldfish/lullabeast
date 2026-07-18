# Lullabeast Dashboard MCP Server

`autodev/mcp/dashboard_server.py` is a **stdlib-only MCP server (stdio
transport)** that exposes the Lullabeast dashboard's HTTP API (`ui/server.py`)
as agent-callable tools — full agentic **control** of and **visibility** into
the pipeline from Claude Code, an OpenClaw agent, or any other MCP client.

It is a thin proxy: every tool maps 1:1 onto a dashboard endpoint. All
validation, orchestrator-liveness 409 guards, queue CAS concurrency, and token
auth stay server-side in `ui/server.py` — the MCP server never touches pipeline
state files directly, so it is exactly as safe (and as powerful) as the
dashboard itself.

## Running it

```bash
# from the repo root (module form — preferred)
python3 -m autodev.mcp.dashboard_server

# or by absolute path from anywhere (a sys.path bootstrap makes this work)
python3 /path/to/lullabeast/autodev/mcp/dashboard_server.py
```

No dependencies beyond the Python standard library — it runs outside the
repo's virtualenv.

### Claude Code

The repo gitignores `/.mcp.json` (reserved for local wiring), so copy the
committed example:

```bash
cp .mcp.json.example .mcp.json
```

or register it imperatively:

```bash
claude mcp add lullabeast-dashboard -- python3 -m autodev.mcp.dashboard_server
```

### Any other MCP client

Point the client at the command above with the environment described below.
The transport is newline-delimited JSON-RPC 2.0 over stdio (the standard MCP
stdio transport); the server implements `initialize`, `ping`, `tools/list`,
and `tools/call`.

### OpenClaw (opt-in — read the warning)

You *can* register it in `openclaw.json` so OpenClaw agents get dashboard
control:

```json
"mcp": {
  "servers": {
    "lullabeast-dashboard": {
      "command": "python3",
      "args": ["/absolute/path/to/lullabeast/autodev/mcp/dashboard_server.py"],
      "env": {"AUTODEV_UI_TOKEN": "<your token>"}
    }
  }
}
```

**This is deliberately NOT wired into the golden template or the pipeline
agents.** Giving the planner/executor/reviewer the power to stop their own
pipeline, answer their own escalations, or mutate the queue inverts the
control hierarchy the orchestrator exists to enforce (and the escalation agent
is NOTIFY-only by design — see the Security Constraints section of CLAUDE.md).
Register it only for a *supervisory* agent that is not itself driven by the
pipeline. On an owned/container install, note that hand-edits to `openclaw.json`
keys pinned by the template are reverted on boot; extra `mcp.servers` entries
are template-unmanaged and survive reconcile.

## Configuration

Resolution mirrors the dashboard's own config doctrine (env wins, then
`ui/config.json`, then defaults). At startup the server also self-loads
`<repo>/.env` (setdefault semantics — an already-set env var always wins),
so the token `install.sh` generated is picked up automatically.

| Setting | Source order | Default |
|---------|--------------|---------|
| Base URL | `AUTODEV_UI_URL` env → `http://127.0.0.1:<port>` | port from `AUTODEV_UI_PORT` / `UI_PORT` env → `ui/config.json` `port` → `18790` |
| Access token | `AUTODEV_UI_TOKEN` env → `ui/config.json` `ui_token` | empty (dashboard legacy loopback-open mode) |
| HTTP timeout | `AUTODEV_MCP_HTTP_TIMEOUT` env (seconds) | `60` |
| Repo root | `AUTODEV_REPO_PATH` env | module-relative |

The token rides as `Authorization: Bearer` on every call — the same channel
scripts use against the dashboard.

## Tool catalogue

**Visibility (read-only):**

| Tool | Endpoint |
|------|----------|
| `pipeline_state` | `GET /api/state` — the primary situational-awareness call |
| `pipeline_events` | `GET /api/events` (limit/offset) |
| `pipeline_log_tail` | `GET /api/log/tail` (lines) |
| `roadmap` | `GET /api/roadmap` |
| `metrics_summary` | `GET /api/metrics-summary` |
| `doctor_report` | `GET /api/doctor` (26 read-only health checks) |
| `queue_list` / `queue_status` | `GET /api/queue` / `GET /api/queue/status` |
| `queue_snapshot` / `queue_report` | `GET /api/queue/{id}/snapshot` / `.../report` |
| `completion_report` | `GET /api/completion-report` |
| `dashboard_get` | any other `GET /api/*` endpoint (guarded escape hatch) |

**Control:**

| Tool | Endpoint |
|------|----------|
| `answer_escalation` | `POST /api/command` (RETRY / RESET_* / SKIP / PROCEED / STOP / NUCLEAR_RESET, optional `target_project_path` to bank for a parked entry) |
| `stop_pipeline` | `POST /api/stop` |
| `resume_ready` | `POST /api/resume-ready` (STOPPED / HALTED_SILENT recovery) |
| `resume_orchestrator` | `POST /api/resume-orchestrator` |
| `git_recover` | `POST /api/pipeline/git-recover` (heavy fallback) |
| `launch_project` | `POST /api/setup/launch` |
| `switch_project` | `POST /api/setup/switch-project` |
| `queue_add` / `queue_delete` | `POST /api/queue/add` / `DELETE /api/queue/{id}` |
| `queue_trigger_next` | `POST /api/queue/trigger-next` |
| `queue_set_mode` | `PATCH /api/queue/mode` (auto/manual) |
| `queue_relaunch` | `POST /api/queue/{id}/relaunch` (resume banked answer) |
| `queue_revalidate` | `POST /api/queue/{id}/revalidate` |

Every tool result is the endpoint's HTTP status + JSON body rendered as text;
`isError` is set for any 4xx/5xx, so an agent sees a 409 liveness refusal as a
meaningful answer rather than a crash. Transport failures (dashboard down)
return a hint naming the base URL.

## Tests

```bash
pytest tests/test_mcp_dashboard_server.py -q
```

Covers the tool catalogue shape, the JSON-RPC handshake and stdio loop, the
tool→endpoint routing table, path-parameter and `dashboard_get` guards, error
mapping, Bearer-token transmission (against a real in-process HTTP server),
and config resolution order.
