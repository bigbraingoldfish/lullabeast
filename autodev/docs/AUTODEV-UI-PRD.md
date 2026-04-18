# AutoDev Pipeline Dashboard — Product Requirements Document

> **Status:** PRD — for conversion to implementation roadmap by Claude Code with full project visibility  
> **Audience:** Claude Code agent with access to the AutoDev project filesystem  
> **Purpose:** Build a real-time pipeline monitoring dashboard that replaces four terminal windows with a single ops-quality UI

---

## Context and Constraints

### What Claude Code Must Do Before Roadmapping

Before converting this PRD to a phase roadmap, Claude Code must:

1. Read `orchestrator.py` and confirm the exact paths for `pipeline_state.json`, `phase_state.json`, and `pipeline.lock`
2. Confirm whether `pipeline_events.jsonl` exists at `events_path` (from config). If it does not exist, the synthetic event feed is the primary source — no orchestrator addition required for v1. The event log remains the documented upgrade path (see §Activity Feed: Synthetic Events and Event Log Upgrade Path).
3. Confirm the Tailscale IP and port conventions already in use (OpenClaw uses `:18789` — pick a non-conflicting port, suggest `:18790`)
4. Check whether a `ui/` directory already exists in the project root
5. Note the Python version on the Pi and whether FastAPI/uvicorn are already installed

These findings should appear at the top of the roadmap as a "Pre-Flight Findings" section before any phases are defined.

---

## Product Overview

A single-page dashboard that gives the operator full situational awareness of the AutoDev pipeline from any browser on the Tailscale network. It reads state files written by the orchestrator and streams events from the activity feed. During normal operation the UI is read-only. When the pipeline is waiting for human input (`WAITING_FOR_HUMAN`), the escalation command panel allows the operator to issue resume commands directly.

**Access model:** UI server runs on the Raspberry Pi. Accessible at `http://<tailscale-ip>:<port>` from any device on the Tailscale network — specifically the Windows machine where the operator currently monitors OpenClaw. No authentication required (Tailscale is the access control layer).

**Stack:** FastAPI (Python, same language as the orchestrator) serving a single-page frontend. Frontend: React via CDN (no build step, no node_modules, no bundler — this runs on a Pi). Styling: Tailwind via CDN. No database. No external services.

**Why no build step:** The Pi is not a build machine. A no-build React setup (importmap or UMD React via CDN) keeps the UI deployable with `pip install fastapi uvicorn` and nothing else.

---

## Activity Feed: Synthetic Events and Event Log Upgrade Path

The v1 activity feed does not require `pipeline_events.jsonl`. The server maintains an in-memory ring buffer (last 50 entries) of synthetic events derived from successive polls of `pipeline_state.json` and `phase_state.json`. A synthetic event is generated whenever any of the following changes are detected:

- `pipeline_status` changes
- `current_agent` changes
- `current_phase_raw_id` changes
- Any retry counter (`planner_retries`, `executor_retries`, `reviewer_retries`) increments

Each synthetic event records: `ts` (ISO timestamp of detection), `event` (derived change type), `agent` (current_agent at time of change), `phase` (current_phase_raw_id), `attempt` (relevant retry counter value if applicable), and `detail` (human-readable summary of what changed). The SSE stream pushes new synthetic events as they are detected. The ring buffer is in-memory only — it does not survive server restarts (not required in v1).

**`pipeline_events.jsonl` is the documented upgrade path.** When the file exists at `events_path` (from config), the server uses it as the event source instead of the synthetic feed, and the feed gains full historical events from before the server started. The file does not currently exist. Claude Code must confirm this during pre-flight. If it does not exist, the synthetic feed is the sole source — no fallback message is shown, the feed is functional from startup.

**Proposed event log format for the upgrade path (one JSON object per line):**
```json
{"ts": "2026-03-14T10:23:01Z", "event": "gate_pass", "agent": "executor", "phase": "CORE-2", "attempt": 1, "detail": null}
{"ts": "2026-03-14T10:31:44Z", "event": "gate_fail", "agent": "reviewer", "phase": "CORE-2", "attempt": 1, "detail": "blocking_issues: 2"}
{"ts": "2026-03-14T10:45:12Z", "event": "escalation_trigger", "agent": "escalation", "phase": "CORE-2", "attempt": null, "detail": "reviewer retries exhausted"}
{"ts": "2026-03-14T10:52:33Z", "event": "escalation_resolve", "agent": "escalation", "phase": "CORE-2", "attempt": null, "detail": "RESET_EXECUTION"}
{"ts": "2026-03-14T11:14:09Z", "event": "phase_complete", "agent": null, "phase": "CORE-2", "attempt": null, "detail": "merged: phase-2-complete"}
```

Valid event types: `gate_pass`, `gate_fail`, `retry`, `escalation_trigger`, `escalation_resolve`, `phase_complete`, `phase_skip`, `skill_inject`, `pipeline_start`, `pipeline_complete`, `orchestrator_crash`, `heartbeat_resume`

---

## Backend — FastAPI Server

### File: `ui/server.py`

**Startup:** Reads a config file (`ui/config.json`) for paths and port. Falls back to sensible defaults if config is absent.

**Config schema:**
```json
{
  "port": 18790,
  "pipeline_state_path": "~/.openclaw/pipeline_state.json",
  "phase_state_path": "~/.openclaw/pipeline-project/phase_state.json",
  "lock_path": "~/.openclaw/pipeline.lock",
  "events_path": "~/.openclaw/pipeline-project/pipeline_events.jsonl",
  "roadmap_path": "~/.openclaw/pipeline-project/roadmap.md",
  "project_dir_path": "~/.openclaw/pipeline-project"
}
```

Path notes (verified against `orchestrator.py`): `pipeline_state_path` is at the workspace root, not inside `pipeline-project`. `lock_path` is likewise at the workspace root. `project_dir_path` is the `SYMLINK_TARGET` — the directory the `POST /api/command` endpoint writes escalation files to, and where `phase_state.json` and `roadmap.md` live.

**Endpoints:**

`GET /api/state`
Returns merged current state from `pipeline_state.json` and `phase_state.json`. Also returns orchestrator liveness (derived from `pipeline.lock` — attempt non-blocking flock; if lock is held, process is alive). Response shape:

```json
{
  "pipeline_status": "WAITING_FOR_SENTINEL",
  "current_phase": 4,
  "current_phase_raw_id": "CORE-4",
  "current_agent": "executor",
  "planner_retries": 0,
  "executor_retries": 1,
  "reviewer_retries": 0,
  "last_action": "executor webhook posted",
  "last_action_timestamp": "2026-03-14T10:23:01Z",
  "project_path": "/path/to/your-project/myproject",
  "orchestrator_alive": true,
  "last_error_code": "ERR_MANIFEST_FILE_MISSING",
  "escalation_resets": 1,
  "event_source": "synthetic"
}
```

Fields sourced from `pipeline_state.json`: `pipeline_status`, `current_phase`, `current_phase_raw_id`, `current_agent`, `planner_retries`, `executor_retries`, `reviewer_retries`, `last_action`, `last_action_timestamp`, `project_path`. Fields sourced from `phase_state.json` (omitted when file does not exist): `last_error_code`, `escalation_resets`. `orchestrator_alive` and `event_source` are server-derived. `event_source` is `"file"` when `pipeline_events.jsonl` exists at `events_path` and is being used as the feed source; `"synthetic"` when the in-memory ring buffer is the source. The activity feed functions identically in both modes — `event_source` is informational for the frontend if it needs to distinguish. When `phase_state.json` is absent (pipeline idle or between phases), `last_error_code` and `escalation_resets` are omitted from the response.

`GET /api/roadmap`
Parses `roadmap.md` and returns phases as structured array:
```json
[
  {"id": "INFRA-1", "goal": "Initialize project scaffold", "status": "complete"},
  {"id": "CORE-1", "goal": "Implement core data model", "status": "complete"},
  {"id": "CORE-2", "goal": "Build query engine", "status": "in_progress"},
  {"id": "CORE-3", "goal": "Add caching layer", "status": "pending"}
]
```
Status derived from roadmap checkbox syntax: `[x]` = complete, `[-]` = skipped, `[!]` = blocked, `[ ]` = pending. Current phase (from `pipeline_state.json`) = in_progress.

`GET /api/events?limit=30&offset=0`
Returns last N events in reverse chronological order. Source is the server's in-memory synthetic event ring buffer unless `pipeline_events.jsonl` exists at `events_path`, in which case the file is used instead. Response includes a `source` field: `"synthetic"` or `"file"`. The feed is always available — no `{"available": false}` case in v1.

`GET /api/events/stream`
Server-Sent Events endpoint. Pushes new synthetic events from the ring buffer as they are detected by the state polling loop. If `pipeline_events.jsonl` exists at `events_path`, tails it instead. Also pushes a heartbeat event every 15 seconds so the frontend can detect connection loss.

`GET /`
Serves `ui/index.html` (the single-page frontend).

`GET /health`
Returns `{"ok": true}` — for confirming the server is up.

### Polling Fallback

If SSE is not viable in the target browser context, the frontend should fall back to polling `/api/state` every 3 seconds and `/api/events` every 5 seconds. Implement SSE first; polling is the fallback.

---

## Frontend — Single Page Dashboard

### File: `ui/index.html`

Single HTML file. React and Tailwind loaded via CDN. No build step. All JS inline or in a companion `ui/app.js` loaded as a module.

---

### Layout

```
┌─────────────────────────────────────────────────────┐
│  AUTODEV                     [status pill] [project] │  ← Header bar
├──────────────────────┬──────────────────────────────┤
│                      │                               │
│   CURRENT PHASE      │   ROADMAP                     │
│   (left panel)       │   (right panel, scrollable)   │
│                      │                               │
├──────────────────────┴──────────────────────────────┤
│   ACTIVITY FEED                                      │  ← Bottom panel
│   (full width, last 30 events, reverse chron)        │
└─────────────────────────────────────────────────────┘
```

Responsive: on narrow screens, stack vertically (phase → roadmap → feed).

---

### Header Bar

**Left:** "AUTODEV" wordmark in a distinctive display font. Not Inter. Not system font. Pick something with character — something monospace-adjacent or industrial. Suggest: `JetBrains Mono`, `Space Mono`, or `Courier Prime` for the wordmark specifically.

**Center:** Pipeline status pill. Large, color-coded, immediately readable from across the room.

| Status | Color | Label |
|---|---|---|
| RUNNING | Amber pulse | RUNNING |
| WAITING_FOR_SENTINEL | Amber pulse | WAITING — {agent} |
| WAITING_FOR_HUMAN | Orange solid | WAITING FOR HUMAN |
| HALTED_SILENT | Red solid | HALTED SILENT |
| BLOCKED | Red solid | BLOCKED |
| Orchestrator dead + status RUNNING | Red solid | ORCHESTRATOR DOWN |

"Amber pulse" = animated pulse to indicate active processing. Static colors for terminal states.

**Right:** Project folder path in monospace, truncated to last two path segments. Orchestrator liveness indicator — small dot, green if alive, red if dead (derived from lock file check).

---

### Current Phase Panel (Left)

**Phase ID and goal** — Large. The phase raw ID (`CORE-4`) in monospace as a label, the goal summary as a readable sentence below it.

**Active agent** — Which agent is currently working. Displayed as a badge: PLANNER / EXECUTOR / REVIEWER / ESCALATION. Each with a distinct but muted color (not traffic-light — more like terminal syntax highlighting hues).

**Attempt counters** — Three small counters in a row:
```
Planner  ●●○  2/3
Executor ●○○  1/3  
Reviewer ●○○  1/3
```
Filled dots = attempts consumed. Shows at a glance how much retry budget remains.

**Last error code** — Only shown if the last gate failed. Direct read of `last_error_code` from `phase_state.json`. Displayed as-is in monospace (e.g., `ERR_MANIFEST_FILE_MISSING`, `ERR_TESTS_FAILING`). If `phase_state.json` is absent or the field is missing, show nothing.

**Elapsed in current state** — How long the pipeline has been in its current state. Updates live. Turns amber if WAITING_FOR_SENTINEL exceeds 5 minutes (useful for spotting a hung agent before the heartbeat cron catches it).

**Skill injected** — If a skill was injected for the current phase/agent, show the discipline name in small text. "infra-config / executor" for example. If no skill, show nothing. *Requires PATCH-1 (see Pre-Implementation Orchestrator Patches): the orchestrator must write `skill_injected` and `skill_agent` to `phase_state.json` at each injection call site. Without this patch, this field has no data source and must show nothing.*

---

### Roadmap Panel (Right)

Scrollable list of all phases. Each phase is one row:

```
✓  INFRA-1   Initialize project scaffold          [complete]
✓  INFRA-2   Configure CI and linting             [complete]  
▶  CORE-1    Implement core data model             [in progress]
○  CORE-2    Build query engine                   [pending]
○  CORE-3    Add caching layer                    [pending]
⊘  AUTH-1    Add authentication                   [skipped]
⚠  DATA-1    Migrate legacy schema                [blocked]
```

Current phase row is highlighted with a left border accent in the primary brand color. Complete phases are muted. Blocked phases are red-tinted.

Phase count summary at the top: `4 / 18 complete` as a small progress bar.

Clicking a phase row expands it inline to show: full goal text, exit criteria (if available from the roadmap), and for complete phases the git tag name.

---

### Activity Feed (Bottom, Full Width)

Last 30 events, reverse chronological. Each row:

```
10:31:44   gate_fail     reviewer    CORE-2   attempt 1   blocking_issues: 2
10:23:01   gate_pass     executor    CORE-2   attempt 1   —
10:14:55   skill_inject  executor    CORE-2   —           infra-config/executor
10:14:50   retry         planner     CORE-2   attempt 2   plan rejected: attribution=impl
```

Columns: timestamp (time only, monospace), event type (color-coded badge), agent, phase, attempt, detail (truncated).

Event type color coding (muted, not saturated):
- `gate_pass` — green tint
- `gate_fail`, `retry` — amber tint
- `escalation_trigger` — orange tint
- `escalation_resolve` — blue tint
- `phase_complete` — green, slightly brighter
- `orchestrator_crash`, `pipeline_start`, `heartbeat_resume` — gray/neutral

Click any row to expand full detail in a slide-out drawer or inline expansion.

**Live updates:** New events appear at the top with a brief fade-in. No page refresh required.

---

### Escalation Log

Accessible via a tab or collapsible section within the activity feed panel. Shows only escalation events paired with their resolutions:

```
CORE-2   Triggered: 10:45:12   reviewer retries exhausted
         Command received:      RESET_EXECUTION
         Resolved: 10:52:33    (7m 21s)

CORE-1   Triggered: 09:14:08   executor retries exhausted
         Command received:      RETRY
         Resolved: 09:14:55    (47s)
```

*Implementation note: the "Triggered" timestamp is the moment the server detected `pipeline_status` → `WAITING_FOR_HUMAN`. This is a proxy for Signal notification dispatch time, accurate to within one polling interval (a few seconds). "Signal sent" is not tracked separately in any state file — the single "Triggered" row covers both.*

Derived from the activity feed event source — synthetic ring buffer in v1, `pipeline_events.jsonl` when available. Trigger events are detected when `pipeline_status` transitions to `WAITING_FOR_HUMAN`; resolution events when it transitions away. The trigger reason is read from `escalation_trigger_reason` in `phase_state.json` (PATCH-2) with fallback to `last_action`. This section is always visible when escalation events are present in the ring buffer; it shows a "No escalations recorded in this session" placeholder when the buffer contains no escalation pairs.

---

## Aesthetic Direction

**Theme:** Dark. Background near-black (`#0d0f12` range), not pure black. Panels slightly lighter (`#141618` range). Borders subtle, not harsh.

**Typography:** 
- Wordmark / phase IDs / status values: monospace — JetBrains Mono or Space Mono via Google Fonts
- Body text / labels: a clean sans-serif that isn't Inter — suggest `IBM Plex Sans` or `DM Sans`
- Log output / paths / JSON values: monospace always

**Accent color:** A specific cyan-teal — `#00b4d8` range. Used sparingly: current phase highlight, active agent badge, progress bar fill. Not splashed everywhere.

**Status colors:** Derived from the accent palette, not pure RGB primaries. Amber for active/waiting states, orange for human-required states, red for error/halted states, muted green for complete.

**Density:** Medium-high. This is an ops tool. Comfortable information density — not cramped, not wasteful. Think: a well-designed terminal multiplexer, not a consumer dashboard.

**No:** Gradients on backgrounds. Purple anything. Rounded-everything cards. Drop shadows on every element. Emoji in the UI. Animations except for the status pulse and event feed fade-in.

---

## Escalation Command Panel

When `pipeline_status` is `WAITING_FOR_HUMAN`, the UI surfaces a command panel that allows the operator to issue resume commands directly — identical in effect to the escalation agent writing `escalation_output.json` and the orchestrator polling for it. This is not a different control path; it is the same path with a better input method.

**Visibility rule: strictly conditional.** The command panel is only rendered when `pipeline_status === "WAITING_FOR_HUMAN"`. It must not appear during `RUNNING`, `WAITING_FOR_SENTINEL`, `HALTED_SILENT`, or `BLOCKED` states. During normal operation the UI is fully read-only.

### Layout

The command panel appears as a prominent section within the Current Phase panel, visually distinct from the status display — a contained block with a border in the orange/warning palette to signal "human action required."

Header: **"Escalation — Human Input Required"** with the trigger reason read from `escalation_trigger_reason` in `phase_state.json` (written by the orchestrator when transitioning to `WAITING_FOR_HUMAN` — see PATCH-2 in Pre-Implementation Orchestrator Patches). Falls back to `last_action` from `pipeline_state.json` if the field is absent.

Six command buttons, each labeled and described:

| Button Label | Command | Description shown in UI |
|---|---|---|
| RETRY | `RETRY` | Re-invoke the failed agent. No state change. Use for transient failures. |
| RESET EXECUTION | `RESET_EXECUTION` | Clear executor/reviewer output, preserve planner output, re-invoke executor. |
| RESET PHASE | `RESET_PHASE` | Full phase reset. Rewinds git, clears all output, re-invokes planner. |
| SKIP | `SKIP` | Mark phase as skipped, advance to next. Use only if outcome is acceptable. |
| PROCEED | `PROCEED` | Human has resolved the issue externally. Run post-merge cleanup and advance. |
| STOP | `STOP` | Halt pipeline. Full manual intervention required. |

**Destructive command confirmation:** RESET_PHASE, SKIP, and STOP require a confirmation step before executing — a modal or inline confirmation prompt: "Are you sure? This cannot be undone." RETRY, RESET_EXECUTION, and PROCEED execute immediately on click (they are recoverable or safe).

**Reset cap awareness:** If `escalation_resets >= 3` (readable from `phase_state.json`), RESET_PHASE, RESET_EXECUTION, and RESET_REVIEWER buttons are disabled with a native `title` tooltip (visible labels **Proceed** / **Stop**, plus a short anti-loop / manual-repo hint) and an inline amber notice. This mirrors the orchestrator's own cap enforcement.

**Post-command behavior:** After a command is issued, the command panel shows a brief "Command sent — waiting for orchestrator..." state and transitions back to read-only as soon as `pipeline_status` changes away from `WAITING_FOR_HUMAN` (detected via the next state poll or SSE event).

### Backend — `POST /api/command`

Accepts a command and writes it through the same mechanism the escalation agent uses:

```json
{ "command": "RESET_EXECUTION" }
```

**Implementation:** The endpoint writes `escalation_output.json` to `project_dir_path` (from config, default `~/.openclaw/pipeline-project`) and then writes `escalation_output.done` as the sentinel — exactly replicating what the escalation agent does. The orchestrator's existing sentinel polling loop picks it up without any modification. If `project_dir_path` does not exist or its symlink is dangling, return 503 with `{"error": "Pipeline project directory not available"}`.

**Validation:**
- Reject if `pipeline_status` is not `WAITING_FOR_HUMAN` — return 409 with `{"error": "Pipeline is not waiting for human input"}`
- Reject unknown command strings — return 400
- Reject if `escalation_resets >= 3` and command is `RESET_PHASE`, `RESET_EXECUTION`, or `RESET_REVIEWER` — return 409 with `{"error": "Reset cap reached"}` (detail text names PROCEED / STOP for operators; UI copy uses button labels **Proceed** / **Stop**)

**File format written (matches escalation agent output exactly):**
```json
{
  "command": "RESET_EXECUTION",
  "source": "ui",
  "timestamp": "2026-03-14T11:22:01Z"
}
```

The `"source": "ui"` field distinguishes UI-issued commands from agent-issued commands in the event log. The orchestrator does not need to handle this field — it reads only `command`. It is present for audit trail purposes.

Verified against `orchestrator.py`: the sentinel filename is `escalation_output.done`, located in `project_dir_path`. The orchestrator reads only the `command` field from `escalation_output.json`. The `source` and `timestamp` fields are ignored by the orchestrator and present for audit trail purposes only.

---

## Non-Requirements (v1)

These are explicitly out of scope for this build. Do not implement:

- Signal message viewer
- Agent session log viewer (OpenClaw's domain)
- Multi-project switcher
- User authentication
- Mobile-optimized layout (Tailscale + browser on the same network is the access model)
- Persistent storage or database
- Notifications / alerts (Signal handles this; the command panel covers in-browser human response when escalation is active)

---

## Deployment

The UI server should be startable with a single command:

```bash
python ui/server.py
```

And optionally registered as a systemd service on the Pi (provide a `.service` file template in `ui/autodev-ui.service`).

The server must handle the orchestrator not running — all endpoints return gracefully when state files don't exist (empty/default state, not 500 errors).

---

## Phasing Suggestion for Roadmap

### Pre-Implementation Orchestrator Patches

The following changes to `orchestrator.py` must be completed before **UI-PHASE** begins. These are orchestrator tasks, not UI phases — commit them separately before the UI roadmap starts.

**PATCH-1 (required): `skill_injected` write**
At each `skill_manager.inject_skill()` call site in `orchestrator.py`, write `{"skill_injected": "<discipline>", "skill_agent": "<role>"}` to `phase_state.json`. This is the data source for the "Skill injected" display in the Current Phase Panel. Without this patch, that field has no data source and shows nothing.

**PATCH-2 (recommended): `escalation_trigger_reason` write**
When the orchestrator transitions to `WAITING_FOR_HUMAN`, write the trigger reason string (e.g., `"executor retries exhausted"`, `"reviewer retries exhausted"`, `"reset cap reached"`) to `phase_state.json` under the key `escalation_trigger_reason`. The command panel header falls back to `last_action` from `pipeline_state.json` if this field is absent — so PATCH-2 is an improvement, not a blocker.

---

### Implementation Phases

Claude Code should validate and adjust this, but a reasonable phase breakdown:

1. **INFRA** — FastAPI server scaffold, config loading, `/health` endpoint, static file serving
2. **API-STATE** — `/api/state` endpoint with liveness detection, path resolution, graceful missing-file handling, phase_state.json merge
3. **API-ROADMAP** — Roadmap parser, `/api/roadmap` endpoint
4. **API-EVENTS** — `/api/events` endpoint, synthetic event ring buffer, state polling loop, SSE stream, pipeline_events.jsonl upgrade path
5. **API-COMMAND** — `POST /api/command` endpoint — write `escalation_output.json` + sentinel to `project_dir_path`, validation (state check, cap check, command allowlist, symlink existence)
6. **UI-SHELL** — HTML skeleton, CDN imports, layout structure, header bar with status pill
7. **UI-PHASE** — Current phase panel, agent badge, attempt counters, last error code, elapsed timer, skill injected display (requires PATCH-1)
8. **UI-ROADMAP** — Roadmap panel, phase rows, expand/collapse, progress bar
9. **UI-FEED** — Activity feed, synthetic event display, event type badges, live SSE updates, pipeline_events.jsonl upgrade path handling
10. **UI-ESCALATION** — Escalation log tab/section
11. **UI-COMMAND** — Escalation command panel (conditional on `WAITING_FOR_HUMAN`), confirmation modals, reset cap enforcement, post-command state transition
12. **POLISH** — Typography, color system, responsive stacking, pulse animation, systemd service file

Each phase should be independently testable. The backend phases (1-5) should be completable and verifiable without any frontend work done.
