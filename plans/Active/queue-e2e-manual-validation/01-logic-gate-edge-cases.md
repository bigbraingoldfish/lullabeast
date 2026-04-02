# Queue E2E — Logic gate review (edge cases)

**Source of truth:** Read **`00-source-of-truth.md`** first (including **How validation is meant to be run** — browser E2E, **wait/sleep**, **visual** evidence; **not** pytest-only), then **[`TASK-03-PROJECT-QUEUE.md`](../TASK-03-PROJECT-QUEUE.md)** and the **Implementation notes** at the end of that file.

**Test projects (this Pi):**  
`/home/pi/projects/queue-test1` · `queue-test2` · `queue-test3` · `queue-test4-child` · `queue-test5-esc`  
(see **`00-source-of-truth.md`** for suggested roles).

Use this document to decide **what** must be true before filing bugs. Each row is a scenario: verify against **TASK-03**, **live code**, and mark **Pass / Fail / N/A / Open gap**.

| # | Scenario | What “correct” usually means | Notes |
|---|-----------|------------------------------|--------|
| G1 | Queue empty → add projects → auto mode | Entries **READY** after preflight; next steps per **`queue_mode`** | Baseline |
| G2 | Auto mode: project A completes | Next eligible **READY** / **SKIPPED_PENDING** becomes **ACTIVE**; symlink + **`pipeline_state`** update | Core auto progression |
| G3 | Manual mode: no silent start | Between projects, nothing new goes **ACTIVE** until **Trigger next** (or spawn from Setup if that’s your workflow) | Distinct from auto |
| G4 | Manual: Trigger next | **`POST /api/queue/trigger-next`**: **409** only if some row is **ACTIVE**; **ESCALATION** / **BLOCKED** parked rows **do not** block trigger | Server + UI |
| G5 | Escalation + **auto** + another eligible entry | On **`WAITING_FOR_HUMAN`**: queue row **ACTIVE → ESCALATION** + **`parked_*`**; if **auto**, **`_select_next_queue_project()`** runs; next project **RUNNING** or halted if none. **Manual** or no eligible next: stay on escalation path for current symlink (no advance). | Orchestrator escalation branch |
| G6 | Roadmap **BLOCKED** + **auto** | Queue **ACTIVE → BLOCKED** + **`roadmap_blocked`** metadata; **`blocked_at`**; then same advance rules as G5 | Planner/phase_resolver exit 2 |
| G7 | Parked **ESCALATION** + command while symlink elsewhere | **`POST /api/command`** with **`target_project_path`** = parked project → **`pending_escalation_command.*`** on disk; **200** + **`deferred: true`**. Wrong state / not **ESCALATION** → **409**. | Not the symlink-target path |
| G8 | Parent not **COMPLETED**, child **READY** | Child **DEPENDENCY_HOLD**; not started until parent **COMPLETED** | `_select_next_queue_project` + server add/parent |
| G9 | Mix of parked + dependency hold | **QUEUE_HALTED** / ordering: **`queue_halted_reason`** **`mixed`** / **`all_dependency_hold`** / **`all_blocked`** as applicable | See orchestrator halt logic |
| G10 | Preflight failure at selection | **SKIPPED_PENDING**, **skip_count**, group **skip-and-requeue** (orchestrator **and** trigger-next aligned) | Not the same as G5/G6 |
| G11 | **QUEUE_HALTED** | **`pipeline_status`** / **`pipeline_status` field** **`QUEUE_HALTED`**; Monitor amber pill; queue screen explains | Terminal queue state |
| G12 | Active project **not** in queue file | **`GET /api/queue`** shows synthetic **`ingest-*`** row at top (display merge); idempotent | Implemented; verify UX |
| G13 | Resume / relaunch parked project | User returns row to **READY** (product flow) + **Trigger next** or auto; orchestrator applies **`pending_escalation_command`** when project becomes **ACTIVE** | End-to-end with **`queue-test5-esc`** |
| G14 | Remove entry / reorder | Positions **1..n** contiguous; **ACTIVE** / **COMPLETED** reorder rules enforced | Housekeeping |

## Minimum project count

Five directories are enough for a solid matrix: **queue-test1–3** (sequence + halt), **queue-test4-child** (dependency), **queue-test5-esc** (escalation / deferred). Add more copies only if you need parallel experiments.

## References (order of precedence)

1. **`00-source-of-truth.md`**
2. **`../TASK-03-PROJECT-QUEUE.md`** (+ implementation notes at EOF)
3. **`autodev/pipeline/orchestrator.py`**, **`ui/server.py`**, **`ui/index.html`**
4. **`CLAUDE.md`**
5. This file (**G1–G14**)
