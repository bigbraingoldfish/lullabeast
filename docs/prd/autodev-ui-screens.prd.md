# AutoDev UI — Product Requirements Document

> **Status:** PRD — for conversion to implementation roadmap with full project visibility  
> **Audience:** Implementing agent with access to the AutoDev project filesystem  
> **Purpose:** A single ops-quality web UI covering **pipeline monitoring**, **PRD → roadmap collaboration**, and **greenfield setup / preflight / launch** — replacing ad-hoc terminals and loose files with one coherent surface

**Canonical file:** This document is the **product intent** source of truth for the full UI. **[roadmap.md](../../roadmap.md)** is the **execution** source of truth (phases, tests, sequencing, glossary).

---

## Context and Constraints

### What the implementing agent must do before roadmapping

Before converting this PRD to a phase roadmap (or extending an existing one), the agent must:

1. Read `orchestrator.py` (or current docs) and confirm paths for `pipeline_state.json`, `phase_state.json`, and `pipeline.lock` — **note:** resolved paths may live under `~/.openclaw/` per deployment; verify against `ui/server.py` `load_config()` defaults.
2. Confirm whether `pipeline_events.jsonl` exists — if not, flag it per §Asterisk: Event Log Dependency; the activity feed must still degrade gracefully.
3. Confirm port conventions (OpenClaw often `:18789`; UI server typically non-conflicting, e.g. `:18790`).
4. Check whether `ui/` exists and how **three screens** are routed in `ui/index.html` (`pipeline`, `ideas`, `preflight`).
5. Note Python version and whether FastAPI/uvicorn are declared in `requirements.txt`.

These findings should appear at the top of a roadmap as a **Pre-Flight Findings** section when generating a new roadmap artifact.

### Full application scope (beyond the original dashboard-only PRD)

The UI is **not** only the Pipeline Monitor. It also includes:

- **Project Ideas** — prd-creator agent sessions, PRD document pane, upload / clarity / conversion, handoff to setup.
- **Setup & Preflight** — roadmap seed + repo path, validation, preflight checks, launch (init project + symlink).

End-to-end intent: **Ideas → (generate roadmap) → Setup → Launch → Monitor**. The **default** screen on load is **Pipeline Monitor**; the user may switch anytime via the sidebar.

---

## Product Overview

A **browser-based** AutoDev control surface served by the same FastAPI app as the pipeline dashboard. It combines:

1. **Pipeline Monitor** — Situational awareness of orchestrator state, roadmap progress, activity feed, escalation commands, and stop — aligned with the original dashboard PRD.
2. **Project Ideas** — Collaborative PRD drafting with persisted sessions and optional PRD upload, clarity check, and PRD→roadmap conversion.
3. **Setup & Preflight** — Validate roadmap markdown, run environment preflight, launch greenfield init and point `pipeline-project` at the new repo.

**Access model:** UI server runs on the host (e.g. Raspberry Pi). Reachable at `http://<host>:<port>` on the operator’s network (e.g. Tailscale). No authentication in v1 (network is the access layer) unless product scope changes.

**Stack:** FastAPI (Python) serving a **no-build** frontend: React + Tailwind via CDN, single `ui/index.html` (inline or companion script). No `node_modules` / bundler required for deploy.

**Agent integration (Ideas):** OpenClaw webhook + **sentinel polling** for agent turns — not SSE/streaming for PRD chat (see roadmap glossary: sentinel files under `ideas_dir`).

**Why no build step:** Same rationale as the original dashboard — deploy with `pip install` + run; Pi-friendly.

---

## Asterisk: Event Log Dependency

The activity feed is designed to consume an append-only event log (`pipeline_events.jsonl`) when present. The orchestrator may or may not write it yet.

If the file does not exist:

- Flag it clearly in any new roadmap derived from this PRD.
- Treat full orchestrator support as a **separate** track from pure UI work.
- The feed must **degrade gracefully** — synthetic events and/or polling remain valid; never hard-fail the whole dashboard.

**Proposed event log format (one JSON object per line):** (unchanged intent from the original PRD)

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

**Startup:** Reads `ui/config.json` for paths, port, `ideas_dir`, hooks URL/token, conversion prompt path, etc. Falls back to defaults documented in [roadmap.md](../../roadmap.md) and `server.py`.

**Endpoints — Pipeline Monitor (core)**

| Endpoint | Role |
|----------|------|
| `GET /api/state` | Merged `pipeline_state.json` + `phase_state.json` + derived **orchestrator_alive** (lock probe), metrics as implemented |
| `GET /api/roadmap` | Parsed phases from active project `roadmap.md` |
| `GET /api/events` | Event feed with limit/offset; file or synthetic source |
| `GET /api/events/stream` | SSE + heartbeat; fallback behavior when unusable |
| `POST /api/command` | Escalation commands when `WAITING_FOR_HUMAN` — writes `escalation_output.json` then sentinel per orchestrator contract |
| `GET /api/metrics-summary` | Summary metrics for panels (as implemented) |
| `POST /api/stop` | Stop pipeline request (as implemented) |
| `GET /health` | `{"ok": true}` |
| `GET /` | Serves `ui/index.html` |

**Response shape for state** — conceptually as in the original dashboard PRD (field names may match merged API — verify against `server.py`):

```json
{
  "pipeline_status": "WAITING_FOR_SENTINEL",
  "current_phase_raw_id": "CORE-4",
  "current_agent": "executor",
  "planner_retries": 0,
  "executor_retries": 1,
  "reviewer_retries": 0,
  "project_path": "/home/pi/projects/myproject",
  "orchestrator_alive": true,
  "escalation_resets": 0,
  "escalation_trigger_reason": null
}
```

**Endpoints — Project Ideas & Setup** (representative; full contracts in roadmap / tests)

| Area | Examples |
|------|----------|
| Ideas CRUD + session | `GET/POST /api/ideas`, `DELETE /api/ideas/{id}`, `GET /api/ideas/{id}/session` |
| Agent turn | `POST /api/ideas/{id}/message` (webhook + sentinel poll) |
| Upload / clarity / readiness / convert | `POST .../upload`, `POST .../clarity-check`, `GET .../readiness`, `POST .../convert` |
| Downloads | `GET .../download`, `GET .../download-roadmap` |
| Setup | `POST /api/setup/roadmap-seed`, `POST /api/setup/validate-roadmap`, `POST /api/setup/preflight`, `POST /api/setup/launch` |

Implementations must match **roadmap** phases and tests; this PRD states **intent**, not every HTTP detail.

### Polling fallback

If SSE is not viable, fall back to polling `GET /api/state` on a ~3s interval and `GET /api/events` on a ~5s interval. **Ideas** agent turns use **server-side** sentinel polling after webhook (not SSE).

---

## Frontend — Web Application

### File: `ui/index.html`

Single HTML file. React and Tailwind via CDN. No build step. All JS inline or loaded as a companion module.

### Application shell — Sidebar and three screens

**Sidebar (left):** Fixed width; wordmark **AUTODEV**; navigation to:

| Key | Label |
|-----|-------|
| `pipeline` | Pipeline Monitor |
| `preflight` | Setup & Preflight |
| `ideas` | Project Ideas |

Active item uses accent highlight per design system.

**Routing:** One React tree switches `currentScreen`. **Default:** `pipeline`. **Ideas → Setup:** “Proceed to Setup” passes **roadmap seed** into preflight state.

---

### Screen 1 — Pipeline Monitor (dashboard)

#### Layout

```
┌─────────────────────────────────────────────────────┐
│  [path segments]              [Stop?] [dot] [status] │  ← Header bar
├──────────────────────┬──────────────────────────────┤
│                      │                               │
│   CURRENT PHASE      │   ROADMAP                     │
│   (+ Escalation cmd  │   (scrollable)                │
│    when WFH)         │                               │
│                      │                               │
├──────────────────────┴──────────────────────────────┤
│   ACTIVITY FEED                                      │
├─────────────────────────────────────────────────────┤
│   FOOTER — refresh, last update                     │
└─────────────────────────────────────────────────────┘
```

Responsive: on narrow screens, stack vertically (header → phase → roadmap → feed → footer).

#### Header bar

**Left:** Project context — e.g. last two path segments in monospace (from state).

**Center / right:** Pipeline **status** pill (large, color-coded), **orchestrator liveness** dot (green/red), **Stop Pipeline** when the pipeline is in a stoppable state — with confirmation modal; copy should reflect that stop completes the current agent turn before halting.

| Status | Treatment (intent) |
|--------|---------------------|
| RUNNING | Teal `run-pulse` on `bg-[#0d9488]` (active compute) |
| WAITING_FOR_SENTINEL | Static teal `#0d9488` (no pulse); pill **Running** or **Running {Agent}** (`formatWaitForSentinelLabel` from `current_agent` / `live_current_agent`) |
| WAITING_FOR_HUMAN | Orange solid; pill **NEEDS YOUR INPUT** |
| HALTED_SILENT | Red solid; pill **INTERVENTION REQUIRED** (native `title` with escalation-failure hint) |
| BLOCKED | Red solid |
| Other `pipeline_status` values | See `docs/prd/AUTODEV-UI-PRD.md` § Header Bar (`PIPELINE_LIVE_PILL` in `ui/index.html`) |
| Orchestrator dead while RUNNING / WAITING_FOR_SENTINEL | Override: ORCHESTRATOR DOWN (red) |

#### Current Phase Panel (left)

**Phase ID and goal** — Phase raw ID in monospace; goal from `GET /api/roadmap` by matching `current_phase_raw_id`.

**Active agent** — Badge: PLANNER / EXECUTOR / REVIEWER / ESCALATION — distinct muted hues (terminal-like, not traffic lights).

**Attempt counters** — Planner / Executor / Reviewer dot rows reflecting retry consumption.

**Last error code** — When present, monospace; optional expand.

**Elapsed in current state** — Live timer from `last_action_timestamp`; **amber** emphasis if stuck in `WAITING_FOR_SENTINEL` beyond ~5 minutes.

**Skill injected** — When `skill_injected` + `skill_agent` present: small muted line.

**Escalation Command Panel** — See dedicated section below; only when `WAITING_FOR_HUMAN`.

#### Roadmap Panel (right)

Scrollable list: status icons (complete / in_progress / pending / skipped / blocked), **current** row highlighted with **accent** left border, progress **N / T** at top. Rows expand inline for full goal + exit criteria. **Git tag** on complete rows: deferred unless API exists.

#### Activity Feed (bottom)

Last ~30 events, reverse chronological. Columns: time (monospace), **badge** (human-readable event type label; **raw machine id** on native **`title`**), agent, phase, attempt, truncated detail — expand row for full detail.

**Event type colors (muted):** Same intent as original — e.g. gate_pass green tint; gate_fail/retry amber; escalation_trigger orange; escalation_resolve blue; phase_complete brighter green; neutral gray for others.

**Live updates:** SSE where possible; new rows fade in ~300ms. **Fallback:** poll `GET /api/events` if SSE fails.

**Empty buffer:** Muted **"No events yet. Events appear here as the pipeline runs."** when the in-memory ring has no rows.

**Absent log:** Muted placeholder — event log unavailable; core panels still work.

#### Escalation Log (within feed)

Tab or toggle: **Activity** vs **Escalation** paired view — trigger time, reason, command, resolve time, duration; in-progress shows “Awaiting command…”.

---

### Screen 2 — Project Ideas

**Purpose:** Library of **ideas** (each with persisted `session.json` + drafts under `ideas_dir`). **Left column:** list + New + per-row actions. **Main area:** conversation (user/assistant) + **PRD document** pane + upload / conversion / proceed.

#### Layout (conceptual)

```
┌──────────┬──────────────────────┬─────────────────────────────┐
│  Ideas   │   Conversation       │   PRD document + actions     │
│  list    │   (scroll)           │   (scroll)                   │
│          │                      │   Upload / readiness /       │
│  + New   │   Composer (bottom)  │   Generate roadmap / Proceed │
└──────────┴──────────────────────┴─────────────────────────────┘
```

#### Target product behavior (user intent)

| Topic | Intent |
|-------|--------|
| **Titles** | Users see **human-meaningful** names — user-set or AI-derived after a substantive turn — **not** raw UUIDs as the primary list label. |
| **Composer** | **Multi-line**, **word-wrapped** input — not a single-line field for long prompts. |
| **Persistence** | Reload restores **messages + PRD**; completed agent turns always show **assistant reply** and updated **prd_content**. |
| **Working state** | **Subtle** loading on the document side — **not** a loud full-pane flash or highlighter-yellow treatment. |
| **Upload PRD** | Prefer **ingest → map/synthesize into canonical sections → preview/edit**; hard-reject only unusable input (e.g. binary). |
| **Thin prompts** | Agent should **ask clarifying questions** before claiming a “complete” PRD — collaboration over one-shot generation. |
| **Download PRD** | Control in **document** chrome; **enabled only** when there is real content — **never** empty/zero-byte downloads. |
| **Clarity / readiness / convert** | Surfaces pass/fail, issues, readiness, roadmap output, download roadmap, **Proceed to Setup**. |

#### Shipped UI copy (Ideas rail + PRD strip)

These strings are implemented in `ui/index.html` (`IdeasScreen`) and are the operator-facing source of truth (see UX tracker L-17–L-19):

- **Empty ideas list** (chats rail, no sessions): `No projects yet. Click + New to start a PRD conversation.`
- **When readiness is `ready`** and `readiness.json` is loaded: the PRD document strip shows **`PRD readiness:`** `{score}` **`/ 10`** and **`Roadmap confidence:`** `{value}`. The value is still the **`conversion_confidence`** field from `readiness.json`, returned as-is in `GET /api/ideas/{id}/readiness` under `data` (display label only — do not rename the JSON key in agent output).

---

### Screen 3 — Setup & Preflight

**Purpose:** Gate **launch**: validated roadmap seed + healthy environment + init.

#### Gating chain (intent)

1. **Project repository path** — Absolute path; user **locks** when confirmed.  
2. **Roadmap seed** — From Ideas or paste/upload; user **locks** when confirmed.  
3. **Validate roadmap** — `POST /api/setup/validate-roadmap`; line-level errors; **launch requires valid seed**, not merely non-empty text.  
4. **Run preflight** — `POST /api/setup/preflight`; per-check pass/fail/warn.  
5. **Launch** — `POST /api/setup/launch`; init repo, commit, set `~/.openclaw/pipeline-project` symlink.

**Browser constraint:** No **native folder picker** in v1 — typed path + helper copy (paste from terminal / `pwd`). Optional future helpers are non-MVP.

**Lock UX:** “Lock” = **confirmed for validation**, not “any string unlocks.” Labels and states must distinguish **editable**, **locked**, and **validated**. Full **Validate → Preflight → Launch** affordances are required for the intended experience (if UI only shows locks without actions, that is a **delivery gap** vs this PRD).

---

## Aesthetic Direction

**Theme:** Dark. Background near-black (`#0d0f12`), panels slightly lighter (`#141618`). Borders subtle.

**Typography:** Wordmark / phase IDs / status: monospace (e.g. JetBrains Mono). Body: clean sans (e.g. IBM Plex Sans). Paths / JSON: monospace.

**Accent:** Cyan-teal `#00b4d8` — sparingly: current phase highlight, primary actions, progress fill.

**Status colors:** Amber active/waiting, orange human-required, red error/halted, muted green complete — **muted**, not saturated primaries.

**Density:** Medium-high — ops tool, not marketing dashboard.

**No:** Gradients on backgrounds. Purple. Heavy drop shadows. **Emoji anywhere in the UI.** Animations limited to **status pulse** (active pipeline states), **feed fade-in**, and **Stop “stopping…”** pulse — avoid extra motion on Ideas document pane beyond a subtle working indicator.

---

## Escalation Command Panel

When `pipeline_status` is `WAITING_FOR_HUMAN`, surface the command panel — **only then**. Same mechanism as escalation agent: write `escalation_output.json` then sentinel via `POST /api/command`.

**Header:** e.g. “Escalation — Human Input Required” + trigger reason from state (`escalation_trigger_reason` / fallback).

| Button | Command | Notes |
|--------|---------|--------|
| RETRY | `RETRY` | Immediate |
| RESET EXECUTION | `RESET_EXECUTION` | Immediate |
| RESET PHASE | `RESET_PHASE` | **Confirm** |
| SKIP | `SKIP` | **Confirm** |
| PROCEED | `PROCEED` | Immediate |
| STOP | `STOP` | **Confirm** |

**Reset cap:** If `escalation_resets >= 3`, disable `RESET_PHASE` and `RESET_EXECUTION` with tooltip per roadmap.

**Post-click:** “Command sent — waiting for orchestrator…” until state leaves `WAITING_FOR_HUMAN`.

### Backend — `POST /api/command`

Same contract as original PRD: validate state, cap, command allowlist; write JSON then `.done`; **409** when not waiting; **409** when cap blocks reset commands.

```json
{ "command": "RESET_EXECUTION" }
```

```json
{
  "command": "RESET_EXECUTION",
  "source": "ui",
  "timestamp": "2026-03-14T11:22:01Z"
}
```

Verify exact filenames and schema against **orchestrator** expectations before changing behavior.

---

## Non-Requirements (v1)

From the original dashboard PRD (still apply where relevant):

- Signal message viewer  
- OpenClaw session log viewer  
- Multi-project switcher inside Monitor  
- User authentication  
- Mobile-first layout (responsive stacking is acceptable)  
- App-side SQL database  
- Push notifications  

**Additional / clarified:**

- **Greenfield-only** enhancement path for existing repos (see execution roadmap scope).  
- **Streaming** PRD agent output (webhook + sentinel is the model).  
- **Native OS directory picker** as a hard requirement.

---

## Deployment

```bash
# Example — adjust for your environment
uvicorn ui.server:app --host 0.0.0.0 --port 18790
```

Optional: `ui/autodev-ui.service` systemd template on the Pi.

All endpoints must **fail soft** when files are missing (defaults / empty collections, not unhandled 500s) where the roadmap requires it.

---

## Phasing Suggestion for Roadmap

The repository may already use **[roadmap.md](../../roadmap.md)** with `INFRA-*`, `UI-E*`, etc. When deriving new work from this PRD:

1. **INFRA / API** — Health, static, state, roadmap, events, SSE, command, stop, metrics  
2. **UI — Monitor** — Shell, header, phase panel, roadmap panel, feed, escalation tab, command panel, polish  
3. **API — Ideas** — Ideas CRUD, session, message + polling, upload, clarity, readiness, convert, downloads  
4. **UI — Ideas** — Split layout, list, composer, PRD pane, conversion bar, proceed  
5. **API — Setup** — roadmap-seed, validate, preflight, launch  
6. **UI — Setup** — Locks + explicit Validate / Preflight / Launch + results surfaces  

Each phase should be **independently testable**. Backend phases should be verifiable without a finished frontend.

---

## Document history

| Date | Change |
|------|--------|
| 2026-03-20 | Full-application PRD aligned to **AUTODEV-UI-PRD.md** structure (sections, tables, ASCII, tone). |
| 2026-03-20 | Prior draft (numbered sections only) superseded by this structure. |
