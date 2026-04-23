# AutoDev Pipeline Dashboard — Product Requirements Document

> **Note:** The **canonical full-application PRD** (Pipeline Monitor + Project Ideas + Setup & Preflight) is [autodev-ui-screens.prd.md](autodev-ui-screens.prd.md), which preserves this document’s **structure and tone**. This file remains the original **pipeline-dashboard-only** reference.

> **Status:** PRD — for conversion to implementation roadmap by Claude Code with full project visibility  
> **Audience:** Claude Code agent with access to the AutoDev project filesystem  
> **Purpose:** Build a real-time pipeline monitoring dashboard that replaces four terminal windows with a single ops-quality UI

---

## Context and Constraints

### What Claude Code Must Do Before Roadmapping

Before converting this PRD to a phase roadmap, Claude Code must:

1. Read `orchestrator.py` and confirm the exact paths for `pipeline_state.json`, `phase_state.json`, and `pipeline.lock`
2. Confirm whether `pipeline_events.jsonl` exists — if not, flag it as a required orchestrator addition (see §Asterisk: Event Log) and note it as a dependency for the activity feed feature
3. Confirm the Tailscale IP and port conventions already in use (OpenClaw uses `:18789` — pick a non-conflicting port, suggest `:18790`)
4. Check whether a `ui/` directory already exists in the project root
5. Note the Python version on the Pi and whether FastAPI/uvicorn are already installed

These findings should appear at the top of the roadmap as a "Pre-Flight Findings" section before any phases are defined.

---

## Product Overview

A single-page dashboard that gives the operator full situational awareness of the AutoDev pipeline from any browser on the Tailscale network. It reads state files written by the orchestrator and streams events from an append-only event log. It does not control the pipeline.

**Access model:** UI server runs on the Raspberry Pi. Accessible at `http://<tailscale-ip>:<port>` from any device on the Tailscale network — specifically the Windows machine where the operator currently monitors OpenClaw. No authentication required (Tailscale is the access control layer).

**Stack:** FastAPI (Python, same language as the orchestrator) serving a single-page frontend. Frontend: React via CDN (no build step, no node_modules, no bundler — this runs on a Pi). Styling: Tailwind via CDN. No database. No external services.

**Why no build step:** The Pi is not a build machine. A no-build React setup (importmap or UMD React via CDN) keeps the UI deployable with `pip install fastapi uvicorn` and nothing else.

---

## Asterisk: Event Log Dependency

The activity feed requires an append-only event log (`pipeline_events.jsonl`) that the orchestrator writes one JSON line to on each significant state transition. This file does not currently exist.

Claude Code must determine during pre-flight whether it exists. If it does not:

- Flag it clearly in the roadmap
- Treat it as a separate orchestrator task (not part of the UI phases)
- Design the activity feed to degrade gracefully when the file is absent — show a "Event log not available — see orchestrator setup docs" message rather than erroring
- The remaining UI panels (status, phase, roadmap, escalation log) must function fully without the event log

**Proposed event log format (one JSON object per line):**
```json
{"ts": "2026-03-14T10:23:01Z", "event": "gate_pass", "agent": "executor", "phase": "CORE-2", "attempt": 1, "detail": null}
{"ts": "2026-03-14T10:31:44Z", "event": "gate_fail", "agent": "reviewer", "phase": "CORE-2", "attempt": 1, "detail": "blocking_issues: 2"}
{"ts": "2026-03-14T10:45:12Z", "event": "escalation_trigger", "agent": "escalation", "phase": "CORE-2", "attempt": null, "detail": "reviewer retries exhausted"}
{"ts": "2026-03-14T10:52:33Z", "event": "escalation_resolve", "agent": "escalation", "phase": "CORE-2", "attempt": null, "detail": "RESET_EXECUTION"}
{"ts": "2026-03-14T11:14:09Z", "event": "phase_complete", "agent": null, "phase": "CORE-2", "attempt": null, "detail": "merged: phase-2-complete"}
```

Valid event types: `gate_pass`, `gate_fail`, `retry`, `escalation_trigger`, `escalation_resolve`, `phase_complete`, `phase_skip`, `skill_inject`, `pipeline_start`, `pipeline_complete`, `orchestrator_crash`, `heartbeat_resume`

**UI mapping:** The dashboard maps canonical log types to **operator-facing labels** on the activity badge; the **raw machine id** remains available via the badge’s native **`title`** (and optional `data-event-type`). Log lines may use the jsonl field **`event`** or the UI may receive **`event_type`** from synthetic server events — both are accepted. Canonical display strings live in `EVENT_TYPE_DISPLAY` / `formatActivityEventTypeLabel` in `ui/index.html` (avoid duplicating the full table here).

---

## Backend — FastAPI Server

### File: `ui/server.py`

**Startup:** Reads a config file (`ui/config.json`) for paths and port. Falls back to sensible defaults if config is absent.

**Config schema:**
```json
{
  "port": 18790,
  "pipeline_state_path": "~/.openclaw/pipeline-project/pipeline_state.json",
  "phase_state_path": "~/.openclaw/pipeline-project/phase_state.json",
  "lock_path": "<project-root>/pipeline.lock",
  "events_path": "<project-root>/pipeline_events.jsonl",
  "roadmap_path": "~/.openclaw/pipeline-project/roadmap.md"
}
```

Claude Code must verify these paths against the actual project before hardcoding defaults.

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
  "project_path": "/home/pi/projects/myproject",
  "orchestrator_alive": true,
  "last_failure_reason": "AttributeError in test_runner.py line 44 — truncated to 120 chars",
  "events_available": true
}
```

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
Returns last N lines from `pipeline_events.jsonl` in reverse chronological order. Returns empty array with `{"available": false}` if file does not exist.

`GET /api/events/stream`
Server-Sent Events endpoint. Tails `pipeline_events.jsonl` and pushes new lines as they are appended. Also pushes a heartbeat event every 15 seconds so the frontend can detect connection loss. Falls back gracefully if file does not exist.

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

**Center:** Pipeline status pill (`pipeline_status` from `GET /api/state`). Large, color-coded, immediately readable from across the room. Canonical strings and Tailwind classes live in `ui/index.html` (`PIPELINE_LIVE_PILL`); this table is the operator-facing contract.

| `pipeline_status` | Animation | UI label (header / live pill) | Notes |
|---|---|---|---|
| RUNNING | Teal `run-pulse` (`bg-[#0d9488]`) | RUNNING | Active compute |
| WAITING_FOR_SENTINEL | Static teal `#0d9488` (no pulse; same surface as `RUNNING`, without animation) | `Running` or `Running {Agent}` from `current_agent` (title case, e.g. `Running Executor`); queue rows use `live_current_agent` from `GET /api/queue` for the project whose path matches `pipeline_state` | Agent invoked; polling for sentinel |
| WAITING_FOR_HUMAN | Static orange | NEEDS YOUR INPUT | Escalation / human gate |
| HALTED_SILENT | Static red | INTERVENTION REQUIRED | Native `title` on pill: escalation path failed; check orchestrator logs and project `escalation_failed.json` |
| BLOCKED | Static red | BLOCKED | |
| PIPELINE_COMPLETE | Static green | COMPLETE | |
| STOPPED | Static red | STOPPED | Operator halt |
| QUEUE_HALTED | Static amber | Queue stalled | Distinct from the header **Queue: halted** chip (navigation); that chip text is unchanged |
| IDLE | Static slate | IDLE | |
| UNKNOWN | Static slate | UNKNOWN | |
| Orchestrator dead + (`RUNNING` or `WAITING_FOR_SENTINEL`) | — | ORCHESTRATOR DOWN (red) | Overrides pill while process is down mid-flight |

**Animation:** Only `RUNNING` uses the repeating `run-pulse` animation. `WAITING_FOR_SENTINEL`, `WAITING_FOR_HUMAN`, terminal states, and `QUEUE_HALTED` use static pills (see `tests/test_ui_status_pulse_animation.py`).

**Queue row pills:** When `GET /api/queue` supplies `live_pipeline_status` for a row, the row pill uses the same live labels as above. Otherwise the UI uses queue-only labels (`ACTIVE`, `Preflight failed`, `Waiting on parent`, `QUEUE BLOCKED`, etc.) from `queueOnlyRowPill` in `ui/index.html`.

**Right:** Project folder path in monospace, truncated to last two path segments. Orchestrator liveness indicator — small dot, green if alive, red if dead (derived from lock file check).

---

### Current Phase Panel (Left)

**Phase ID and goal** — Large. The phase raw ID (`CORE-4`) in monospace as a label, the goal summary as a readable sentence below it.

**Active agent** — Which agent is currently working. Displayed as a badge: PLANNER / EXECUTOR / REVIEWER / ESCALATION. Each with a distinct but muted color (not traffic-light — more like terminal syntax highlighting hues).

**Agent attempts** — Section heading plus three rows (`Planner` / `Executor` / `Reviewer`). Each row shows **three boxed attempt cells** and a right-aligned **`n/3`** consumption count (neutral `text-slate-500` only; no semantic color on the fraction). Slots are derived from `planner_retries` / `executor_retries` / `reviewer_retries`, `current_agent`, and `pipeline_status` via `getAgentAttemptDotStates`; **`n`** comes from `computeAgentAttemptFractionN` (same file). **Pending** slot: slate border + attempt index digit; **in-flight** (`RUNNING` / `WAITING_FOR_SENTINEL`): blue `#3b82f6` border/cell treatment with subtle pulse on that cell only + index digit (distinct from pipeline teal pills and lime success); **success** = lime `#2DEB1E` + check icon (same as **COMPLETE** pill); **failure** = red `#dc2626` + X icon. Each cell has a native **`title`** (`AGENT_ATTEMPT_DOT_TITLES`). Hex values use `PIPELINE_STATUS_PILL_HEX` / `AGENT_ATTEMPT_DOT_HEX`. Component: **`AgentAttemptRow`** in `ui/index.html`. (Orchestrator may reset `executor_retries` on `ROUTE_EXECUTOR` — cells follow live counters.)

**Last failure reason** — Only shown if the last gate failed. Muted text, truncated to 120 characters. Monospace. Click to expand full content in a modal or inline expansion.

**Elapsed in current state** — How long the pipeline has been in its current state. Updates live. Turns amber if WAITING_FOR_SENTINEL exceeds 5 minutes (useful for spotting a hung agent before the heartbeat cron catches it).

**Skill injected** — If a skill was injected for the current phase/agent, show the discipline name in small text. "infra-config / executor" for example. If no skill, show nothing.

**Empty phase (no `current_phase_raw_id`)** — When `pipeline_status` is **`IDLE`** or **`UNKNOWN`**, show **No pipeline running.** and one short paragraph steering the operator to **Project Ideas** (PRD) then **Setup & Preflight** (repo + launch) and the **queue** (`data-testid="current-phase-empty-idle"`). Other statuses keep a single neutral **No active phase** line.

**Git checkout recovery** — When `last_action` contains **Git operation failed**, an amber strip offers **Recover Git** with a native **`title`** (stash including untracked, checkout, state refresh; not `git reset`). **`GET /api/state`** exposes **`git_recover_suggested_branch`**; the modal uses **Branch to return to** prefilled from config + repo heuristics (override if the wrong branch was used).

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

**Empty roadmap list** — When `pipeline_status` is **`IDLE`** or **`UNKNOWN`** and there is no roadmap payload, show idle-oriented copy and `data-testid="roadmap-empty-idle"` (steer to Ideas / Setup and `roadmap.md`). Otherwise keep a single generic empty line.

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

Columns: timestamp (time only, monospace), event type (**human-readable** color-coded badge; raw id on **`title`**), agent, phase, attempt, detail (truncated).

Event type color coding (muted, not saturated):
- `gate_pass` — green tint
- `gate_fail`, `retry` — amber tint
- `escalation_trigger` — orange tint
- `escalation_resolve` — blue tint
- `phase_complete` — green, slightly brighter
- `orchestrator_crash`, `pipeline_start`, `heartbeat_resume` — gray/neutral

Click any row to expand full detail in a slide-out drawer or inline expansion.

When event log is unavailable: show a single muted row — "Event log not available. Add pipeline_events.jsonl support to orchestrator to enable this panel."

**Live updates:** New events appear at the top with a brief fade-in. No page refresh required.

---

### Escalation Log

Accessible via a tab or collapsible section within the activity feed panel. Shows only escalation events paired with their resolutions:

```
CORE-2   Triggered: reviewer retries exhausted
         Signal sent: 10:45:12
         Command received: RESET_EXECUTION  
         Resolved: 10:52:33  (7m 21s)

CORE-1   Triggered: executor retries exhausted  
         Signal sent: 09:14:08
         Command received: RETRY
         Resolved: 09:14:55  (47s)
```

Derived from event log. If event log unavailable, this section is hidden.

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

Header: **"Escalation — Human Input Required"** with the trigger reason pulled from the most recent escalation event in the event log (or from `phase_state.json` if the event log is unavailable).

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

**Implementation:** The endpoint writes `escalation_output.json` to the shared pipeline project directory and then writes `escalation_output.done` as the sentinel — exactly replicating what the escalation agent does. The orchestrator's existing sentinel polling loop picks it up without any modification.

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

Claude Code must verify the exact `escalation_output.json` schema and sentinel filename against the actual orchestrator code before implementing this endpoint. The schema above is based on the spec — the implementation must match what the orchestrator actually polls for.

---

## Non-Requirements (v1)

These are explicitly out of scope for this build. Do not implement:

- Signal message viewer
- Agent session log viewer (OpenClaw's domain)
- Multi-project switcher
- User authentication
- Mobile-optimized layout (Tailscale + browser on the same network is the access model)
- Persistent storage or database
- Notifications / alerts (Signal handles this beyond what the command panel provides)

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

Claude Code should validate and adjust this, but a reasonable phase breakdown:

1. **INFRA** — FastAPI server scaffold, config loading, `/health` endpoint, static file serving
2. **API-STATE** — `/api/state` endpoint with liveness detection, path resolution, graceful missing-file handling
3. **API-ROADMAP** — Roadmap parser, `/api/roadmap` endpoint
4. **API-EVENTS** — `/api/events` endpoint, SSE stream, graceful absent-file handling
5. **API-COMMAND** — `POST /api/command` endpoint — write `escalation_output.json` + sentinel, validation (state check, cap check, command allowlist)
6. **UI-SHELL** — HTML skeleton, CDN imports, layout structure, header bar with status pill
7. **UI-PHASE** — Current phase panel, agent badge, attempt counters, failure reason, elapsed timer
8. **UI-ROADMAP** — Roadmap panel, phase rows, expand/collapse, progress bar
9. **UI-FEED** — Activity feed, event type badges (labels + raw on hover), live SSE updates, absent-log fallback
10. **UI-ESCALATION** — Escalation log tab/section
11. **UI-COMMAND** — Escalation command panel (conditional on `WAITING_FOR_HUMAN`), confirmation modals, reset cap enforcement, post-command state transition
12. **POLISH** — Typography, color system, responsive stacking, pulse animation, systemd service file

Each phase should be independently testable. The backend phases (1-4) should be completable and verifiable without any frontend work done.
