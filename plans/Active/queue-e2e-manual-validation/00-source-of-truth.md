# Source of truth — queue manual validation & troubleshooting

This folder (`queue-e2e-manual-validation/`) is for **manual E2E checks** on the Pi. **Do not** treat `TASK-03-PROJECT-QUEUE.md` alone as the full specification for **escalation + queue interaction**—that document predates or only partially overlaps the finalized behavior.

## Primary (authoritative for escalation park-and-advance)

**Implementation plan — Escalation Park-and-Advance**

- **Canonical file (this machine):**  
  `/home/pi/.cursor/plans/escalation_park_and_advance_87788e9d.plan.md`
- **What it defines:** Mermaid flow; **park** after successful escalation webhook when `queue_mode == auto` and another eligible entry exists; `_queue_update_active_entry(BLOCKED, escalation_context)`; `_select_next_queue_project()`; **BLOCKED** without `pending_command` → skip-and-requeue; **BLOCKED** with `pending_command` → resumable replay; `PATCH /api/queue/{entry_id}/command`; queue UI (badge, hub); automated tests to align with.

If that path is unavailable, use the **same** content from any copy checked into the repo or from the feature’s design notes—the **plan content**, not generic queue docs, is what matters for G5–G7 and S3–S5.

## Secondary

| Artifact | Role |
|----------|------|
| **`plans/Active/TASK-03-PROJECT-QUEUE.md`** | General queue architecture, endpoints, and **post-implementation** escalation notes. Use for **breadth** (e.g. trigger-next, dependency tree). Where TASK-03 and the **escalation plan** conflict on park-and-advance, **prefer the plan** (and live code). |
| **`autodev/pipeline/orchestrator.py`** | Ground truth for `_select_next_queue_project`, escalation branch, `_write_escalation_command_files`. |
| **`ui/server.py`** | Ground truth for `PATCH /api/queue/{entry_id}/command`, `VALID_COMMANDS`, queue file I/O. |
| **`ui/index.html`** | Ground truth for CMD STAGED / staged-command UX. |
| **`tests/test_queue_escalation_park.py`**, **`tests/test_queue_api.py`** (`TestPatchQueueEntryCommand`) | Expected API/orchestrator contracts. |
| **`CLAUDE.md`** | Operational constants and repo orientation—not a substitute for the escalation plan. |

## Quick alignment checklist (plan vs wrong assumption)

| Topic | Per escalation plan (summary) |
|-------|-------------------------------|
| When to park | After **successful** escalation **webhook**, if **auto** and **at least one** non-ACTIVE eligible entry (READY / SKIPPED_PENDING / BLOCKED **with** `pending_command`—see implementation). |
| When **not** to park | **Manual** queue mode, or **no** eligible next entry → stay in **WAITING_FOR_HUMAN** on current project (legacy behavior). |
| BLOCKED, no command | **Skip-and-requeue** until something can run or **QUEUE_HALTED**. |
| BLOCKED / READY + staged command | **`PATCH /api/queue/{id}/command`** sets `pending_command`, **BLOCKED → READY**; orchestrator replays on select; UI shows staged state. |

Use **`01-logic-gate-edge-cases.md`** for IDs G1–G14; escalation-specific gates **G5–G7** must be judged against this document and the plan file, **not** against TASK-03 in isolation.
