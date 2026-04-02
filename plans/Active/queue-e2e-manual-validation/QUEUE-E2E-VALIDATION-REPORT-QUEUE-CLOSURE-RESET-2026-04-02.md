# Queue E2E validation report — queue closure (post project reset)

**Date:** 2026-04-02  
**Scope:** Plan “Queue E2E: symlink invariants, reset test projects, edge-case roadmaps, rerun validation” **after** running [`scripts/queue-e2e-reset-test-projects.sh`](../../scripts/queue-e2e-reset-test-projects.sh) with **`--create-phaseonly`**.  
**Authority:** [05-STRICT-E2E-RUNBOOK.md](05-STRICT-E2E-RUNBOOK.md), [00-source-of-truth.md](00-source-of-truth.md) (including symlink/git invariants), queue-closure scenario definitions.

---

## Final review — TLDR

- **Reset:** Five runner repos normalized to **real `git` + `main` + commit** and roadmap **A**; **`queue-test-phaseonly`** created (**`phase/demo`** only) for V4; docs updated in **00** / **02**; backups **`pipeline_*.json.bak.202604020929`** at `~/.openclaw/`.
- **Primary evidence:** Integrated **browser** (navigate, snapshot, **`browser_wait_for`**, sleep 2–3s) on **Project Queue**, **Pipeline Monitor**, **Setup & Preflight**; **`curl`/`jq`** parity; **pytest appendix only** (102 passed).
- **Pass highlights:** **G12** ingest row in UI/API with empty file queue + `pipeline_state` path; **V4** git **warn** via **`POST /api/setup/preflight`** + browser path confirm; **V5** `queue_halted_reason` on **`GET /api/state`** + Monitor **“Queue: halted”** (forced disk state, then restored); **V3** dependent child **READY** + **DEP** + summary counts; **G4** **`POST /api/queue/trigger-next`** **200** advanced while a merged **ingest** row existed (parked **ESCALATION** visible on **queue-test2** in UI).
- **Not fully proven in this session:** **V1** (orchestrator startup with **`roadmap.B.v1-complete.md`** copied to **`roadmap.md`** on test1—not launched); **V2** webhook ordering logs; **S1** main-loop **PIPELINE_COMPLETE** auto-advance; end-to-end **G7** deferred command file on disk.
- **Artifacts:** `pipeline_state.json` restored after V5 probe from **`.pre_v5`** copy; symlink **`~/.openclaw/pipeline-project`** → **`/home/pi/projects/queue-test1`** after phaseonly preflight moved it.

---

## Summary (R1)

1. [TASK-03-PROJECT-QUEUE.md](../TASK-03-PROJECT-QUEUE.md) (+ EOF implementation notes)  
2. [00-source-of-truth.md](00-source-of-truth.md)  
3. [autodev/pipeline/orchestrator.py](../../autodev/pipeline/orchestrator.py), [ui/server.py](../../ui/server.py), [ui/index.html](../../ui/index.html)

---

## Environment (Phase A)

| Check | Result |
|-------|--------|
| **A1** | `http://localhost:18790` |
| **A2** | `GET /api/state` → **200** |
| **A3** | `AUTODEV_ROOT=/home/pi/.openclaw`; queue/state default paths |
| **A4** | Backups from reset script: **`*.bak.202604020929`** |
| **A5–A6** | `queue-test1`–`5` reset; **`queue-test-phaseonly`** present |
| **A7** | Spec order acknowledged |

**Paragraph:** Validation ran against UI on **localhost:18790** after **`queue-e2e-reset-test-projects.sh --create-phaseonly`**. Test projects under **`/home/pi/projects`** use **roadmap A** (pending **CORE-E1**) except **phaseonly** repo. **`roadmap.B.v1-complete.md`** is present on each runner for optional **V1** swap.

---

## Wait budget

| Step | Tool / interval |
|------|-----------------|
| Queue load | `sleep 3s` after navigation; optional `browser_wait_for` text |
| Monitor pill (V5) | `browser_wait_for` **halted**, timeout **10s** |
| Preflight UI | `sleep 2s` after **Confirm path** |
| Orchestrator-heavy scenarios | Not run to completion; would document **e.g. 120 × 5s** polls per 05 |

---

## Forcing log

| Action | Detail |
|--------|--------|
| Reset script | `scripts/queue-e2e-reset-test-projects.sh --create-phaseonly` |
| Queue file | Emptied to **`[]`** then **POST /api/queue/add** test2, test3, test4-child |
| Symlink | **`ln -sfn /home/pi/projects/queue-test1 ~/.openclaw/pipeline-project`** after phaseonly preflight |
| **V5** | Merged **`QUEUE_HALTED`** + **`queue_halted_reason: mixed`** into **`pipeline_state.json`**; restored from **`.pre_v5`** |
| **V4 API** | **`POST /api/setup/preflight`** body **`repo_path: /home/pi/projects/queue-test-phaseonly`** |

---

## Phase B — Scenarios (V1–V5 + S1–S3)

| ID | Status | Evidence summary (R9) |
|----|--------|------------------------|
| **V1** | **Fail** | **Roadmap B** file on disk verified **`PIPELINE_COMPLETE`** via **`phase_resolver.py`**; orchestrator **startup auto-advance** not executed |
| **V2** | **Partial / Open** | UI **ESCALATION** on **queue-test2**; no log-ordered **webhook → advance** proof |
| **V3** | **Pass** | API: child **READY** + **`parent_id`**; UI (**T14:45Z**): **“3 projects — 2 ready, … 1 dependent”**, rows **queue-test2 — ESCALATION**, **queue-test3 — READY**, **queue-test4-child — READY** with **DEP** |
| **V4** | **Pass** | API: **`git repo`** check **`status: warn`**, message contains **“No main or master branch”** and **`phase/demo`**; browser: path **`/home/pi/projects/queue-test-phaseonly`** confirmed readonly after **Confirm path** (**Run Preflight** stayed disabled without roadmap seed—full checklist deferred to API) |
| **V5** | **Pass** | API: **`{"pipeline_status":"QUEUE_HALTED","queue_halted_reason":"mixed"}`**; UI: control **“Queue: halted”**; state restored after |
| **S1** | **Fail** | No observed main-loop **PIPELINE_COMPLETE** → next **ACTIVE** |
| **S2** | **Partial** | **Manual** in UI; **Trigger Next** produced **HTTP 200** **`started: queue-test2`** (not 409—file-backed queue had no **ACTIVE** blocking) |
| **S3** | **Pass** (UI) | **ESCALATION** row visible; **auto vs manual** advance contrast not fully matrixed |

---

## Phase C — Gates G1–G14 (abbrev.)

| Gate | Status | Note |
|------|--------|------|
| G1 | **Pass** | Queue add + display |
| G2 | **Fail** | No completion advance proof |
| G3 | **Pass** | **Manual** selected |
| G4 | **Pass** | **trigger-next** **200** with ingest merge context |
| G5 | **Partial** | **ESCALATION** visible; full auto-advance not closed |
| G6–G7, G9, G13 | **Fail** / open | Not forced end-to-end |
| G8 | **Pass** | Child **READY** with non-blocking parent semantics + UI **DEP** |
| G10 | **Pass** | Pytest contracts + preflight API |
| G11 | **Pass** | Same as V5 |
| G12 | **Pass** | **`ingest-a9a847614d94`**, **`ingested: true`**, **`live_pipeline_status`** in **`GET /api/queue`** when file queue empty |
| G14 | **Open** | Reorder not exercised |

---

## Pytest appendix (D2 only)

```text
73 passed  (tests/test_queue_api.py + test_queue_dependency_edit.py + test_api_setup_preflight.py)
29 passed  (autodev/tests/test_orchestrator_queue.py)
```

---

## Deliverables checklist (plan §8)

- [x] Script: **`scripts/queue-e2e-reset-test-projects.sh`** (+ **`scripts/README.md`**)  
- [x] Doc: symlink/git invariants + script pointer in **00**; **02** hollow-`.git` warning  
- [x] This report (browser + wait + R9; pytest secondary)

---

## Operator notes

- Re-run **`./scripts/queue-e2e-reset-test-projects.sh`** anytime to re-normalize repos (creates new `*.bak.*` for queue/state).  
- For **V1**, on **queue-test1**: `cp roadmap.B.v1-complete.md roadmap.md && git commit -am 'V1 complete'` then launch orchestrator per runbook.  
- Resolve **queue-test2** **ESCALATION** via normal product flow before production use.
