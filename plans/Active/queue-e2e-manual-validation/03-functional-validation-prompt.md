# FUNCTIONAL VALIDATION — Role prompt (after prerequisites)

**Prerequisite:** `02-prerequisite-setup.md` is complete (clean queue, valid test repos, **`GET /api/state`** works).  
**You may use these pre-existing project paths for testing** (ensure each passes preflight: git + `roadmap*.md`, etc.):

- `/home/pi/projects/queue-test1`
- `/home/pi/projects/queue-test2`
- `/home/pi/projects/queue-test3`
- `/home/pi/projects/queue-test4-child`
- `/home/pi/projects/queue-test5-esc`

**Source of truth for “correct” behavior:** **`00-source-of-truth.md`** → **[`TASK-03-PROJECT-QUEUE.md`](../TASK-03-PROJECT-QUEUE.md)** (including **Implementation notes** at the bottom). Do **not** rely on superseded one-off plan filenames.

---

## Role: Queue & progression validation lead (manual + UI + API + visual)

**Mission:** Validate behaviors in **`01-logic-gate-edge-cases.md`** (G1–G14), using **TASK-03 + code** for park-and-advance, **`ESCALATION`** / **`BLOCKED`** rows, **`POST /api/command`** deferred paths, and **ingest**.

**This is not pytest-only.** Automated tests (`pytest tests/ …`, `autodev/tests/ …`) validate API/orchestrator contracts; **this** pass must also prove **real UI behavior** (see below).

**Rules:**

- **Do not change application code** in this pass—observe, record evidence, file follow-up tickets for gaps.
- Before calling something a bug, confirm against **`00-source-of-truth.md`**, TASK-03 **Implementation notes**, and the referenced **Python/HTML** files.

### Browser-first E2E + wait (mandatory methodology)

- Use **integrated browser tools** to exercise the live dashboard: open **Project Queue** and **Pipeline Monitor**, click **Add Project**, **Trigger next**, mode toggle, row selection—**end-to-end through the UI** wherever the scenario allows, not only **`curl`**.
- **Wait for real outputs.** After a click or pipeline start, the system may need **tens of seconds to many minutes**. Use **`browser_wait_for`** (text or condition) and/or **sleep in short chunks** (e.g. 2–5s) with **browser_snapshot** between chunks until the expected label/state appears—or until you hit a **documented timeout** (e.g. 10–15 minutes for a full phase—adjust to your environment). **Do not skip a step because it is slow**; **sleep/wait is the correct tool** so validation completes without skipping.
- **Visual validation:** Record **badges**, pill text (**Queue:** …, **ESCALATION**, **BLOCKED**, **QUEUE_HALTED**), colors, disabled **Trigger next**, drag handles, parent/child indicators—**screenshots or snapshot quotes** in the report.
- **`curl` / JSON** is **supplementary** evidence (parity with UI), not a replacement for “does it look right in the app?”

**Skip policy:** Mark **Skipped** only when **unsafe** or **impossible** (e.g. cannot force escalation without production risk)—**not** because waiting was tedious.

---

## Before each scenario

- Note **`queue_mode`** (**auto** vs **manual**).
- **UI:** Open **Project Queue**; note order and badges on screen.
- **API (optional parity):** **`GET /api/queue`**, **`GET /api/queue/status`**, **`GET /api/state`**: order, **`state`**, **`parent_id`**, **`parked_*`**, **`live_pipeline_status`**, positions.

---

## Scenarios (S1–S9)

Execute in order. Prefer **browser** for user-visible steps; use **Skipped** only for **safety/environment** limits—not for **timeout impatience** (use wait/sleep instead).

### S1 — Auto progression (G2)

- Set queue **auto**. Order e.g. **queue-test1 → queue-test2 → queue-test3** (all **READY**, no deps).
- Launch pipeline for the first entry (per your normal Setup / resume flow).
- **Expect:** On **PIPELINE_COMPLETE** for the first, the next **READY** becomes **ACTIVE** without **Trigger next**.
- **Record:** Second project **ACTIVE**? **`pipeline_state.json`** **`project_path`** matches symlink?

### S2 — Manual + Trigger next (G3–G4)

- Set **manual**. Two **READY** entries (e.g. **queue-test1**, **queue-test2**).
- **Expect:** Second does **not** start until **Trigger next**.
- Click **Trigger next**.
- **Expect:** Next **ACTIVE**, orchestrator spawn OK (no **500**); **409** only if some row is **ACTIVE** (parked **ESCALATION**/**BLOCKED** alone must **not** cause **409**).

### S3 — Escalation park-and-advance (G5)

- **Auto**; queue at least two eligible projects (e.g. **queue-test5-esc** then **queue-test2**).
- Force **WAITING_FOR_HUMAN** on the active project (escalation path per your pipeline).
- **Expect:** Active row → **`ESCALATION`** + **`parked_*`**; if **auto** and another entry can run, symlink advances and global state targets the **next** project (**RUNNING**).
- **Contrast — manual:** Same setup, **manual** → **no** symlink advance; parked row remains **ESCALATION** with metadata.

### S4 — Roadmap blocked park-and-advance (G6)

- Provoke roadmap **BLOCKED** (e.g. **`[!]`** phase / gate exit **2** with **BLOCKED** in output) on an **ACTIVE** queued project.
- **Expect:** Row **ACTIVE → BLOCKED**, **`parked_reason: roadmap_blocked`**, **`blocked_at`**; **auto** may advance to next eligible.

### S5 — Deferred escalation command (G7)

- With **queue-test5-esc** parked (**ESCALATION**) and **another** project **ACTIVE** (symlink points elsewhere): **`POST /api/command`** with **`{"command":"RETRY","target_project_path":"/home/pi/projects/queue-test5-esc"}`** (adjust path to realpath).
- **Expect:** **200**, **`deferred": true`**; **`pending_escalation_command.json`** under **queue-test5-esc** (not under active symlink). Sending the same command **without** **`target_project_path`** while global status is not **WAITING_FOR_HUMAN** on symlink → expect **409** (normal rule).
- When that project becomes **ACTIVE** again, orchestrator should consume pending and enter escalation handling (**WAITING_FOR_HUMAN** / sentinel path as designed).

### S6 — Preflight skip-and-requeue (G10)

- Arrange a **READY** entry that fails **full** preflight (or use trigger-next with a bad path per test design).
- **Expect:** **SKIPPED_PENDING**, **skip_count** increments, group moves forward per **TASK-03**; not confused with **ESCALATION**/**BLOCKED** parking.

### S7 — Dependency hold (G8–G9)

- Use **queue-test4-child** as child; set parent to e.g. **queue-test1** while parent is not **COMPLETED**.
- **Expect:** Child **DEPENDENCY_HOLD**; not started until parent completes.

### S8 — QUEUE_HALTED (G11)

- Arrange no runnable entry (all **DEPENDENCY_HOLD**, all parked, or repeated preflight failure as appropriate).
- **Expect:** **`QUEUE_HALTED`** in **`pipeline_state.json`**, **`queue_halted_reason`** set; Monitor + queue pills reflect halted state.

### S9 — Ingest row (G12)

- Run pipeline with **`project_path`** **not** listed in **`pipeline_queue.json`** (or temporarily remove matching entry while pipeline runs—careful).
- **Expect:** **`GET /api/queue`** includes **`ingest-*`** synthetic row for that path; repeated **GET** does not duplicate; **`live_pipeline_status`** consistent with global state when paths match; parked rows show **`parked_pipeline_status`** when symlink targets another project.

---

## Required output format

Deliver in review notes (markdown or ticket):

1. **Scenario table:** S1–S9 — **Pass / Fail / Skipped**, **evidence** (**screenshot** or browser snapshot excerpt **and** where useful **`curl`** / JSON / log line). Visual proof is required for UI-facing scenarios (S1–S5, S7–S9 especially).
2. **Misalignments:** intent vs observed.
3. **Open gaps:** crashes, non-atomic queue writes, UX confusion.
4. **Next tickets:** **3–7** prioritized follow-ups.

---

## References (read before “bug”) — order matters

| Order | Artifact |
|-------|----------|
| 1 | **`00-source-of-truth.md`** |
| 2 | **`../TASK-03-PROJECT-QUEUE.md`** + EOF implementation notes |
| 3 | **`autodev/pipeline/orchestrator.py`**, **`ui/server.py`**, **`ui/index.html`** |
| 4 | **`01-logic-gate-edge-cases.md`** (G1–G14) |
| 5 | **`CLAUDE.md`** |
