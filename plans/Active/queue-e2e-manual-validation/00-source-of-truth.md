# Source of truth — queue manual validation & troubleshooting

This folder (`queue-e2e-manual-validation/`) is for **manual E2E checks** on the Pi. Use it together with the **checked-in spec and code**, not orphaned Cursor plan files.

For **strict, accountable** runs (global rules, forcing via `pipeline_queue.json` / `pipeline_state.json` / `pipeline-project`, phased checklists, no pytest-as-primary), use **[`05-STRICT-E2E-RUNBOOK.md`](05-STRICT-E2E-RUNBOOK.md)**.

## How validation is meant to be run (browser + wait + visual)

This track is **not** “run **`pytest`** and call it done.” It is **also** real **UI / visual** validation:

- **Integrated browser tools** (e.g. Cursor’s browser MCP: navigate, snapshot, click, fill) should drive the **AutoDev UI** at the real port (e.g. `http://localhost:18790`) for **Project Queue**, **Pipeline Monitor**, Setup as needed—same as a human would.
- **Wait for real outcomes.** Pipeline steps (orchestrator, agents, sentinels) can take **minutes**. Use **explicit waits** between actions: e.g. **`browser_wait_for`** with a **text** condition, or **sleep** in **short increments** (e.g. 2–5s) with **snapshot** checks in between, repeating until the expected pill/state appears—or until a generous **timeout** you document. **Do not skip a scenario** because it is “taking too long”; slowness is expected; **sleep/wait exists so you don’t skip**.
- **Visual evidence:** Capture **what the user sees**—state badges, **Queue:** … pills, **amber** halted styling, **Trigger next** enabled/disabled, dependency labels—not only **`curl`** / **`GET /api/queue`** JSON. Screenshots or snapshot excerpts belong in the report alongside API snippets.
- **`pytest`** (`tests/test_queue_api.py`, `autodev/tests/test_orchestrator_queue.py`, etc.) is a **complementary** contract layer. Manual E2E confirms **layout, timing, and integrated behavior** that tests alone do not prove.

**Legitimate skip:** Only **Skipped** when a step is **unsafe** or **impossible** in the environment (e.g. cannot safely force escalation). **Not** skipped because waiting was inconvenient.

## Primary (authoritative)

**[`TASK-03-PROJECT-QUEUE.md`](../TASK-03-PROJECT-QUEUE.md)** — single source of truth for queue semantics, including:

- **Park-and-advance:** On **`WAITING_FOR_HUMAN`** (escalation) or **roadmap `BLOCKED`**, the active queue row moves to **`ESCALATION`** or **`BLOCKED`** (not `ACTIVE`), with **`parked_at`**, **`parked_reason`** (`escalation` \| `roadmap_blocked`), **`parked_pipeline_status`**.
- **Auto vs manual:** In **`queue_mode: auto`**, after parking, **`_select_next_queue_project()`** runs (same pattern as after **`PIPELINE_COMPLETE`**). In **manual**, the orchestrator does not switch symlink until **Trigger next** (or equivalent). **`PATCH /api/queue/mode`** **manual → auto** (when the orchestrator is not holding the lock and the pipeline is not mid-agent) runs the **same start-next logic as Trigger next** once, then leaves **`queue_mode: auto`** for later in-process advances ([`ui/server.py`](../../ui/server.py) `_maybe_auto_kick_queue_after_manual_to_auto`).
- **Skip-and-requeue (preflight only):** **`SKIPPED_PENDING`** — not used for escalation/roadmap park.
- **Deferred escalation commands:** When the **symlink** points at another project, **`POST /api/command`** accepts optional **`target_project_path`** matching a parked **`ESCALATION`** row; writes **`pending_escalation_command.json`** (+ done file) under that project. The orchestrator applies it when that project becomes **ACTIVE** again (writes **`escalation_output`**, sets **`WAITING_FOR_HUMAN`** as needed).
- **Ingest (display):** **`GET /api/queue`** merges a synthetic **`ingest-<hash>`** row when **`pipeline_state.json`**’s **`project_path`** is absent from the file-backed queue (idempotent, same path → same id).
- **`QUEUE_HALTED`** and **`queue_halted_reason`** (`all_blocked` includes all parked **`ESCALATION`** + **`BLOCKED`** non-terminal rows when nothing can run).

The **“Implementation notes (park-and-advance closure)”** section at the **end** of that same file summarizes what landed in **`orchestrator.py`**, **`server.py`**, and **`ui/index.html`**.

## Secondary (ground truth in code)

| Artifact | Role |
|----------|------|
| **`autodev/pipeline/orchestrator.py`** | `_queue_park_active_entry`, `_queue_after_park_maybe_advance`, `_select_next_queue_project`, `_apply_pending_escalation_command`, roadmap **`BLOCKED`** / escalation **`WAITING_FOR_HUMAN`** branches. |
| **`ui/server.py`** | `GET /api/queue` (ingest merge + `live_pipeline_status` / `parked_pipeline_status`), `POST /api/command` (optional `target_project_path`), `POST /api/queue/trigger-next`, queue file I/O, `GET /api/queue/status`, `GET /api/state` queue summary. |
| **`ui/index.html`** | Queue screen: **`queueRowDisplay`**, pills, **Trigger next** (manual). |
| **`autodev/tests/test_orchestrator_queue.py`**, **`tests/test_queue_api.py`** | Automated contracts for parking, ingest, deferred command, counts. |
| **`CLAUDE.md`** (repo root) | Operational constants, **`VALID_STATES`** including **`QUEUE_HALTED`**, path rules. |

## Symlink and git invariants (mirror pytest + live code)

These are **required** for preflight, queue add, and orchestrator runs—not optional polish.

| Invariant | Why |
|-----------|-----|
| **`$AUTODEV_ROOT/pipeline-project`** realpath | Preflight / queue add ([`ui/server.py`](../../ui/server.py) `_run_preflight_checks`) expects the symlink to resolve to the repo under test; server may recreate the link. |
| **ACTIVE row `project_path` vs symlink** | Orchestrator [`_find_active_queue_entry`](../../autodev/pipeline/orchestrator.py) matches **ACTIVE** queue entry to **`SYMLINK_TARGET`** realpath (tests in [`autodev/tests/test_orchestrator_queue.py`](../../autodev/tests/test_orchestrator_queue.py)). |
| **`main` or `master` branch + commits** | Hollow **`mkdir .git`** only (see old **§C** anti-pattern) is **not** a real repo. `reset_phase` and related paths run **`git checkout main`** (or `master`); missing branch → checkout failure. Use **`git init`**, **`git branch -M main`**, and at least **one commit** with `roadmap.md`. |
| **Deferred command / ingest** | [`tests/test_queue_api.py`](../../tests/test_queue_api.py): symlink target = **ACTIVE** project while parked **ESCALATION** row may point elsewhere; `pipeline_state.json` **`project_path`** aligns with active work for ingest merge tests. |

**Reset helper (artifact wipe + git `main` + default roadmap “A”):** from repo root run [`scripts/queue-e2e-reset-test-projects.sh`](../../scripts/queue-e2e-reset-test-projects.sh). It backs up `pipeline_queue.json` / `pipeline_state.json`, cleans pipeline artifacts under `queue-test1`–`5`, normalizes git, and writes a single-phase `roadmap.md`. Use **`--create-phaseonly`** to add **`/home/pi/projects/queue-test-phaseonly`** for V4 preflight **warn** (phase-only branch, no `main`/`master`). For **V1** startup-complete, copy `roadmap.B.v1-complete.md` over `roadmap.md` on **queue-test1** (script installs this sidecar file).

---

## Test projects (fixed paths on this Pi)

Prerequisite repo creation is documented in **`02-prerequisite-setup.md`**. For **this** validation round, use these **existing** directories as queue entries (each must satisfy preflight: **real** git repo with **`main`**, **`roadmap*.md`**, etc.):

| Path | Suggested use |
|------|----------------|
| `/home/pi/projects/queue-test1` | First in sequence, auto-progression, baseline |
| `/home/pi/projects/queue-test2` | Second project, auto-advance after completion |
| `/home/pi/projects/queue-test3` | Third slot, ordering / skip experiments |
| `/home/pi/projects/queue-test4-child` | Child row (parent dependency with `queue-test1`–`3` or another parent) |
| `/home/pi/projects/queue-test5-esc` | Escalation / deferred-command / park-and-advance |

## Quick alignment checklist (TASK-03 + code)

| Topic | Expected behavior (summary) |
|-------|-------------------------------|
| Park on escalation | Queue **`ACTIVE` → `ESCALATION`**, parked metadata; global state **`WAITING_FOR_HUMAN`** for that project until advanced or resumed. |
| Park on roadmap blocked | Queue **`ACTIVE` → `BLOCKED`**, **`parked_reason: roadmap_blocked`**, **`blocked_at`**. |
| Auto-advance after park | **`queue_mode: auto`** → **`_select_next_queue_project()`** after parking (orchestrator main loop / startup roadmap blocked path). **Repo-init** escalation path parks but does **not** auto-advance (documented exception). |
| Deferred command | **`POST /api/command`** with **`target_project_path`** = parked project realpath, **`ESCALATION`** row; writes under **that** project, not the symlink target. |
| UI when symlink moved | **`parked_pipeline_status`** (and GET merge) keeps row labels accurate. |
| `blocked_count` (API) | Counts **`BLOCKED`** + **`ESCALATION`** (parked work). |

Use **`01-logic-gate-edge-cases.md`** for gate IDs **G1–G14**; judge **G5–G7** against **TASK-03 + implementation notes + live code**, not legacy drafts.
