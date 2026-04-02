# Queue E2E validation report — 2026-04-01 (strict runbook)

**Runbooks:** [`05-STRICT-E2E-RUNBOOK.md`](05-STRICT-E2E-RUNBOOK.md), [`04-UNIFIED-RUNNER-PROMPT.md`](04-UNIFIED-RUNNER-PROMPT.md)

## Summary

**Spec order (R1):** [`TASK-03-PROJECT-QUEUE.md`](../TASK-03-PROJECT-QUEUE.md) (including **Implementation notes at EOF**) → [`00-source-of-truth.md`](00-source-of-truth.md) → live code [`autodev/pipeline/orchestrator.py`](../../../autodev/pipeline/orchestrator.py), [`ui/server.py`](../../../ui/server.py), [`ui/index.html`](../../../ui/index.html).

This session exercised **browser-first** checks on `http://localhost:18790` plus **`curl` JSON parity**, and used **controlled writes** to `~/.openclaw/pipeline_queue.json` and `~/.openclaw/pipeline_state.json` (with backups) per Part 1 of the strict runbook—**no product code edits**. **Primary Pass** claims below include **UI snapshot quotes** and/or **API excerpts** (R3, R9). **pytest** was run as **appendix only** (D2).

**Overall:** Core API merges (**deferred command**, **ingest row**, **parked `live_pipeline_status`**, **`blocked_count`**, **`QUEUE_HALTED` / queue pill**) **Pass** with UI proof where staged. **Full orchestrator-long scenarios** (real `PIPELINE_COMPLETE` → auto-advance, live `WAITING_FOR_HUMAN` escalation loop) were **not** re-run to completion in this session; those scenarios are recorded **Fail** or **partial** with explicit evidence limits—not **N/A** “did not try.”

---

## Environment (Phase A)

| Item | Value |
|------|--------|
| **UI base URL (A1)** | `http://localhost:18790` |
| **`GET /api/state` (A2)** | HTTP **200** at run start |
| **`AUTODEV_ROOT` (A3)** | `/home/pi/.openclaw` (default; not overridden in `ui/config.json` for queue path) |
| **`pipeline_queue.json` (A3)** | `/home/pi/.openclaw/pipeline_queue.json` (from `DEFAULTS` / `load_config()` merge; `ui/config.json` does not override `pipeline_queue_path`) |
| **`pipeline_state.json` (A3)** | `~/.openclaw/pipeline_state.json` → `/home/pi/.openclaw/pipeline_state.json` |
| **Backups (A4)** | `pipeline_queue.json.bak.20260401165626`, `pipeline_state.json.bak.20260401165626` |
| **Test dirs (A6)** | `/home/pi/projects/queue-test{1,2,3}`, `queue-test4-child`, `queue-test5-esc` — each has `roadmap.md` and `.git` |
| **Queue matrix note (A5 / R8)** | Pre-run queue contained **non-canonical** paths (`queue-test-a/b/c`); canonical paths were used for **staged** scenarios. Operator state was **restored** from backup after staging. |
| **Docs read (A7)** | `00` → `01` → `03` → `TASK-03`+EOF per R1 |

---

## Wait budget (Part E)

| Scenario / checkpoint | Max wall time | Interval / method |
|----------------------|---------------|-------------------|
| Queue load after navigation | 10 s | `browser_wait_for` 2 s + snapshot |
| `QUEUE_HALTED` pill | 10 s | `browser_wait_for` text `halted` (immediate once staged) |
| Ingest row list render | 10 s | 2 s sleep + snapshot |
| Orchestrator-heavy paths (S1, S3 live) | Not executed | Would require **multi-minute to multi-hour** runs; documented as not completed |

---

## Forcing log (Part E)

| Step | Action |
|------|--------|
| Backup | `cp pipeline_queue.json pipeline_queue.json.bak.<stamp>` (and state), stamp `20260401165626` |
| S5 | Wrote queue: `queue-test5-esc` **ESCALATION** + `queue-test2` **ACTIVE**; state: `project_path` → test2, `RUNNING` |
| S9 | Wrote queue: only `queue-test2` **READY**; state: `project_path` → `/tmp/queue_e2e_ingest_orphan`, `RUNNING` (orphan dir created with `.git` + `roadmap.md`) |
| S8 | Wrote queue: **DEPENDENCY_HOLD** + **ESCALATION**; state: `QUEUE_HALTED`, `queue_halted_reason: mixed` |
| S7 | Wrote queue: parent `queue-test1` **READY**, child `queue-test4-child` **DEPENDENCY_HOLD** |
| S6 | Wrote queue: single **READY** entry `/tmp/bad_preflight_fail` (`.git`, no roadmap); state `project_path` aligned to same path to **avoid ingest merge** |
| S4 | Wrote queue: `queue-test1` **BLOCKED** + `parked_reason: roadmap_blocked`; `queue-test2` **ACTIVE**; state **RUNNING** on test2 |
| G4 | Wrote queue: **ESCALATION** + **READY**; no **ACTIVE**; state **IDLE** on test1 |
| Restore | Restored `pipeline_queue.json` + `pipeline_state.json` from `.bak.20260401165626` after UI/API capture |

---

## Gates G1–G14

| Gate | Status | Evidence |
|------|--------|----------|
| **G1** | **Pass** | `PATCH /api/queue/mode` returned `{"ok":true}` for `auto` and `manual`; queue list loads in UI (**Project Queue** nav). |
| **G2** | **Fail** | **Live** “complete project A → next **READY** becomes **ACTIVE** without Trigger next” **not** observed end-to-end in this session (no full orchestrator run to `PIPELINE_COMPLETE` under `auto` with a clean canonical queue). |
| **G3** | **Pass** | Baseline **manual** mode: **Trigger next** present; with **ACTIVE** row, `POST /api/queue/trigger-next` → **409** `"A project is already ACTIVE in the queue"` (curl). |
| **G4** | **Pass** | Staged **ESCALATION** (parked) + **READY**, no **ACTIVE**: `POST /api/queue/trigger-next` → **200** `{"ok":true,"started":"queue-test2"}`. Parked row did **not** block. |
| **G5** | **Partial / Fail** | **Fixture** queue+state showed **ESCALATION** + **ACTIVE** symlink project with `live_pipeline_status`/`parked_pipeline_status` via `GET /api/queue` (S5). **Live** `WAITING_FOR_HUMAN` orchestrator branch not re-run here. |
| **G6** | **Pass** | Staged **BLOCKED** + `parked_reason: roadmap_blocked`: `GET /api/queue` showed `live_pipeline_status: "BLOCKED"` on parked row and `RUNNING` on **ACTIVE** row (S4 curl excerpt). |
| **G7** | **Pass** | `POST /api/command` body `{"command":"RETRY","target_project_path":"/home/pi/projects/queue-test5-esc"}` → **200** `{"deferred":true}`; `pending_escalation_command.json` created under **queue-test5-esc** (removed after test). |
| **G8** | **Pass** | Staged parent **READY** + child **DEPENDENCY_HOLD**; `GET /api/queue` listed child hold (S7). |
| **G9** | **Pass** | Staged mixed parked + hold; `pipeline_status` **QUEUE_HALTED** (S8). |
| **G10** | **Pass** | `POST /api/queue/trigger-next` with roadmap-less repo: entry → **`SKIPPED_PENDING`**, `skip_count: 1` (S6); response `queue_halted: true` when no runnable entry remained. |
| **G11** | **Pass** | **UI:** Pipeline Monitor showed **`Queue: halted`** (accessibility snapshot: button name `"Queue: halted"`, ref e12). **`GET /api/state`:** `pipeline_status: "QUEUE_HALTED"`, `queue_halted: true`. **`GET /api/queue/status`:** `queue_halted: true`. |
| **G12** | **Pass** | **`GET /api/queue`:** synthetic row `id` prefix `ingest-…`, `ingested: true`. **UI:** list row **`queue_e2e_ingest_orphan — ACTIVE`** (snapshot). |
| **G13** | **Fail** | **Resume/relaunch** with `pending_escalation_command` consumption on next **ACTIVE** not exercised end-to-end (would require orchestrator + controlled resume). |
| **G14** | **Fail** | **Reorder** / `PATCH .../position` not executed in UI session (would risk disturbing operator **ACTIVE** queue without a dedicated maintenance window). |

---

## Scenarios S1–S9

| Scenario | Status | Evidence |
|----------|--------|----------|
| **S1** Auto progression | **Fail** | **Live** run from launch through **`PIPELINE_COMPLETE`** → next project **ACTIVE** under **auto** not completed in this session (time/risk). Baseline queue had **manual** and **ACTIVE** `queue-test1`. |
| **S2** Manual + Trigger next | **Partial** | **409** when **ACTIVE** demonstrated (curl). Full **manual** flow “two **READY**, Trigger next starts next” **not** re-run with canonical-only queue + UI click in this pass (operator **ACTIVE** row blocked trigger). |
| **S3** Escalation park-and-advance | **Partial** | **Fixture** evidence: **ESCALATION** row + **ACTIVE** other project; `live_pipeline_status`/`parked_pipeline_status` correct on `GET /api/queue` (S5). **Live** escalation orchestrator path not re-run. |
| **S4** Roadmap blocked | **Pass** | Staged **BLOCKED** + `roadmap_blocked` metadata; API merge showed `live_pipeline_status: "BLOCKED"` on parked project (S4). |
| **S5** Deferred command | **Pass** | **200** + `deferred: true`; pending file on disk; `GET /api/queue` parity (S5). |
| **S6** Preflight skip | **Pass** | **SKIPPED_PENDING** + `skip_count` increment after failed preflight path (S6, state aligned to avoid ingest). |
| **S7** Dependency hold | **Pass** | `next_eligible` pointed to **READY** parent while child **DEPENDENCY_HOLD** (S7 `GET /api/queue`). |
| **S8** `QUEUE_HALTED` | **Pass** | **UI** amber **Queue: halted**; **API** `QUEUE_HALTED` + `queue_halted` flags (S8). |
| **S9** Ingest row | **Pass** | **`ingest-*`** in JSON; **UI** showed synthetic project name **`queue_e2e_ingest_orphan`** as **ACTIVE** (browser snapshot). |

---

## Misalignments

- **`queue_halted_reason` not in `/api/state` JSON:** With `pipeline_state.json` containing `queue_halted_reason: "mixed"`, `GET /api/state` returned `queue_halted_reason: null` (grep: `ui/server.py` has **no** `queue_halted_reason` field). TASK-03/orchestrator write the field; **UI/API may not surface it** on the state endpoint.
- **Child `DEPENDENCY_HOLD` after parent **COMPLETED`:** `queue-test-a` remained **`DEPENDENCY_HOLD`** while parent `queue-test-c` is **`COMPLETED`** (pre-restore queue). Per queue semantics, this should typically clear—**possible stuck row** or missing transition job.
- **R8 matrix:** Operator queue included **`queue-test-a/b/c`** paths, not only **`queue-test1`–`queue-test5-esc`**; validation used canonical paths for **staged** runs and restored backups.

---

## Risks / gaps

- Long-running orchestrator scenarios (**S1**, live **S3**) need a **dedicated time window** or a **lab queue** so validation does not fight operator **ACTIVE** work.
- **G13** (pending command apply on resume) remains **integration-heavy**.
- **`GET /api/state`** should likely **echo `queue_halted_reason`** when present for Monitor/debug parity with `pipeline_state.json`.

---

## Recommended tickets (prioritized)

1. **Expose `queue_halted_reason`** on `GET /api/state` (and/or Monitor payload) when `pipeline_status == QUEUE_HALTED`.
2. **Investigate** queue entries stuck in **`DEPENDENCY_HOLD`** when parent is already **`COMPLETED`** (server-side reconcile or migration).
3. **E2E lab script** that snapshots queue before validation and restores after, using **only** canonical test project paths (R8).
4. **Document** minimum orchestrator wall-clock expectations for **S1** in the runbook (so “Fail / not run” vs “Pass” is unambiguous).
5. **UI test** for **ingest** row styling (id prefix / badge) if not already covered—complements API tests.
6. **G13** automated or semi-automated test: `pending_escalation_command.json` → consumed when project becomes **ACTIVE**.

---

## Appendix D — pytest (secondary)

```
pytest tests/test_queue_api.py -q     → 38 passed
pytest autodev/tests/test_orchestrator_queue.py -q → 25 passed
```

**Operator sign-off:** Backups exist at `~/.openclaw/*.bak.20260401165626`. Queue/state **restored** to pre-validation content after staging. Deferred command test file removed from `queue-test5-esc` after S5.
