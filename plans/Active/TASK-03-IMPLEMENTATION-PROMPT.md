# Guided implementation prompt — Orchestrator ↔ queue (TASK-03)

Use this prompt together with **[TASK-03-PROJECT-QUEUE.md](TASK-03-PROJECT-QUEUE.md)** — that document is the **source of truth** for queue semantics. If code and TASK-03 disagree, **TASK-03 wins** unless you explicitly revise the doc first.

The UI server is expected at **http://localhost:18790** during live checks.

---

## CONSTRAINTS

- You **MAY** read any file on the filesystem.
- You **MAY** run `curl` (or similar) to inspect API state.
- You **MAY** run `pytest` to verify tests.
- You **MAY** edit code for this task, including **`autodev/pipeline/orchestrator.py`**, **`ui/server.py`**, and **`ui/index.html`** when required to satisfy TASK-03 (park-and-advance, deferred commands, ingest, queue enrichment, UI pills/actions).
- You **MAY** restart OpenClaw / UI servers or the orchestrator as needed for validation.
- You **SHOULD** use **browser tools** (Cursor browser MCP or equivalent) for **visual** verification of the Queue screen, badges (**ACTIVE**, **ESCALATION**, roadmap **BLOCKED**), Trigger Next, and Pipeline Monitor integration.
- During **live end-to-end** checks, use **`sleep`** (or short waits between browser snapshots) to **poll periodically** (e.g. every 10–30 seconds) while the orchestrator runs: re-check `/api/queue`, `/api/state`, and the Queue UI until behavior matches TASK-03 or a stable terminal state is reached. **Do not end the turn** until this periodic verification shows the updated behavior working as expected **or** you have documented a blocking issue with evidence (logs, API response, screenshot/snapshot).

---

## CONTEXT

`~/.openclaw/pipeline_queue.json` tracks order, dependencies, and per-entry state. The orchestrator integrates at **`PIPELINE_COMPLETE`** and related paths per TASK-03.

**TASK-03 requires (non-exhaustive):**

1. **Park-and-advance:** On **`WAITING_FOR_HUMAN`** (escalation), park the queue row as **`ESCALATION`**; on **roadmap gate `BLOCKED`**, park as **`BLOCKED`**. Preserve resume artifacts. In **`queue_mode: auto`**, advance to the next eligible project like after completion; in **`manual`**, do not auto-switch until **Trigger next** (or equivalent).
2. **Single `ACTIVE` invariant:** Parked rows are **not** `ACTIVE`. **`POST /api/queue/trigger-next`** must return **409** only when an entry is **`ACTIVE`**, not merely because rows are parked.
3. **Deferred escalation commands:** If **`POST /api/command`** targets a parked project while the symlink points elsewhere, record the command (queue entry and/or project-local file per TASK-03); apply when that project becomes active again.
4. **Ingest:** If **`pipeline_state.json`** references a project **not** in the queue (same realpath), **ingest** at top (idempotent — no duplicates).
5. **Display:** Persist **`parked_at`**, **`parked_reason`**, **`parked_pipeline_status`** (or equivalent) so parked rows do not “go blank” after the symlink advances.
6. **Preflight skip-and-requeue** remains **`SKIPPED_PENDING`** only — **do not** conflate with escalation or roadmap blocked.

**Possible legacy / hygiene gaps** (verify in code; fix if still true):

- Resume / launch paths may leave **`pipeline_queue.json`** out of sync with the process actually running.
- Auto-advance may not re-check that the queue entry for the symlink is still **`ACTIVE`** before proceeding (stale state risk).
- **`_select_next_queue_project()`** should include a **`visited_ids`** (or equivalent) guard so a single call cannot spin forever when entries repeatedly fail preflight. *(If already present, add/keep tests and move on.)*

---

## PHASE 1 — Read and report (no code changes)

Read **[TASK-03-PROJECT-QUEUE.md](TASK-03-PROJECT-QUEUE.md)** in full, then read and report with **line numbers**:

### 1. `autodev/pipeline/orchestrator.py`

- Every place the orchestrator **starts** or **continues** a run in a way that affects which project is active (including after queue selection).
- **`_select_next_queue_project()`**: full loop logic (include **`visited_ids`** if present). Note any mismatch with TASK-03 eligibility (**`READY` / `SKIPPED_PENDING`** only).
- Every **`transition_state("PIPELINE_COMPLETE")`** and following **queue** updates.
- Every path to **`WAITING_FOR_HUMAN`** / **`escalation`** agent: is there **park** → **`ESCALATION`** + optional **`_select_next_queue_project()`** per **`queue_mode`**?
- Every **`transition_state("BLOCKED")`** (roadmap): is there **park** → queue **`BLOCKED`** + **`parked_*`** fields + optional advance per TASK-03?
- Every **`transition_state("HALTED_SILENT")`**: is **`FAILED`** (or TASK-03-approved) queue update applied where appropriate?
- Any **`_queue_update_active_entry`** call sites — do they still assume **`ACTIVE`** when TASK-03 requires **`ESCALATION`** / **`BLOCKED`**?

### 2. `ui/server.py`

- **`POST /api/setup/launch`**, **`POST /api/resume-orchestrator`**, **`POST /api/queue/trigger-next`**, **`POST /api/command`**: do they read/update **`pipeline_queue.json`** per TASK-03?
- **`_spawn_orchestrator`** (or equivalent): show signature and queue side effects.
- **`GET /api/queue`**: ingest of missing active project; enrichment for **`live_pipeline_status`** vs parked fields.

Summarize **gaps vs TASK-03** before PHASE 2.

---

## PHASE 2 — Preflight loop safety (TDD)

**Goal:** `_select_next_queue_project()` terminates when every entry fails preflight (or is ineligible), with no infinite loop.

1. **Write the test first** (e.g. `tests/test_orchestrator_queue.py` or the project’s existing queue test module):
   - Queue with multiple entries, all fail **`_queue_preflight`** (mocked).
   - Assert **`_select_next_queue_project()`** returns **`False`** and results in **`QUEUE_HALTED`** (or documented behavior).
   - Assert each entry id is not processed forever (coverage of **`visited_ids`** or equivalent).
2. Run: `pytest … -k … -v` — **if implementation already passes**, keep the test as regression guard.
3. Implement or adjust only what is needed; **do not** remove **`visited_ids`** behavior if already correct.

**Commit (if you changed code or added tests):**  
`orchestrator: guard select_next_queue_project against preflight spin / regression test`

---

## PHASE 3 — Park-and-advance (orchestrator) (TDD)

**Goal:** Align orchestrator with TASK-03 **§ Park-and-advance**.

1. **Tests first** (mock filesystem / queue / symlink as in existing tests):
   - Escalation path: entry goes **`ACTIVE` → `ESCALATION`** with **`parked_*`**; artifacts preserved; **`auto`** calls next selection; **`manual`** does not start next without trigger semantics.
   - Roadmap **`BLOCKED`**: **`ACTIVE` → `BLOCKED`** (queue) with **`parked_*`**; same advance rules.
   - Deferred command file or queue field: **record** when applicable; **apply** when project becomes active again (minimal viable behavior per TASK-03).
2. Implement in **`orchestrator.py`**.
3. Run targeted then broader pytest.

**Commit:**  
`orchestrator: TASK-03 park-and-advance for escalation and roadmap blocked`

---

## PHASE 4 — Terminal halts and queue `FAILED` (TDD)

**Goal:** On terminal **`HALTED_SILENT`** (and similar failure exits), active queue entry reflects **`FAILED`** with timestamp where TASK-03 expects — **without** conflating with **`ESCALATION`** / roadmap **`BLOCKED`**.

1. Enumerate **`HALTED_SILENT`** (and related) transitions from PHASE 1.
2. **Tests first**, then implement **`_queue_update_active_entry("FAILED", …)`** (or TASK-03 fields) at each required site.
3. pytest.

**Commit:**  
`orchestrator: queue FAILED on terminal halts per TASK-03`

---

## PHASE 5 — Server and API (TDD)

**Goal:** **`ui/server.py`** matches TASK-03 for:

- **`trigger-next`**: **409** only if **`ACTIVE`**; parked **`ESCALATION`** / **`BLOCKED`** do not block alone.
- **`POST /api/command`**: deferred storage when symlink targets another project.
- **`GET /api/queue`**: ingest + **`live` / parked** enrichment.
- Launch / resume: queue stays consistent when orchestrator is spawned for a path that appears in the queue.

**Tests:** extend **`tests/test_queue_api.py`** (and related) **before** implementation.

**Commit:**  
`server: TASK-03 queue API — trigger-next, deferred command, ingest`

---

## PHASE 6 — UI (`ui/index.html`) (TDD where possible + browser)

**Goal:** Queue list and action hub match TASK-03: **ESCALATION** vs roadmap **BLOCKED**, “Awaiting escalation command” when appropriate, Trigger Next disabled only for **`ACTIVE`**, no blank parked rows when another project is symlink-active.

- Prefer **component-level** or API-driven checks in tests where the repo already patterns them; otherwise rely on PHASE 7 browser verification.

**Commit:**  
`ui: TASK-03 queue display and actions for parked entries`

---

## PHASE 7 — Live verification (browser + `sleep` + curl)

**Do not skip.** Use a **loop** until stable or blocked:

1. **curl:** `GET /api/queue`, `GET /api/state` — counts and states match TASK-03 expectations for your test scenario.
2. **Browser:** open Queue screen; confirm badges and actions; navigate to Pipeline Monitor if needed.
3. **`sleep N`** then **re-repeat** (e.g. 2–3 times over a minute or two if orchestrator is running) to catch **async** state updates.
4. Record pass/fail with concrete evidence.

**Final suite:**

```bash
pytest tests/ -q
pytest autodev/tests/ -q   # if present
```

**Final commit (if anything remains):**  
`orchestrator-queue: TASK-03 integration verification`

Push per your branch policy (`main` only if intended).

---

## Notes for the implementing agent

- If **`visited_ids`** is **already** implemented in **`_select_next_queue_project()`**, treat PHASE 2 as **verification + regression test**, not a blind paste of old instructions.
- **Roadmap `BLOCKED`** (pipeline status) maps to queue state **`BLOCKED`** (parked); **human escalation** maps to queue **`ESCALATION`** — do not use one for the other.
- When in doubt, re-read **[TASK-03-PROJECT-QUEUE.md](TASK-03-PROJECT-QUEUE.md)** § Core Concepts and § Orchestrator Changes.
