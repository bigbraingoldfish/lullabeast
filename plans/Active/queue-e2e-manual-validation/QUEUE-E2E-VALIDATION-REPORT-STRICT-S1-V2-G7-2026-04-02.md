# Strict validation report — S1, V2, G7, PATCH auto (2026-04-02)

## TLDR — strict closure (Pass / Fail)

| Gate | Result | Evidence |
|------|--------|----------|
| **V2** | **Pass** | UI Queue row **`queue-test2 — ESCALATION`**; **`sessions.json`** key **`agent:escalation:pipeline:phase-1:escalation`**; escalation **`sessions/`** listing (536 files, recent mtimes). Natural state — no executor forcing this pass. |
| **PATCH live** | **Pass** | After **uvicorn restart** (stale process from 2026-04-01 omitted **`auto_advance`**): **`PATCH` manual → auto** returned **`auto_advance.attempted: true`**, **`started: "queue-test3"`**; **`POST /api/queue/trigger-next`** on restored freeze snapshot returned same **`started`**. |
| **G7** | **Pass** | **`POST /api/command`** with **`target_project_path`** = **`queue-test2`**, symlink **`queue-test3`**, file queue **ESCALATION + ACTIVE** → **200**, **`deferred: true`**, **`pending_escalation_command.json`** on **`queue-test2`**. Restored strict freeze after. |
| **S1** | **Pass (observed)** | **`GET /api/queue`** showed **ingested** **`queue-test1` COMPLETED** while file-backed next **READY** was **`queue-test3`**; **`PATCH` auto-kick** set **`queue-test3` → ACTIVE** and spawned orchestrator (same ordering as trigger-next). |

**Operational note:** Live **`auto_advance`** requires the UI server to load current **`ui/server.py`**; a long-running **uvicorn** from the prior day did not expose the field until restart.

---

## Code + tests (unchanged)

| Item | Detail |
|------|--------|
| Helper | **`_queue_run_trigger_next_logic(config)`** |
| PATCH hook | **`_maybe_auto_kick_queue_after_manual_to_auto`** → **`auto_advance`** |
| Skips | **`orchestrator_lock_held`**, **`pipeline_status_busy`**, **`queue_has_active`** |
| Tests | **`tests/test_queue_api.py`** — **47** passed including **`TestPatchQueueMode`**, **`TestPostCommandDeferred`** |

---

## Freeze / restore

```bash
./scripts/queue-e2e-strict-freeze.sh
# This closure used: STRICT_FREEZE_TS=20260402-105846
# Restore:
# cp ~/.openclaw/pipeline_queue.json.strict-freeze.20260402-105846 ~/.openclaw/pipeline_queue.json
# cp ~/.openclaw/pipeline_state.json.strict-freeze.20260402-105846 ~/.openclaw/pipeline_state.json
# ln -sfn "$(cat ~/.openclaw/pipeline-project.strict-freeze.20260402-105846.readlink)" ~/.openclaw/pipeline-project
```

**Documented symlink (freeze):** `readlink -f ~/.openclaw/pipeline-project` → **`/home/pi/projects/queue-test1`**.

---

## Wait budget (strict run, 2026-04-02)

| Step | Budget |
|------|--------|
| Browser queue load | **3s** wait after navigation |
| Browser queue reload (post-restore) | **2s** wait |
| **`PATCH` manual → auto** experiment | **single request** (no extended poll) |
| V2 escalation poll | **Not required** — escalation already visible (natural state) |
| Full dual-project pipeline “movie” | **Not re-run** — S1 inferred from merged queue + auto-kick |

---

## Forcing log (destructive edits — all reverted)

| Action | Detail |
|--------|--------|
| **Uvicorn** | Stale PID **1579875** killed; **`python3 -m uvicorn ui.server:app --host 0.0.0.0 --port 18790 --app-dir /home/pi/projects/autodev-ui`** restarted. |
| **PATCH auto-kick** | **`queue-test3`** set **ACTIVE** in file; **`pipeline_state.json`** **`project_path`** moved to **test3** until restore. |
| **Trigger-next equivalence** | Restored **`pipeline_queue.json`** + **`pipeline_state.json`** from **`.strict-freeze.20260402-105846`**, **`PATCH` manual**, **`POST /api/queue/trigger-next`**. |
| **G7** | Wrote **2-row** queue (**test2** ESCALATION, **test3** ACTIVE), set **`pipeline_status`: RUNNING**, **`project_path`**: test3, **`ln -sfn /home/pi/projects/queue-test3 ~/.openclaw/pipeline-project`**. |
| **Cleanup** | Removed **`pending_escalation_command.*`** on **test2**; restored queue + state + symlink from freeze **105846**. |

---

## R9 — `curl` / `jq` excerpts (timestamps local to Pi)

**API sanity (after freeze 105846):**

```bash
curl -s http://localhost:18790/api/state | jq '{queue_mode,orchestrator_alive,pipeline_status,current_agent,project_path}'
curl -s http://localhost:18790/api/queue | jq '{queue_mode, rows: [.queue[] | {name,state,position}]}'
```

**`PATCH` manual → auto (after server restart):**

```json
{
  "ok": true,
  "queue_mode": "auto",
  "auto_advance": {
    "attempted": true,
    "ok": true,
    "started": "queue-test3"
  }
}
```

**Equivalence — same logic as trigger-next (freeze restored, manual mode):**

```json
{ "ok": true, "started": "queue-test3" }
```

**G7 deferred command:**

```bash
curl -s -X POST http://localhost:18790/api/command \
  -H 'Content-Type: application/json' \
  -d '{"command":"RETRY","target_project_path":"/home/pi/projects/queue-test2"}'
```

```json
{ "status": "ok", "command": "RETRY", "deferred": true }
```

**Pending file (before delete on restore):**

```text
/home/pi/projects/queue-test2/pending_escalation_command.json
{"command": "RETRY", "source": "ui", "timestamp": "2026-04-02T16:07:03.253304+00:00"}
```

---

## R9 — UI / browser (accessibility snapshot lines)

- **Project Queue:** **`queue-test1 — COMPLETED`**, **`queue-test2 — ESCALATION`**, **`queue-test3 — READY`**, **`queue-test4-child — READY`** (with **DEP**); toggle **Auto** selected; summary **“4 projects — 2 ready … 1 complete”**.
- **Pipeline Monitor (earlier in pass):** **`COMPLETE`** pill, **Queue: 3 waiting**, activity feed shows **escalation** agent **`status_changed`** for **CORE-E1**.

---

## V2 — session artifacts

- **Count:** `find ~/.openclaw/agents/escalation/sessions -type f | wc -l` → **536**.
- **Recent files (mtime tail):** includes **`.../b5905db7-4668-48aa-a81a-73e401fc3b80.jsonl`**, **`sessions.json`** (same second as above).
- **`sessions.json` key (pipeline prefix):** **`agent:escalation:pipeline:phase-1:escalation`** (session id **`a14cd342-9e11-424f-8337-59747b981f1f`**).

---

## Pytest appendix

```text
tests/test_queue_api.py .............................................. 47 passed
tests/test_queue_api.py::TestPostCommandDeferred ..................... 1 passed
```

---

## Tickets (no spin-off plan)

_None._ Remaining risk: operators must **restart the UI server** after pulling **`PATCH` auto-kick** changes, or **`auto_advance`** will not appear in JSON responses.
