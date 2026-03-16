# AutoDev Pipeline Dashboard — Development Roadmap

> Source PRD: `deployment-package/Updates/AUTODEV-UI-PRD.md`
> Generated: 2026-03-16
> Total Phases: 23

This document is the **source of truth** for intent, phase sequencing, and execution state of the AutoDev Pipeline Dashboard build.

- Process and workflow live in the agent's SKILL.md.
- Phase archives live in `phases/{phase_id}.md` (one per completed phase).
- Execution metrics live in `metrics.jsonl`.
- Hard-won insights live in `lessons.md`.

---

## Pre-Flight Findings

Pre-flight analysis completed against the live filesystem before this roadmap was produced. Findings are binding — they override any config defaults in the PRD.

| Item | Finding |
|---|---|
| `pipeline_state.json` path | `~/.openclaw/pipeline_state.json` (workspace root, not inside `pipeline-project`) |
| `phase_state.json` path | `~/.openclaw/pipeline-project/phase_state.json` |
| `pipeline.lock` path | `~/.openclaw/pipeline.lock` |
| `pipeline_events.jsonl` | Does not exist — synthetic event feed is the v1 primary source; file is the upgrade path |
| Port 18790 | No conflict — confirmed available |
| `ui/` directory | Does not exist — to be created in INFRA-1 |
| Python version | 3.11.2 |
| FastAPI / uvicorn | **Not installed** — must be declared in `requirements.txt` and installed before the server can run |
| PATCH-1 (`skill_injected` write) | **Applied** — `_record_injected_skill()` added to `orchestrator.py`; writes `skill_injected` and `skill_agent` to `phase_state.json` after each `inject_skill()` call |
| PATCH-2 (`escalation_trigger_reason` write) | **Applied** — `orchestrator.py` writes `escalation_trigger_reason` to `phase_state.json` at all three `WAITING_FOR_HUMAN` transition sites |

---

## Conventions

### Phase ID

Format: `{SUBSYSTEM}-{N}` — e.g., `API-1`, `UI-3`, `CORE-2`

### Done Criteria

A phase is complete only when ALL of the following are true:

1. All tests pass (full suite, not just current phase's tests).
2. Linting passes (`ruff check .`).
3. Cloud review returns **APPROVED**.
4. Changes committed with message `phase({phase_id}): {goal summary}`.
5. Roadmap checkbox updated from `- [ ]` to `- [x]`.
6. Phase archive written to `phases/{phase_id}.md`.
7. Metric appended to `metrics.jsonl`.

### Cross-Cutting Constraints

These apply to every phase:

- **TDD**: Tests written before implementation. A test that passes before any code is written is invalid.
- **Graceful degradation**: Every endpoint must return a valid response (empty state, not 500) when state files are absent.
- **No secrets in source**: Config paths, tokens, and keys are never hardcoded.
- **Atomic writes**: Any phase that writes files to the filesystem uses temp-file + rename. Never write sentinel before payload.
- **Scope**: Never expand beyond the stated goal. Defer discoveries to `lessons.md`.

---

## Milestones

- **M1 — Foundation**: `INFRA-1`, `INFRA-2`
- **M2 — Backend**: `CORE-1`, `API-1`, `API-2`, `CORE-2`, `API-3`, `API-4`, `API-5`
- **M3 — Frontend Shell**: `UI-1`, `UI-2`
- **M4 — Dashboard Panels**: `UI-3`, `UI-4`, `UI-5`, `UI-6`
- **M5 — Live Feed & Escalation Log**: `UI-7`, `UI-8`, `UI-9`
- **M6 — Command Panel**: `UI-10`, `UI-11`
- **M7 — Polish & Deployment**: `UI-12`, `UI-13`, `INFRA-3`

---

## Phase Catalog

### Milestone 1 — Foundation

- [x] `INFRA-1` | LOW | Create the `ui/` directory with `server.py` scaffold, `config.json` defaults, and `requirements.txt`
  > Test: `ui/server.py` imports cleanly with no errors. `load_config()` with no `config.json` present returns all seven keys (`port`, `pipeline_state_path`, `phase_state_path`, `lock_path`, `events_path`, `roadmap_path`, `project_dir_path`) with `~` expanded to absolute paths matching the verified pre-flight paths. `load_config()` with a partial `config.json` overrides only provided keys and preserves defaults for the rest. `requirements.txt` lists `fastapi` and `uvicorn`.
  > Notes: Verified defaults — `pipeline_state_path: ~/.openclaw/pipeline_state.json`, `lock_path: ~/.openclaw/pipeline.lock`, `project_dir_path: ~/.openclaw/pipeline-project`. Port default: 18790. Server is not yet started in this phase — scaffold only.

- [x] `INFRA-2` | LOW | Start the FastAPI application, serve `ui/index.html` at `GET /`, and return `{"ok": true}` at `GET /health`
  > Test: `uvicorn ui.server:app` starts without error. `GET /health` returns 200 `{"ok": true}`. `GET /` with `ui/index.html` present returns 200 with `Content-Type: text/html`. `GET /` with `ui/index.html` absent returns 404. Server starts correctly when `ui/config.json` is absent (uses defaults).

---

### Milestone 2 — Backend

- [x] `CORE-1` | LOW | Implement a parser for the pipeline project's `roadmap.md` checkbox format returning structured phase data
  > Test: `- [x] \`INFRA-1\` | LOW | Goal text` parses to `{id: "INFRA-1", goal: "Goal text", status: "complete", exit_criteria: []}`. `[ ]` → `pending`, `[-]` → `skipped`, `[!]` → `blocked`. A phase followed by `> criteria line` has that line in `exit_criteria`. Multiple `>` lines produce multiple exit criteria entries. Non-phase lines (headers, blank lines, comment lines) produce no output. Parser returns empty list for empty or absent file without raising.
  > Notes: Roadmap format: `- [ ] \`phase-id\` | RISK | Goal` with optional `> ` exit criteria lines immediately after. Backticks around phase ID must be stripped from output. The `| RISK |` segment is present in roadmap lines but not needed in the API response — discard it.

- [x] `API-1` | HIGH | Add `GET /api/state` returning merged `pipeline_state.json` and `phase_state.json` with server-derived liveness and event source fields
  > Test: With both files present and lock held, returns 200 with all `pipeline_state.json` fields, `last_error_code` and `escalation_resets` from `phase_state.json`, `orchestrator_alive: true`, and `event_source: "synthetic"` (or `"file"` if `events_path` exists). With `pipeline_state.json` absent, returns 200 with sensible defaults (`pipeline_status: "UNKNOWN"` or equivalent, all counters 0, `orchestrator_alive: false`). With `phase_state.json` absent, `last_error_code` and `escalation_resets` are omitted from the response. `orchestrator_alive` is `true` when `fcntl.LOCK_EX | fcntl.LOCK_NB` on `lock_path` raises `BlockingIOError`, `false` when the lock is acquirable (acquired then immediately released).
  > Notes: HIGH risk — fcntl liveness check must acquire and immediately release. Never hold the lock. `event_source` is `"file"` if `events_path` file exists on disk, `"synthetic"` otherwise. All file reads must handle `FileNotFoundError` and `json.JSONDecodeError` gracefully.

- [x] `API-2` | LOW | Add `GET /api/roadmap` returning the parsed roadmap with the in-progress phase correctly identified
  > Test: Returns 200 JSON array of phase objects `{id, goal, status, exit_criteria}`. The phase whose `id` matches `current_phase_raw_id` from `pipeline_state.json` has `status: "in_progress"` regardless of its checkbox state in the file. All other phases reflect their checkbox status from CORE-1 parser. Returns empty array `[]` when `roadmap_path` is absent. When `pipeline_state.json` is absent or `current_phase_raw_id` is empty string, no phase is overridden to `in_progress`.
  > Notes: Depends on CORE-1. If a phase is both `[x]` and matches `current_phase_raw_id`, `in_progress` takes precedence.

- [x] `CORE-2` | HIGH | Implement the background state polling loop and in-memory synthetic event ring buffer
  > Test: After starting the polling loop and writing a new `pipeline_state.json` with a changed `pipeline_status`, a synthetic event appears in the ring buffer within 5 seconds with correct fields: `ts` (ISO timestamp), `event` (derived type string), `agent` (current_agent value), `phase` (current_phase_raw_id), and `detail` (human-readable description of the change). Same behavior for changes to `current_agent`, `current_phase_raw_id`, and incremented retry counters. Buffer caps at 50 entries — the 51st entry evicts the oldest. If `events_path` file exists, the ring buffer still accumulates synthetic events but the API layer serves from file (buffer unused as primary source). Polling loop continues running across repeated identical state reads without generating duplicate events.
  > Notes: HIGH risk — execution model choice is binding: if implemented as a **daemon thread**, use `threading.Lock` (or `collections.deque`, which is GIL-protected for `append`/`popleft`); if implemented as an **asyncio task** (preferred for FastAPI), use `asyncio.Lock` — `threading.Lock` will deadlock inside an event loop. Pick one model and be consistent throughout CORE-2, API-3, and API-4. Poll interval 2–3 seconds. The four trigger conditions: `pipeline_status` change, `current_agent` change, `current_phase_raw_id` change, any of `planner_retries` / `executor_retries` / `reviewer_retries` incrementing.

- [x] `API-3` | LOW | Add `GET /api/events?limit=N&offset=M` serving the synthetic ring buffer with file-source fallback
  > Test: Returns 200 `{"events": [...], "source": "synthetic", "total": <int>}`. With `limit=10&offset=0` returns up to 10 most-recent events in reverse chronological order. With `limit=10&offset=10` returns the next 10 older events. Empty buffer returns `{"events": [], "source": "synthetic", "total": 0}`. When `events_path` file exists, reads last N lines of the JSONL file in reverse order and returns `"source": "file"`. Default limit when parameter absent: 30.
  > Notes: Depends on CORE-2. File reading for the upgrade path: read lines from end of file using seek, parse each as JSON, skip malformed lines.

- [x] `API-4` | HIGH | Add `GET /api/events/stream` as an SSE endpoint pushing new ring buffer events and a 15-second heartbeat
  > Test: Client connecting via `EventSource` or curl receives an SSE `event: heartbeat` `data: {}` message within 15 seconds. When a new synthetic event is added to the ring buffer, the connected client receives it as an SSE message within one polling cycle (≤5 seconds). Connection remains open across multiple events without closing. A client connecting with an empty buffer receives no immediate data message but does receive the heartbeat. When `events_path` file exists, server tails the file and pushes new lines as SSE messages instead of reading from the ring buffer.
  > Notes: HIGH risk — use FastAPI `StreamingResponse` with `media_type: "text/event-stream"`. SSE format: `data: {json}\n\n`. Keep-alive via heartbeat prevents proxy/load-balancer timeouts. Each pushed event must be newline-terminated per SSE spec.

- [x] `API-5` | HIGH | Add `POST /api/command` that writes escalation files to `project_dir_path` after passing all validation checks
  > Test: `POST /api/command {"command": "RETRY"}` when `pipeline_status` is `WAITING_FOR_HUMAN` returns 200, creates `project_dir_path/escalation_output.json` containing `{command: "RETRY", source: "ui", timestamp: <ISO>}`, then creates `project_dir_path/escalation_output.done`. Same request when `pipeline_status` is `RUNNING` returns 409 `{"error": "Pipeline is not waiting for human input"}`. `{"command": "UNKNOWN_CMD"}` returns 400. `{"command": "RESET_PHASE"}` when `escalation_resets >= 3` returns 409 `{"error": "Reset cap reached"}`. `POST /api/command` when `project_dir_path` symlink is dangling or absent returns 503. `escalation_output.json` is always written before `escalation_output.done`.
  > Notes: HIGH risk — write order is critical: JSON payload first, sentinel second (matches orchestrator expectation). Re-read `pipeline_state.json` and `phase_state.json` immediately before writing — do not use a cached state. Valid commands: `RETRY`, `RESET_EXECUTION`, `RESET_PHASE`, `SKIP`, `PROCEED`, `STOP`. RESET_PHASE and RESET_EXECUTION are subject to the cap check; others are not.

---

### Milestone 3 — Frontend Shell

- [x] `UI-1` | LOW | Create `ui/index.html` with a 3-panel grid layout and CDN imports for React, Tailwind, and web fonts
  > Test: Page loads in browser with no JavaScript console errors. Three layout regions are present and non-overlapping: a header bar (top), a two-column middle section (left panel + right panel), and a full-width bottom panel. Tailwind utility classes apply correctly. React CDN import resolves. JetBrains Mono or Space Mono font loads. IBM Plex Sans or DM Sans body font loads. No build step — all resources loaded via CDN or inline script tags.
  > Notes: No-build React setup: UMD React + ReactDOM via CDN, Babel standalone for JSX transform (or `htm` if preferred). All JS either inline in `index.html` or in a companion `ui/app.js` loaded as a module. Tailwind CDN (Play CDN or standalone script). Responsive layout handled in UI-13 — this phase establishes the desktop grid only.

- [x] `UI-2` | LOW | Implement the header bar showing the AUTODEV wordmark, pipeline status pill, project path, and liveness dot
  > Test: AUTODEV wordmark renders in JetBrains Mono or Space Mono. Status pill renders correct label and color class for each of the six states: RUNNING (amber pulse), WAITING_FOR_SENTINEL (amber pulse, label "WAITING — {agent}"), WAITING_FOR_HUMAN (orange solid), HALTED_SILENT (red solid), BLOCKED (red solid). When `orchestrator_alive` is `false` and `pipeline_status` is `RUNNING` or `WAITING_FOR_SENTINEL`, status pill overrides to "ORCHESTRATOR DOWN" in red. Project path displays last two path segments in monospace. Liveness dot is visually green when `orchestrator_alive: true`, red when `false`. Header re-renders correctly on each state poll.
  > Notes: Polls `GET /api/state` every 3 seconds. Amber pulse is a CSS animation — only applied to RUNNING and WAITING_FOR_SENTINEL states; all others are static. The "ORCHESTRATOR DOWN" override takes precedence over any other label when the liveness condition is met.

---

### Milestone 4 — Dashboard Panels

- [x] `UI-3` | LOW | Implement the Current Phase panel showing phase ID, goal text, and active agent badge
  > Test: `current_phase_raw_id` from state renders in monospace as the phase ID label. Goal text for the current phase is fetched from `GET /api/roadmap` by matching `current_phase_raw_id` and renders as a readable sentence below the ID. Agent badge renders one of four variants (PLANNER, EXECUTOR, REVIEWER, ESCALATION), each in a distinct muted color. When `current_phase_raw_id` is empty or not found in roadmap, panel shows a neutral "No active phase" placeholder without errors.
  > Notes: Requires one `GET /api/state` call (for ID and agent) and one `GET /api/roadmap` call (for goal text). Goal text lookup should only trigger when `current_phase_raw_id` changes, not on every state poll.

- [x] `UI-4` | LOW | Add attempt counters, last error code, elapsed timer, and skill injected display to the Current Phase panel
  > Test: Planner, Executor, Reviewer dot counters render filled/empty dots corresponding to `planner_retries`, `executor_retries`, `reviewer_retries` (e.g., `●●○ 2/3`). `last_error_code` renders in monospace only when the field is present in the state response — absent or null means the field is not shown. Elapsed timer increments live in the browser from `last_action_timestamp`; when `pipeline_status` is `WAITING_FOR_SENTINEL` and elapsed exceeds 5 minutes, timer text color changes to amber. Skill injected renders as "discipline / agent" in small muted text when both `skill_injected` (non-null) and `skill_agent` are present in state; hidden otherwise.
  > Notes: Elapsed timer uses client-side `setInterval` (1-second tick), not server push. Counter dots cap display at 3 (matching the 3-attempt budget per role). All four fields read from the merged `GET /api/state` response — no additional API calls.

- [x] `UI-5` | LOW | Implement the Roadmap panel showing all phases as a scrollable list with status icons, current phase highlight, and phase count progress bar
  > Test: All phases from `GET /api/roadmap` render one row each. Status icons match: ✓ complete, ▶ in_progress, ○ pending, ⊘ skipped, ⚠ blocked. The in-progress phase row has a left border accent in the primary accent color and is visually distinct from others. Complete phases are visually muted (reduced opacity or lighter text). Blocked phases have a red tint. Progress bar above the list shows "N / T complete" where N is the count of complete phases and T is total phases. Panel is scrollable when phase count exceeds visible height.
  > Notes: Depends on `GET /api/roadmap`. Roadmap panel re-renders when state poll detects a `current_phase_raw_id` change. Progress bar fill percentage = complete / total.

- [x] `UI-6` | LOW | Add inline expand/collapse to roadmap phase rows showing full goal text and exit criteria
  > Test: Clicking a phase row expands it inline without navigation or page reload. Expanded state shows the full goal text and each entry in `exit_criteria` as a separate line (if the array is non-empty). Clicking the expanded row again collapses it. When a second row is clicked while another is expanded, the first collapses and the second expands (only one row expanded at a time). Rows with empty `exit_criteria` expand to show goal only, with no exit criteria section rendered.
  > Notes: Git tag display for complete phases is deferred — no API mechanism exists to retrieve git tags in v1. Expand/collapse is pure frontend state (no additional API calls). `exit_criteria` comes from the `exit_criteria` array in the `GET /api/roadmap` response.

---

### Milestone 5 — Live Feed & Escalation Log

- [x] `UI-7` | LOW | Implement the Activity Feed panel with static event row rendering, color-coded type badges, and inline row expansion
  > Test: Up to 30 events from `GET /api/events` render in reverse chronological order. Each row shows: timestamp (time portion only, monospace), event type badge, agent, phase, attempt (or `—` if null), detail (truncated to ~60 chars). Badge colors match: `gate_pass` green tint, `gate_fail` and `retry` amber tint, `escalation_trigger` orange tint, `escalation_resolve` blue tint, `phase_complete` bright green, all others neutral gray. Clicking any row expands inline to show full detail text; clicking again collapses. When the event buffer is empty, a muted "No events recorded yet" placeholder renders.
  > Notes: Initial render populates from `GET /api/events?limit=30`. Color tints are muted (not saturated primaries). Row expand uses the same single-active pattern as UI-6.

- [x] `UI-8` | HIGH | Connect the Activity Feed to the SSE stream so new events appear at the top with a fade-in animation, with polling fallback when SSE is unavailable
  > Test: After connecting to `GET /api/events/stream`, new events appear at the top of the feed list without page refresh, within one polling cycle. New event rows have a visible CSS fade-in animation (~300ms). Heartbeat SSE messages do not appear as visible rows. When `EventSource` connection fails (network error or server unavailable), the frontend falls back to polling `GET /api/events` every 5 seconds and continues displaying events. Fallback polling resumes SSE when connection becomes available again.
  > Notes: HIGH risk — `EventSource` reconnection behavior differs by browser. Detect fallback trigger via `EventSource.onerror`. During fallback, dedup events against already-displayed entries by comparing `ts` field to avoid duplicate rows.

- [x] `UI-9` | LOW | Add an Escalation Log tab within the Activity Feed panel showing paired escalation events
  > Test: A tab or toggle control switches the feed between "Activity" view and "Escalation" view. Escalation view shows only escalation pairs: each pair displays phase ID, triggered timestamp, trigger reason, command received, resolved timestamp, and elapsed duration (e.g., "7m 21s"). An in-progress escalation (trigger event with no matching resolve event) shows "Awaiting command..." in place of command and resolution fields. When no escalation events are in the ring buffer, the escalation view shows "No escalations recorded in this session." Switching tabs does not trigger additional API calls.
  > Notes: Pairing logic: match `escalation_trigger` event to subsequent `escalation_resolve` event for the same `phase` field. Trigger reason comes from the synthetic event's `detail` field (which the engine populates from `escalation_trigger_reason` in `phase_state.json`).

---

### Milestone 6 — Command Panel

- [x] `UI-10` | HIGH | Implement the Escalation Command Panel with conditional visibility, all six command buttons, and immediate execution for non-destructive commands
  > Test: Command panel does not render in any state other than `WAITING_FOR_HUMAN` — verified across RUNNING, WAITING_FOR_SENTINEL, HALTED_SILENT, and BLOCKED. Panel renders within the Current Phase panel with an orange border when `pipeline_status` is `WAITING_FOR_HUMAN`. Panel header shows trigger reason from state (or `last_action` fallback). All six buttons (RETRY, RESET EXECUTION, RESET PHASE, SKIP, PROCEED, STOP) render with their PRD descriptions. Clicking RETRY, RESET EXECUTION, or PROCEED sends `POST /api/command` immediately and the panel transitions to "Command sent — waiting for orchestrator..." state. The waiting state displays until the next state poll shows `pipeline_status` has left `WAITING_FOR_HUMAN`.
  > Notes: HIGH risk — conditional rendering must be driven strictly by `pipeline_status` value from state, not by any local UI flag. Trigger reason displayed in header comes from `escalation_trigger_reason` in the state response (added via PATCH-2); fallback to `last_action` when absent.

- [ ] `UI-11` | LOW | Add confirmation modals for destructive commands and reset cap enforcement to the Escalation Command Panel
  > Test: Clicking RESET PHASE, SKIP, or STOP shows a modal with the text "Are you sure? This cannot be undone." Confirming the modal sends the command via `POST /api/command`; dismissing the modal closes it without sending any command. When `escalation_resets >= 3` in the state response, RESET PHASE and RESET EXECUTION buttons are visually disabled and show a tooltip "Reset cap reached (3/3). Use PROCEED or STOP." when hovered. Disabled buttons do not send any request on click. RETRY, PROCEED, and SKIP are never disabled by the reset cap.
  > Notes: `escalation_resets` is read from the merged state response. Modal is inline (not a browser `confirm()` dialog). Cap enforcement mirrors the server-side check in API-5 — both layers enforce the cap independently.

---

### Milestone 7 — Polish & Deployment

- [ ] `UI-12` | LOW | Apply the full design system — dark theme, typography hierarchy, accent palette, and status colors — across all panels
  > Test: Background is in the `#0d0f12` range (near-black, not pure black). Panel backgrounds are slightly lighter (`#141618` range). Borders are subtle (not white or harsh). AUTODEV wordmark and all phase IDs render in JetBrains Mono or Space Mono. Body text and labels render in IBM Plex Sans or DM Sans (not Inter or system font). Log output, paths, and JSON values use a monospace font. The primary accent color is in the `#00b4d8` (cyan-teal) range and appears on: current phase highlight border, active agent badge background, and progress bar fill. No gradients on backgrounds. No purple anywhere. No drop shadows on panel cards. No emoji anywhere in the UI.
  > Notes: This phase is the full visual pass — all previous UI phases render with minimal/placeholder styling and this phase applies the complete design spec. No new functionality. Pure CSS/style changes. Status color rules: amber for active/waiting states, orange for human-required, red for error/halted, muted green for complete.

- [ ] `UI-13` | LOW | Apply responsive vertical stacking for narrow viewports and add pulse animation to active status states
  > Test: At viewport width ≤ 768px, the layout stacks vertically in order: header → current phase panel → roadmap panel → activity feed. At viewport width > 768px, the two-column desktop layout is restored. RUNNING and WAITING_FOR_SENTINEL status pills have a visible, repeating CSS pulse animation. WAITING_FOR_HUMAN, HALTED_SILENT, and BLOCKED pills have no animation (static solid color). New event rows in the Activity Feed fade in visibly over ~300ms. No other animations are present in the UI.
  > Notes: Responsive breakpoint via Tailwind `sm:` or a media query. Pulse animation: CSS `@keyframes` or Tailwind `animate-pulse`. Feed fade-in: CSS transition on `opacity` from 0 to 1. These are the only two animations permitted per the PRD Aesthetic Direction.

- [ ] `INFRA-3` | LOW | Add `ui/autodev-ui.service` as a systemd unit file template for running the UI server on the Pi
  > Test: File parses as a valid systemd unit (`systemd-analyze verify ui/autodev-ui.service` returns no errors, or manual inspection confirms required sections). `[Unit]` section has `Description` and `After=network.target`. `[Service]` section has `ExecStart` pointing to the correct Python interpreter and `ui/server.py`, `WorkingDirectory` set to the project root, `Restart=on-failure`, and `RestartSec=5`. `[Install]` section has `WantedBy=multi-user.target`. File includes inline comments documenting the install steps: copy to `/etc/systemd/system/`, run `systemctl daemon-reload`, `systemctl enable autodev-ui`, `systemctl start autodev-ui`.
  > Notes: `ExecStart` should use the full path to the Python interpreter (e.g., `/usr/bin/python3`). Do not hardcode the project path — use a placeholder comment instructing the operator to edit `WorkingDirectory` before installing.

---

## Transformation Summary

**PRD Source**: AutoDev Pipeline Dashboard — Product Requirements Document
**Date**: 2026-03-16
**Total Capabilities Identified**: 23
**Total Phases Created**: 23
**Subsystems Involved**: INFRA (3), CORE (2), API (5), UI (13)
**Estimated Complexity**: High (23 phases, 6 HIGH-risk phases, non-trivial async/SSE implementation)

**Key Decisions**:
- `API-EVENTS` from the PRD was split into three phases (CORE-2, API-3, API-4) because the event engine, the REST endpoint, and the SSE stream are independently testable and have different risk profiles.
- `UI-PHASE` was split into UI-3 and UI-4 because the basic panel structure (ID, goal, badge) and the dynamic additions (counters, timer, error code, skill) have different data sources and test footprints.
- `UI-ROADMAP` was split into UI-5 and UI-6 because inline expand/collapse is a distinct interaction behavior testable independently from static rendering.
- `UI-COMMAND` was split into UI-10 and UI-11 because button rendering with immediate execution and confirmation modals with cap enforcement are independently verifiable units of behavior.
- `POLISH` was split into UI-12 (design system) and UI-13 (responsive layout + animations) because visual correctness and motion/layout are separable concerns with different test approaches.
- CORE-1 (roadmap parser) was placed before API-2 (roadmap endpoint) but can be developed in parallel with API-1.
- INFRA-3 (systemd service) is sequenced last as it is fully independent and purely a deployment artifact.

**Assumptions Made**:
- The `ui/` directory is a subdirectory of `~/.openclaw/` (the OpenClaw workspace root). All paths in `server.py` resolve relative to this assumption.
- The pipeline project's `roadmap.md` follows the exact checkbox format: `- [ ] \`phase-id\` | RISK | Goal` with optional `> ` exit criteria lines. Other `> ` line formats (e.g., `> Test:`, `> Notes:` from the Dev_Roadmap_template) are not present in pipeline project roadmaps.
- SSE is implemented via FastAPI `StreamingResponse` without an additional `sse-starlette` dependency. If this proves impractical during INFRA-1, `sse-starlette` should be added to `requirements.txt` and this assumption updated in `lessons.md`.
- Git tag display in the roadmap panel expand (mentioned in the PRD) is explicitly not implemented — no API mechanism exists in v1. The UI-6 phase shows goal and exit criteria only.
- The ring buffer does not persist across server restarts. This is acceptable for v1 per the PRD.

**Open Questions** (require stakeholder clarification before the affected phase begins):
- None blocking. All flagged items from the design review have been resolved or explicitly deferred with documented rationale.

---

## Validation Report

**Completeness** ✅
Every capability from the PRD maps to at least one phase. Non-requirements are not represented. The pre-implementation orchestrator patches (PATCH-1, PATCH-2) are noted as already applied — they are not phases in this roadmap.

**Atomicity** ✅
No phase contains "and then" in its Goal. Every phase maps to a single commit. The largest phases (CORE-2, API-5, UI-10) each have a single observable outcome as their goal.

**Verifiability** ✅
Every phase has a `> Test:` line describing external observables with specific assertions (status codes, field names, state values, visual behaviors). No test intent describes internal implementation steps.

**Dependencies** ✅
Phase ordering respects all technical dependencies. No circular dependencies exist. CORE-1 and API-1 can proceed in parallel after INFRA-1 (noted in milestone grouping). No API phase precedes the data layer it depends on.

**Format Compliance** ✅
All phases follow `- [ ] \`ID\` | RISK | Goal` format. All IDs use `{SUBSYSTEM}-{N}` convention. All phases have `> Test:` lines. Notes lines do not redefine goals. Phases are in execution order.

**Risk Coverage** ✅
Six phases marked HIGH: API-1 (fcntl liveness), CORE-2 (threading + ring buffer), API-4 (SSE streaming), API-5 (file write + validation ordering), UI-8 (SSE client + fallback), UI-10 (state-driven conditional rendering). All HIGH phases have test intents that specifically exercise the risk scenario.
