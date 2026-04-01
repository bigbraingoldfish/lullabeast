# Task 03 — Project Queue

*AutoDev MVP pre-tester work. Claude Code should read this document in full before planning.*

**Source of truth:** Queue semantics — including **park-and-advance** (escalation
and roadmap `BLOCKED`), **preflight skip-and-requeue**, **deferred escalation
commands**, **ingest** of an active project missing from the queue, and
**`queue_mode`** — are defined here. Implementation must match this document.

---

## Background

AutoDev currently handles one project at a time. When a pipeline run completes
or blocks, the next project requires manual setup. This task introduces a project
queue that allows users to line up multiple pre-validated projects, define
parent-child dependencies between them, and let the pipeline work through them
automatically — pausing only when every remaining entry is in dependency hold,
parked (escalation / roadmap blocked), or otherwise ineligible, such that
`QUEUE_HALTED` applies.

The queue is a separate screen from the Pipeline Monitor. The monitor shows what
is happening right now. The queue shows what is planned. These are different mental
modes and must not be combined.

---

## Core Concepts

### Queue entry states

**Invariant:** At most **one** queue entry may be `ACTIVE` at a time (`ACTIVE` means
this entry’s project is the current `pipeline-project` symlink target and is the
one the orchestrator is driving for non-parked work).

| State | Meaning |
|-------|---------|
| `READY` | Passed preflight, waiting its turn in the queue |
| `ACTIVE` | Currently being built by the pipeline (symlink + orchestrator run target) |
| `ESCALATION` | **Parked:** pipeline is in `WAITING_FOR_HUMAN` for this project; all resume artifacts under the project directory remain intact (phase branch, `phase_state.json`, failure context, etc.). The entry is **not** `ACTIVE` while another project may run. UI shows an **ESCALATION** tag; escalation actions remain available. |
| `BLOCKED` | **Parked:** roadmap gate returned **roadmap blocked** (`[!]` phase / gate exit indicating blocked). Same **park-and-advance** queue semantics as `ESCALATION` for advancing the queue in **auto** mode; distinct from preflight skip. UI may show a **BLOCKED** (roadmap) badge. |
| `SKIPPED_PENDING` | **Preflight skip only:** orchestrator preflight failed at start; entry moved to next-in-line position per skip-and-requeue rules — **not** used for escalation or roadmap blocked (those use `ESCALATION` / `BLOCKED`). |
| `DEPENDENCY_HOLD` | A parent project in the queue has not reached `COMPLETED` (parent may be `READY`, `ACTIVE`, `ESCALATION`, `BLOCKED`, `SKIPPED_PENDING`, etc.) |
| `COMPLETED` | Pipeline finished successfully |
| `FAILED` | Pipeline halted with a terminal error; requires manual intervention |

**Display note:** The queue list may derive an **ESCALATION** pill from queue state
`ESCALATION` **or** from persisted `parked_pipeline_status` / entry fields when
global `pipeline_state.json` no longer matches this row (e.g. after the symlink
advances to another project). Persisted fields are required so parked rows do not
“go blank” when another project is active.

### Dependency model
Projects in the queue can have a parent-child relationship. A child project
cannot become `ACTIVE` until its parent reaches `COMPLETED`. If a parent is
not `COMPLETED` (including `ESCALATION`, `BLOCKED`, `SKIPPED_PENDING`, or
`DEPENDENCY_HOLD` upstream), the child is `DEPENDENCY_HOLD` and is skipped when
its position is reached. The queue continues to the next dependency-clear
project.

### Skip-and-requeue behavior (preflight only)
When **preflight** fails at the time the orchestrator would start a project:
- That entry becomes `SKIPPED_PENDING`, `skip_count` increments, and the entry
  moves to the next position in the queue (not to the end — **next** slot per
  existing rules, including group moves where applicable)
- The following project may become `ACTIVE` if it is `READY` and has no
  unresolved parent dependency
- When the pipeline cycles back to the skipped project, if preflight still fails,
  skip again
- This path is **only** for preflight failure — **not** for escalation or
  roadmap blocked (see **Park-and-advance** below)

### Park-and-advance behavior (escalation and roadmap BLOCKED)
When the pipeline **escalates** (`WAITING_FOR_HUMAN`) or hits **roadmap blocked**
(gate `BLOCKED` / `[!]` phase):

1. **Park** the current queue entry: transition it from `ACTIVE` to `ESCALATION`
   (human escalation) or `BLOCKED` (roadmap blocked). Persist timestamps /
   optional snapshot fields on the entry so the Queue screen still shows the
   correct tag and context **after** the symlink points at another project.
2. **Preserve** all resume state under that project’s directory (`pipeline_state.json`
   in the project tree if used, `phase_state.json`, phase branch, failure context,
   etc.). Do not destructive-clean artifacts needed for resume or for the
   escalation agent / UI commands.
3. **Advance** (only if `queue_mode` is **`auto`**): select the next eligible
   project (`READY` / `SKIPPED_PENDING`, dependency-clear, preflight passes) via
   the same ordering and `_select_next_queue_project()` rules as after
   `PIPELINE_COMPLETE`. **Manual** mode: do **not** auto-start the next project;
   the user uses **Trigger next** or provides the configured manual feedback.
4. **Deferred escalation commands:** If the human issues a resume command
   (e.g. via `POST /api/command`) while the **symlink** points at **another**
   project, the command must be **recorded** for the parked project — either on
   the queue entry and/or under that project’s directory in a dedicated file
   (e.g. `pending_escalation_command.json`) with the same **write-then-done**
   ordering discipline as `escalation_output.json` / `escalation_output.done`
   where applicable. When that project becomes `ACTIVE` again, the orchestrator
   applies the pending command as the next action.

**QUEUE_HALTED:** Enter when **no** eligible project remains to run (e.g. all
non-terminal entries are `DEPENDENCY_HOLD`, or parked entries cannot be advanced,
or preflight skip exhausts options per spec). Reasons may include
`all_blocked`, `all_dependency_hold`, or `mixed` — aligned with
`queue_halted_reason` in `pipeline_state.json`.

### Pre-validation requirement
A project can only be added to the queue if it passes preflight checks at
add time. This is not a manual gate — the queue add action triggers a
preflight validation automatically and rejects the project if it fails,
showing the user exactly what needs to be fixed.

A second validation runs automatically when the orchestrator is about to
start a queued project (same validation, covers the edge case of config
drift between queue add and pipeline start).

---

## Data Model

### Queue state file
`~/.openclaw/pipeline_queue.json`

```json
{
  "queue": [
    {
      "id": "uuid",
      "project_path": "/path/to/project",
      "idea_id": "uuid",
      "name": "Project Name",
      "state": "READY",
      "position": 1,
      "parent_id": null,
      "added_at": "ISO8601",
      "started_at": null,
      "completed_at": null,
      "blocked_at": null,
      "skip_count": 0,
      "preflight_validated_at": "ISO8601",
      "notes": "",
      "parked_at": null,
      "parked_reason": null,
      "parked_pipeline_status": null
    }
  ],
  "queue_mode": "auto",
  "last_updated": "ISO8601"
}
```

`queue_mode`: `"auto"` (pipeline moves to next project automatically on
completion) or `"manual"` (pipeline stops between projects and waits for
user to trigger the next). User-toggleable from the queue screen.

All writes to `pipeline_queue.json` must use `mkstemp` + `os.replace`
(atomic write). Never write directly to this file.

**Optional fields (park-and-advance):** `parked_at`, `parked_reason`
(`escalation` \| `roadmap_blocked`), `parked_pipeline_status` (snapshot of
`pipeline_status` when parked, for UI when global state targets another project).
Implementations may add a stable reference for **deferred** resume commands
on the entry or document a project-local file path. Entries must remain
merge-compatible with older queue files (missing fields = null / absent).

### Ingest of active project not in queue
If `pipeline_state.json` references a `project_path` that is **not** represented
in `pipeline_queue.json` (same canonical realpath as an existing entry):

- **Ingest** that project into the queue for display (no “hidden” active work).
- Recommended: insert at **position 1** and shift others down; set state from
  live pipeline status (`ACTIVE` vs `ESCALATION` / `BLOCKED` as applicable).
- Use a stable generated `id` (e.g. UUID) and a sensible `name` (directory
  basename or existing naming rules). **Idempotent:** repeated reads must not
  duplicate the same project.

---

## Orchestrator Changes — `autodev/pipeline/orchestrator.py`

### Queue integration
On pipeline completion (`PIPELINE_COMPLETE` state), the orchestrator checks
`pipeline_queue.json`:
1. Mark the current project as `COMPLETED` in the queue
2. If `queue_mode` is `"manual"`: stop, write state, wait for user trigger
3. If `queue_mode` is `"auto"`: call `_select_next_queue_project()`

### Park-and-advance (escalation and roadmap BLOCKED)
When the pipeline enters **`WAITING_FOR_HUMAN`** (escalation path) or
**roadmap `BLOCKED`**:

1. Update the current queue entry from `ACTIVE` to **`ESCALATION`** or
   **`BLOCKED`** respectively; set `parked_at`, `parked_reason`, and
   `parked_pipeline_status` as needed.
2. Preserve all artifacts required for resume under the **parked** project’s
   directory (no destructive cleanup beyond existing contracts).
3. If `queue_mode` is **`auto`**: call `_select_next_queue_project()` to start
   the next eligible project (same pattern as after `PIPELINE_COMPLETE`). If
   **`manual`**: orchestrator stops without switching symlink to the next
   project until the user triggers the next step (see server **trigger-next**).
4. When the parked project is **selected again** (turn comes back, or user
   triggers resume), apply any **deferred** command recorded for that project
   before continuing normal escalation handling.

### `_select_next_queue_project()`
- Read `pipeline_queue.json`
- Walk the queue in position order
- For each project: eligible states are **`READY`** or **`SKIPPED_PENDING`** only.
  Entries in **`ESCALATION`**, **`BLOCKED`**, **`DEPENDENCY_HOLD`**, etc. are not
  started by this walk until they transition back to `READY` / `SKIPPED_PENDING`
  per product rules (e.g. after human resolution).
- For the first eligible project: check no unresolved parent dependency
- For the first eligible project:
  - Run preflight validation (same checks as `/api/setup/validate-repo`)
  - If passes: set state to `ACTIVE`, update `pipeline-project` symlink,
    update `pipeline_state.json`, begin orchestration
  - If fails: set state to `SKIPPED_PENDING`, increment `skip_count`,
    move to next position, try the following project
- If no eligible project found: enter `QUEUE_HALTED` state, write to
  `pipeline_state.json`, emit event to `pipeline_events.jsonl`

### `QUEUE_HALTED` state
New pipeline state. Written to `pipeline_state.json` as:
```json
{
  "pipeline_status": "QUEUE_HALTED",
  "status": "QUEUE_HALTED",
  "queue_halted_reason": "all_blocked | all_dependency_hold | mixed"
}
```
The Pipeline Monitor must surface this state clearly. The queue screen
shows which projects are blocked and why. Semantics of `all_blocked` must
align with **parked** entries (`ESCALATION` / `BLOCKED`) when no runnable
entry remains.

### On escalation resolution and resume
- While a project is **parked** (`ESCALATION`), the human may issue commands from
  the Pipeline Monitor or Queue action hub. Commands apply per **Deferred
  escalation commands** above when the symlink does not point at that project.
- When the project is **active** again, the orchestrator must resume from
  preserved state (phase branch, `phase_state.json`, etc.) so **RETRY** and
  related commands pick up the correct phase branch.
- **`BLOCKED` (roadmap)** entries follow the same park-and-advance and resume
  expectations for queue ordering; roadmap-specific UI copy may differ from
  escalation.

---

## Server Changes — `ui/server.py`

### New endpoints

**`GET /api/queue`**
Returns full `pipeline_queue.json` content plus computed fields:
- `dependency_tree`: nested structure showing parent-child relationships
- `next_eligible`: id of the next project that would run if triggered now
- After **ingest** (see Data Model): if global `project_path` is missing from
  the queue, synthesize or merge an entry so the active pipeline is always visible
- Enrich entries with `live_pipeline_status` **and/or** persisted parked fields
  so **`ESCALATION`** / roadmap **`BLOCKED`** rows stay accurate when another
  project is symlink-active

**`POST /api/queue/add`**
Body: `{ "project_path": str, "idea_id": str | null, "parent_id": str | null }`
- Runs preflight validation against `project_path`
- On pass: adds entry to queue with state `READY`, assigns next available
  position
- On fail: returns 400 with `{ "validation_errors": [...] }`

**`DELETE /api/queue/{entry_id}`**
- Removes entry from queue
- If entry is ACTIVE: returns 409 (cannot remove active project)
- Reorders remaining positions sequentially

**`PATCH /api/queue/{entry_id}/position`**
Body: `{ "position": int }`
- Reorders the entry to the specified position
- Shifts other entries accordingly
- Cannot move ACTIVE or COMPLETED entries

**`PATCH /api/queue/{entry_id}/parent`**
Body: `{ "parent_id": str | null }`
- Sets or clears parent dependency
- Validates no circular dependencies before writing
- Returns 400 if circular dependency detected

**`POST /api/queue/trigger-next`**
- Manually triggers `_select_next_queue_project()` from the server side
- Used when `queue_mode` is `"manual"` and user wants to start the next project
- Returns **409** only if a project is currently **`ACTIVE`** (running). Parked
  entries (`ESCALATION`, `BLOCKED`) **do not** count as `ACTIVE` — the next
  project must be startable when the previous row is parked and not running

**`PATCH /api/queue/mode`**
Body: `{ "queue_mode": "auto" | "manual" }`
- Toggles queue mode
- Takes effect on the next project transition

**`GET /api/queue/status`**
Returns summary for the Pipeline Monitor header integration:
```json
{
  "queue_length": 4,
  "ready_count": 2,
  "blocked_count": 1,
  "completed_count": 1,
  "queue_mode": "auto",
  "queue_halted": false
}
```

### Update `GET /api/state`
Add queue summary fields from `/api/queue/status` so the Pipeline Monitor
can show a queue status pill without a separate API call.

---

## New Screen — `ui/index.html`

### Navigation
Add "Project Queue" to the left sidebar navigation between Pipeline Monitor
and Setup & Preflight. Use a queue/list icon consistent with existing nav icons.

### Queue Screen layout
Three-column layout:

**Left column — Queue list (ordered)**
- Drag-to-reorder list of all queued projects
- Each entry shows: position number, project name, state badge, skip count
  if > 0, dependency indicator if has parent
- State badges use distinct colors:
  - READY: slate/neutral
  - ACTIVE: teal pulse (same treatment as pipeline running state)
  - ESCALATION: amber (parked, awaiting human / deferred command handling)
  - BLOCKED: amber or red per roadmap-blocked convention (parked roadmap)
  - SKIPPED_PENDING: orange
  - DEPENDENCY_HOLD: purple
  - COMPLETED: green (muted)
  - FAILED: red
- Click entry to select it and show detail in center column
- Drag handle on left edge of each entry for reordering
- Reorder disabled for ACTIVE and COMPLETED entries

**Center column — Entry detail**
Shows for selected entry:
- Project name and path
- Current state with timestamp ("Blocked 2h ago")
- Parent dependency (if set) with link to parent entry
- Child dependencies (if any)
- Skip count and reason for last skip
- Preflight validation status and last validated timestamp
- Action buttons contextual to state:
  - READY: Remove from queue, Set parent
  - ESCALATION: Awaiting escalation command (actions remain available); optional
    link to Pipeline Monitor
  - BLOCKED (roadmap): View context / monitor as implemented
  - SKIPPED_PENDING: Reset to READY (re-validates preflight first)
  - COMPLETED: Remove, View results
  - FAILED: Remove, View error

**Right column — Dependency graph**
Visual representation of parent-child relationships in the current queue.
Simple node graph — projects as nodes, dependency arrows between them.
Nodes colored by state (same color scheme as list badges).
If queue has no dependencies defined: show placeholder "No dependencies set —
drag projects to set parent relationships or use the detail panel."

### Queue controls (top bar)
- "Add Project" button — opens a modal to select project directory and
  optionally link an idea from the ideas list
- Queue mode toggle: "Auto" | "Manual" pill toggle
- "Trigger Next" button — only visible in Manual mode, disabled if a
  project is **ACTIVE** (not when entries are only **parked**)
- Queue summary: "4 projects — 2 ready, 1 blocked, 1 complete"

### Add Project modal
- Project path input with directory browser hint
- Optional: link to an existing idea (dropdown of ideas with roadmaps generated)
- Optional: set parent project (dropdown of existing queue entries)
- "Validate & Add" button — runs preflight, shows results inline before adding
- On validation pass: entry added, modal closes, list updates
- On validation fail: show each failed check with fix instructions,
  keep modal open

### Pipeline Monitor integration
Add a small queue status pill to the Pipeline Monitor header when queue
has entries:
- "Queue: 3 waiting" in neutral state
- "Queue: halted" in amber when QUEUE_HALTED
- Click pill navigates to Queue screen

---

## Testing Requirements

**Philosophy**: TDD. Write tests first, implement to pass them.
Mock all filesystem writes and orchestrator subprocess calls.

### What to mock
- `pipeline_queue.json` reads and writes — use temp directory fixture
- `pipeline_state.json` reads and writes
- `pipeline-project` symlink operations
- Preflight validation calls — return configurable pass/fail
- Orchestrator subprocess spawn

### Test coverage required

**`GET /api/queue`**
- Returns empty queue when file absent
- Returns full queue with dependency tree computed correctly
- `next_eligible` correctly identifies first READY project with no
  unresolved parent

**`POST /api/queue/add`**
- Rejects project when preflight validation fails (returns 400 with errors)
- Adds project with READY state when preflight passes
- Assigns correct sequential position
- Circular dependency detection: A→B→A returns 400

**`DELETE /api/queue/{id}`**
- Returns 409 when entry is ACTIVE
- Removes entry and resequences positions correctly

**`PATCH /api/queue/{id}/position`**
- Correctly reorders and shifts other entries
- Rejects reorder of ACTIVE entry

**`POST /api/queue/trigger-next`**
- Returns 409 when a project is **ACTIVE**
- Does **not** return 409 solely because entries are **parked** (`ESCALATION` /
  `BLOCKED`) without an `ACTIVE` row
- Triggers next READY project correctly
- Skips **preflight-failed** entries per `SKIPPED_PENDING` rules; parked entries
  are not conflated with preflight skip
- Returns QUEUE_HALTED when no eligible project remains

**`POST /api/command` (deferred commands)**
- When symlink targets another project, command is stored for the parked project
  and applied on next activation (per chosen file/entry mechanism)

**`GET /api/queue` (ingest)**
- If `pipeline_state.json` references a project not in the queue, ingest appears
  at top (or merged) without duplicates on repeated calls

**Orchestrator queue logic**
- **Park-and-advance:** on `WAITING_FOR_HUMAN` or roadmap `BLOCKED`, queue entry
  becomes `ESCALATION` or `BLOCKED`, artifacts preserved; in **auto** mode,
  `_select_next_queue_project()` runs when appropriate
- `_select_next_queue_project()` only selects `READY` / `SKIPPED_PENDING`;
  parked entries do not start until eligibility rules say so
- DEPENDENCY_HOLD correctly prevents child from becoming ACTIVE when
  parent is not COMPLETED
- QUEUE_HALTED state written when no runnable entry remains
- **Skip-and-requeue (preflight):** `SKIPPED_PENDING` moves position+1, not end
- Second preflight validation runs before each project starts
- Atomic writes: pipeline_queue.json never partially written

**`GET /api/state`**
- Includes queue summary fields when queue file exists
- Queue summary absent when queue file missing (no error)

---

## Implementation Constraints

- All `pipeline_queue.json` writes must be atomic (mkstemp + os.replace)
- Queue position is 1-indexed, always sequential, no gaps
- At most one **`ACTIVE`** entry; **`ESCALATION`** and **`BLOCKED`** are parked,
  not `ACTIVE`
- Circular dependency check must run before any parent assignment write
- The orchestrator must not block on queue operations — queue state updates
  should be fast file writes, not synchronous API calls
- `QUEUE_HALTED` is a valid `pipeline_status` value — add it to the list
  of valid states in CLAUDE.md and any state validation logic
- Queue screen drag-to-reorder must call `PATCH /api/queue/{id}/position`
  on drop, not on every drag event (avoid hammering the API during drag)
- Never auto-remove COMPLETED or FAILED entries — user must explicitly remove
- Parked rows must remain visually consistent when the global symlink targets
  another project (persisted queue fields and/or merge rules)

---

## Claude Code Instructions

**Before any implementation work (after this spec is aligned):**

1. Create a branch for implementation, e.g. `feature/queue-park-and-advance`.
2. Checkpoint commit on that branch (message at team discretion).
3. Confirm hash before writing code.

**Legacy checkpoint (historical task template):**
```
git add -A && git commit -m "pre-project-queue: checkpoint"
```

**Process:**
1. Planning phase first. Read in full:
   - autodev/pipeline/orchestrator.py (focus on PIPELINE_COMPLETE handling
     and state transition logic)
   - ui/server.py (focus on existing setup/preflight endpoints for
     validation reuse pattern)
   - ui/index.html (focus on existing screen routing and nav structure)
   Wait for plan approval before writing anything.

2. Write tests first (TDD). All tests should fail initially.

3. Implement orchestrator changes.

4. Implement server.py endpoints.

5. Implement ui/index.html queue screen and nav addition.

6. Manual verification checklist:
   - Queue screen appears in nav and routes correctly
   - Add Project modal runs preflight and rejects invalid projects
   - Valid project added appears in list with READY state
   - Drag reorder works, calls API on drop
   - Dependency assignment prevents child from starting before parent completes
   - Auto mode: pipeline moves to next project after completion
   - Manual mode: pipeline stops, Trigger Next button starts next project
   - Skip-and-requeue (preflight): SKIPPED_PENDING moves to next position
   - Park-and-advance: escalation and roadmap BLOCKED park, auto advances in auto mode
   - QUEUE_HALTED: pipeline stops when no runnable entry, status shows in monitor
   - All new tests pass: `pytest tests/ -q`
   - CLAUDE.md updated with QUEUE_HALTED as valid pipeline state

7. After verification:
```
git add -A
git commit -m "project-queue: queue screen, auto-advance, dependency model, orchestrator integration"
git push origin main
```
