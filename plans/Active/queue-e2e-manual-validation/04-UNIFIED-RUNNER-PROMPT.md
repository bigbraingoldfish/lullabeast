# One-shot prompt — queue E2E manual validation (all docs, one session)

Copy everything inside the block below into your AI assistant or runbook. It aligns the reviewer with **`00-source-of-truth.md`**, **`01-logic-gate-edge-cases.md`**, **`03-functional-validation-prompt.md`**, and **[`TASK-03-PROJECT-QUEUE.md`](../TASK-03-PROJECT-QUEUE.md)** (including implementation notes). **Do not** use **`02-prerequisite-setup.md`** in this pass—prerequisites are already satisfied.

**Stricter runbook (enforced rules, forcing strategies, accountability):** [`05-STRICT-E2E-RUNBOOK.md`](05-STRICT-E2E-RUNBOOK.md).

---

## Prompt (copy from here)

You are the **Queue E2E validation lead** for AutoDev on the Pi. Work in **one focused session**: read the docs, execute checks, produce a single report.

### Ground rules

1. **Spec priority:** [`TASK-03-PROJECT-QUEUE.md`](../TASK-03-PROJECT-QUEUE.md) and its **Implementation notes** at EOF, then [`00-source-of-truth.md`](00-source-of-truth.md), then live code [`autodev/pipeline/orchestrator.py`](../../../autodev/pipeline/orchestrator.py), [`ui/server.py`](../../../ui/server.py), [`ui/index.html`](../../../ui/index.html).
2. **Do not change product code** during validation—observe only; file gaps as tickets.
3. **Validation type:** This is **not** “run pytest and stop.” You must perform **real UI / visual E2E** using **integrated browser tools** (navigate, snapshot, click, wait). **`pytest`** is a **secondary** check if you choose to run it; the primary evidence is **what users see** (badges, pills, queue list, Monitor). Use **`browser_wait_for`** and/or **sleep in short increments** with repeated **snapshots** until the UI shows the expected state—or a **documented long timeout**. **Never skip a step because the pipeline is slow**; **wait/sleep exists so you don’t skip.** Only **Skipped** for **unsafe** or **impossible** steps—not impatience.
4. **Test projects** (use these paths for queue entries; each must pass normal preflight):

   - `/home/pi/projects/queue-test1`
   - `/home/pi/projects/queue-test2`
   - `/home/pi/projects/queue-test3`
   - `/home/pi/projects/queue-test4-child`
   - `/home/pi/projects/queue-test5-esc`

### What to read first (in order)

1. [`00-source-of-truth.md`](00-source-of-truth.md) — authoritative stack + quick checklist.
2. [`01-logic-gate-edge-cases.md`](01-logic-gate-edge-cases.md) — gates **G1–G14** (pass/fail matrix).
3. [`03-functional-validation-prompt.md`](03-functional-validation-prompt.md) — scenarios **S1–S9** with steps.

### What to execute

Run through **S1–S9** from **`03-functional-validation-prompt.md`**, mapping each to the relevant **G*** gates in **`01-logic-gate-edge-cases.md`**. Use the **queue-test*** paths above instead of generic names. For each scenario: drive **`http://localhost:18790`** (or your port) in the **browser** first; use **`curl`** / Network tab **in addition** for JSON parity, not instead of UI proof.

**Critical behaviors to confirm (TASK-03 + implementation):**

- Park-and-advance: queue **ACTIVE → ESCALATION** (escalation) or **BLOCKED** (roadmap), **`parked_*`** fields; **auto** advances via **`_select_next_queue_project()`**; **manual** does not advance past park without **Trigger next**.
- **Deferred command:** **`POST /api/command`** with **`target_project_path`** only when a **parked ESCALATION** row matches; expect **`deferred: true`** and files under the **target** project dir.
- **Ingest:** **`GET /api/queue`** shows **`ingest-*`** when global **`project_path`** is missing from the file queue.
- **QUEUE_HALTED** / **queue_halted_reason**; **blocked_count** includes **ESCALATION** + **BLOCKED** in **`/api/queue/status`** and **`GET /api/state`**.

### Deliverable (single artifact)

One markdown report with:

| Section | Content |
|---------|---------|
| **Summary** | 2–4 sentences: overall pass/fail. |
| **Gates G1–G14** | Table: Pass / Fail / N/A + one evidence line each tested. |
| **Scenarios S1–S9** | Table: Pass / Fail / Skipped + evidence (**screenshots / browser snapshots** for UI steps, plus API where relevant). |
| **Misalignments** | Bullets: expected vs seen (with file/API quotes). |
| **Risks / gaps** | Open issues for engineering. |
| **Recommended tickets** | 3–7 prioritized follow-ups. |

If something cannot be run (no GPU, unsafe escalation), mark **Skipped** and explain.

### Stop condition

You are done when every applicable **S*** scenario has a status and the **Deliverable** sections are filled. Do not expand scope to unrelated screens (Ideas, PRD) unless a queue bug forces it.

---

## Prompt (copy ends here)
