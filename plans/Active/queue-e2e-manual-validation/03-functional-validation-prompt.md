# FUNCTIONAL VALIDATION — Role prompt (after prerequisites)

**Prerequisite:** `02-prerequisite-setup.md` is complete: queue was cleared, `queue-e2e-*` dirs exist with minimal single-phase roadmaps, `GET /api/state` works.

**Source of truth for “correct” behavior:** Start with **`00-source-of-truth.md`**. For escalation parking, BLOCKED handling, staged commands, and **PATCH** `/api/queue/{id}/command`, the **Escalation Park-and-Advance** plan (`escalation_park_and_advance_87788e9d.plan.md` — path in `00-source-of-truth.md`) is authoritative. **`TASK-03-PROJECT-QUEUE.md`** is supporting material only; do **not** reject implementation solely because older TASK-03 text omitted park-and-advance.

---

## Role: Queue & progression validation lead (manual + UI + API)

**Mission:** Systematically validate behaviors against **`01-logic-gate-edge-cases.md`**, judged using the **escalation plan** + code for G5–G7 and scenarios S3–S5. Also cover auto progression (G2), manual trigger-next (G3–G4), dependency hold (G8–G9), QUEUE_HALTED (G11), open gap G12.

**Rules:**

- **Do not change application code** in this pass—only observe, record evidence, and list gaps for a follow-up implementation round.
- Before claiming a bug, read **`00-source-of-truth.md`**, the **escalation plan** (or its summary there), and **References** below—not TASK-03 alone.

---

## Before each scenario

- Note **queue_mode** (auto vs manual).
- Snapshot: **GET /api/queue** (or queue screen): order, states, `parent_id`, `pending_command`, `blocked_at`, positions.

---

## Scenarios (S1–S8)

Execute in order; **skip** any step that is unsafe or impossible in your live OpenClaw / GPU environment.

### S1 — Auto progression (G2)

- Set queue **auto**. Order queue: solo-alpha → solo-beta → solo-gamma (all READY, no deps).
- Start pipeline for the first entry (launch/resume per your setup).
- **Expect:** When the first project reaches COMPLETED (or your chosen stop point), the system advances so the **next READY** is picked up per product behavior.
- **Record:** Did the second become ACTIVE without manual **Trigger next**?

### S2 — Manual + Trigger next (G3–G4)

- Clear queue; set **manual**. Add two READY projects.
- **Expect:** The second project does **not** start until **Trigger next** (or equivalent).
- Click **Trigger next**.
- **Expect:** Next project becomes ACTIVE; orchestrator spawn succeeds (no 500).

### S3 — Escalation + park-and-advance (G5) — **per escalation plan**

- Queue **auto**; ensure another **eligible** non-ACTIVE entry exists (READY / SKIPPED_PENDING / BLOCKED with `pending_command`—see orchestrator `_has_ready` logic).
- Force escalation on the **active** project; escalation agent **webhook** must **succeed** (park logic runs only after success—see plan §2b).
- **Expect:** Active → **BLOCKED** + `blocked_at` + **`escalation_context`**; orchestrator calls **`_select_next_queue_project()`**; **next** project starts (**RUNNING** planner path) **or** if next has staged command, **WAITING_FOR_HUMAN** replay per plan.
- **Contrast:** Set queue to **manual** (or no other eligible entry) → **no** park; pipeline stays **WAITING_FOR_HUMAN** on current project (plan: branch D → L).
- **Record:** `pipeline_queue.json` + `pipeline_state.json` + UI.

### S4 — BLOCKED skip / order (G6) — **per plan “BLOCKED + no pending_command”**

- Queue with **BLOCKED** (no `pending_command`) and another eligible entry.
- **Expect:** **`_select_next_queue_project`** **skip-and-requeues** the BLOCKED group; another entry runs; **QUEUE_HALTED** only when nothing can proceed (plan + orchestrator).
- **Record:** Positions before/after (`pipeline_queue.json` or screenshot).

### S5 — Staged command (G7) — **plan Step 3 + 2c**

- **BLOCKED** entry; stage via **PATCH `/api/queue/{id}/command`** (VALID_COMMANDS) **or** queue UI.
- **Expect:** **`pending_command`**, **`pending_command_set_at`**, state **BLOCKED → READY**; UI shows staged (**CMD STAGED** / copy). When orchestrator **selects** that entry: cleanup, **`_write_escalation_command_files`**, **WAITING_FOR_HUMAN** with restored **`escalation_context`** when present.

### S6 — Dependency hold (G8–G9)

- Add **parent**, then **child**, and set **parent** dependency in UI while parent is not COMPLETED.
- **Expect:** Child **DEPENDENCY_HOLD**; it does not run until parent is COMPLETED (then eligibility updates).

### S7 — QUEUE_HALTED (G11)

- Arrange queue so no project can start (all BLOCKED / DEPENDENCY_HOLD / preflight failure as appropriate).
- **Expect:** `QUEUE_HALTED` in `pipeline_state.json` with `queue_halted_reason`; Pipeline Monitor shows the amber queue-halted pill.

### S8 — Open gap: path not in queue (G12)

- From Setup / switch-project flow (if present), point at a **valid repo path that is not** in `pipeline_queue.json`.
- **Ideal (often unimplemented):** auto-add to top of queue, preserve relative order of existing entries.
- **Record:** Actual behavior only; tag as **product gap** for engineering if behavior differs from ideal.

---

## Required output format

Deliver these sections in your review notes (markdown or ticket):

1. **Scenario table:** S1–S8, columns: **Pass / Fail / Skipped**, **evidence** (screenshot path, API JSON snippet, log line).
2. **Misalignments:** bullets — **intent** vs **observed**.
3. **Open gaps:** especially **G12**; any crash or inconsistent queue file write.
4. **Next implementation tickets:** **3–7** prioritized bullets for a follow-up sprint.

---

## References (read before calling “bug”) — **order matters**

| Order | Artifact | Why |
|-------|------------|-----|
| 1 | **`00-source-of-truth.md`** (this folder) | Declares escalation plan as primary over raw TASK-03 |
| 2 | **`escalation_park_and_advance_87788e9d.plan.md`** (path in `00-source-of-truth.md`) | Park-and-advance, BLOCKED rules, PATCH, UI |
| 3 | `autodev/pipeline/orchestrator.py` | `_select_next_queue_project`, escalation webhook branch, `_write_escalation_command_files` |
| 4 | `ui/server.py` | `PATCH /api/queue/{entry_id}/command`, `VALID_COMMANDS`, `POST /api/queue/trigger-next` |
| 5 | `plans/Active/TASK-03-PROJECT-QUEUE.md` | **Secondary** — general queue; do not override the escalation plan for G5–G7 |
| 6 | `CLAUDE.md` | Path constants, operational notes |
| 7 | `01-logic-gate-edge-cases.md` | Edge-case IDs G1–G14 |
