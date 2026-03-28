# Task 03 — Project Queue

*AutoDev MVP pre-tester work. Claude Code should read this document in full before planning.*

---

## Background

AutoDev currently handles one project at a time. When a pipeline run completes
or blocks, the next project requires manual setup. This task introduces a project
queue that allows users to line up multiple pre-validated projects, define
parent-child dependencies between them, and let the pipeline work through them
automatically — pausing only when a blocked project has no unblocked successor
to fall back to.

The queue is a separate screen from the Pipeline Monitor. The monitor shows what
is happening right now. The queue shows what is planned. These are different mental
modes and must not be combined.

---

## Core Concepts

### Queue entry states

| State | Meaning |
|-------|---------|
| `READY` | Passed preflight, waiting its turn in the queue |
| `ACTIVE` | Currently being built by the pipeline |
| `BLOCKED` | Pipeline escalated and is waiting for human input |
| `SKIPPED_PENDING` | Was blocked when its turn came; moved to next-in-line position; will be retried |
| `DEPENDENCY_HOLD` | A parent project in the queue has not completed successfully |
| `COMPLETED` | Pipeline finished successfully |
| `FAILED` | Pipeline halted with a terminal error; requires manual intervention |

### Dependency model
Projects in the queue can have a parent-child relationship. A child project
cannot become ACTIVE until its parent reaches COMPLETED state. If a parent
is BLOCKED or SKIPPED_PENDING, the child enters DEPENDENCY_HOLD and is
skipped when its position in the queue is reached. The queue continues to
the next unblocked, dependency-clear project.

### Skip-and-requeue behavior
When a project is BLOCKED at the time the pipeline would start it:
- It moves to the next position in the queue (not to the end — next)
- The following project becomes ACTIVE if it is READY and has no
  unresolved parent dependency
- When the pipeline cycles back to the skipped project, if it is still
  blocked it skips again
- If ALL projects in the queue are blocked or in DEPENDENCY_HOLD
  simultaneously: the pipeline enters a QUEUE_HALTED state, sends an
  escalation notification, and waits for human intervention

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
      "notes": ""
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

---

## Orchestrator Changes — `autodev/pipeline/orchestrator.py`

### Queue integration
On pipeline completion (`PIPELINE_COMPLETE` state), the orchestrator checks
`pipeline_queue.json`:
1. Mark the current project as `COMPLETED` in the queue
2. If `queue_mode` is `"manual"`: stop, write state, wait for user trigger
3. If `queue_mode` is `"auto"`: call `_select_next_queue_project()`

### `_select_next_queue_project()`
- Read `pipeline_queue.json`
- Walk the queue in position order
- For each project: check state is `READY` or `SKIPPED_PENDING`, check
  no unresolved parent dependency
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
shows which projects are blocked and why.

### On escalation resolution
When a human sends a resume command from the Pipeline Monitor while a
queued project is ACTIVE, the existing resume flow handles it. No queue
changes needed for this path — the queue entry stays ACTIVE until the
project reaches COMPLETED or FAILED.

---

## Server Changes — `ui/server.py`

### New endpoints

**`GET /api/queue`**
Returns full `pipeline_queue.json` content plus computed fields:
- `dependency_tree`: nested structure showing parent-child relationships
- `next_eligible`: id of the next project that would run if triggered now

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
- Returns 409 if a project is currently ACTIVE

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
  - BLOCKED: amber
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
  - BLOCKED: View in Pipeline Monitor (links to monitor screen)
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
  project is ACTIVE
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
- Returns 409 when project is ACTIVE
- Triggers next READY project correctly
- Skips BLOCKED projects and tries next
- Returns QUEUE_HALTED when all projects are blocked

**Orchestrator queue logic**
- `_select_next_queue_project()` skips blocked projects and increments
  skip_count
- DEPENDENCY_HOLD correctly prevents child from becoming ACTIVE when
  parent is not COMPLETED
- QUEUE_HALTED state written when all projects exhausted
- Skip-and-requeue: blocked project moves to position+1, not end of queue
- Second preflight validation runs before each project starts
- Atomic writes: pipeline_queue.json never partially written

**`GET /api/state`**
- Includes queue summary fields when queue file exists
- Queue summary absent when queue file missing (no error)

---

## Implementation Constraints

- All `pipeline_queue.json` writes must be atomic (mkstemp + os.replace)
- Queue position is 1-indexed, always sequential, no gaps
- Circular dependency check must run before any parent assignment write
- The orchestrator must not block on queue operations — queue state updates
  should be fast file writes, not synchronous API calls
- `QUEUE_HALTED` is a valid `pipeline_status` value — add it to the list
  of valid states in CLAUDE.md and any state validation logic
- Queue screen drag-to-reorder must call `PATCH /api/queue/{id}/position`
  on drop, not on every drag event (avoid hammering the API during drag)
- Never auto-remove COMPLETED or FAILED entries — user must explicitly remove

---

## Claude Code Instructions

**Before any changes:**
```
git add -A && git commit -m "pre-project-queue: checkpoint"
```
Confirm hash before proceeding.

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
   - Skip-and-requeue: blocked project moves to next position, not end
   - QUEUE_HALTED: pipeline stops when all blocked, status shows in monitor
   - All new tests pass: `pytest tests/ -q`
   - CLAUDE.md updated with QUEUE_HALTED as valid pipeline state

7. After verification:
```
git add -A
git commit -m "project-queue: queue screen, auto-advance, dependency model, orchestrator integration"
git push origin main
```
