# Queue E2E — Logic gate review (edge cases)

**Source of truth:** Read **`00-source-of-truth.md`** first. For **escalation, BLOCKED, park-and-advance, and staged queue commands**, the authoritative spec is the **Escalation Park-and-Advance** implementation plan (`escalation_park_and_advance_87788e9d.plan.md` on this machine—see `00-source-of-truth.md`), **not** raw `TASK-03-PROJECT-QUEUE.md` alone. Use TASK-03 as supplementary background; on conflict, **plan + code** win.

Use this document to plan **what** must be true before writing tests or running manual validation. Each row is a scenario to verify against **that plan (where applicable)**, **live code**, and mark **Pass / Fail / N/A / Open gap**.

| # | Scenario | What “correct” usually means | Notes |
|---|-----------|------------------------------|--------|
| G1 | Queue empty → add projects → auto mode | Next READY starts when orchestrator runs / auto-advance rules apply | Baseline |
| G2 | Auto mode: project A completes | Next eligible READY starts (respecting deps, preflight) | Core “auto progression” |
| G3 | Manual mode: nothing starts until Trigger next | No silent auto-start; button runs server-side trigger path | Distinct from orchestrator-only auto |
| G4 | Manual: Trigger next | Next eligible project becomes ACTIVE, orchestrator spawned as designed | Test with 2+ READY, no ACTIVE |
| G5 | Escalation + **auto** + another **eligible** non-ACTIVE entry | After successful escalation **webhook**: ACTIVE→**BLOCKED** + `escalation_context`, then `_select_next_queue_project()` starts next (park-and-advance). If **manual** or no eligible entry → **no** park; stay WFH on current project. | **Escalation plan** flowchart + Step 2b |
| G6 | **BLOCKED** + **no** `pending_command` (selection pass) | **Skip-and-requeue** (bump position); repeat → **QUEUE_HALTED** when nothing can run | Plan: “BLOCKED re-selection rules” |
| G7 | **BLOCKED** + `pending_command` **or** READY after **PATCH** `/api/queue/{id}/command` | Resumable: clean escalation files, write `escalation_output.*`, **WAITING_FOR_HUMAN** + restored context when context present; **PATCH** sets **BLOCKED→READY** + `pending_command` | Plan Steps 2c + 3 + tests |
| G8 | Parent not COMPLETED, child READY | Child **DEPENDENCY_HOLD** (or equivalent); not started until parent completes | Queue semantics |
| G9 | Parent BLOCKED, child depends on parent | Child should not run until parent resolved; after skip/requeue, order/visited behavior | Combine G6 + G8 |
| G10 | Preflight failure | SKIPPED_PENDING / skip-and-requeue (orchestrator) vs server trigger path | Two code paths |
| G11 | QUEUE_HALTED | All remaining work blocked / hold / mixed; UI shows pill | Terminal queue state |
| G12 | Switch / launch to repo path **not** in queue | *Ideal:* enqueue at top, preserve order — **likely product gap** until spec + code confirm | Document as open |
| G13 | Relaunch / orchestrator down on BLOCKED | UI relaunch path still works | From queue hub |
| G14 | Remove entry / reorder | No corruption; positions sequential | Housekeeping |

## Minimum project count

**Why more than three directories:** You need distinct paths for: two independents in sequence, a third “line” project, a parent + child pair for dependency tests, and at least one extra for rotation / halt experiments—**5–7** minimal single-phase repos is reasonable for one manual matrix without reusing paths for conflicting scenarios.

## References (order of precedence)

1. **`00-source-of-truth.md`** (this folder)
2. **Escalation Park-and-Advance plan** — `escalation_park_and_advance_87788e9d.plan.md` (see path in `00-source-of-truth.md`)
3. **`autodev/pipeline/orchestrator.py`**, **`ui/server.py`**, **`ui/index.html`** (implementation)
4. **`plans/Active/TASK-03-PROJECT-QUEUE.md`** — general queue design; use for trigger-next, dependencies, **not** as sole spec for park-and-advance
5. **`CLAUDE.md`** — ops / orientation
