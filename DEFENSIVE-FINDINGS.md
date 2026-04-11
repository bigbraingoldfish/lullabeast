# DEFENSIVE-FINDINGS.md

**Single source of truth** for defensive review outcomes.  
**Check off work in the table below first** — use **Status**: `open` → `in-progress` → `done`. Sync the same status on the matching row in the category sections when you update here (or vice versa; keep them aligned).  
Supersedes detailed notes in `DEFENSIVE_REVIEW_FINDINGS(Cursor Auto).md` and `defensive_review_findings.md` (those files now point here only).

---

## Consolidated summary action list

Ordered execution list (crash/corruption first, then API, then clarity, then remaining items, then backlog). One row per finding ID.

| # | Status | ID | Tier | Action (summary) | Quick fix / notes |
|---|--------|----|------|------------------|-------------------|
| 1 | done | **C4-01** | T1 crash | Lock liveness probe: open lock file **read-only** (`ui/server.py` ~904-926). | Change `open(lock_path, 'w')` to `open(lock_path, 'a')`. Use `'a'` not `'r'` -- `'r'` raises `FileNotFoundError` if file doesn't exist yet. `flock` call unchanged. One-liner. |
| 2 | done | **C3-05** | T1 crash | UI launch / switch-project: write `pipeline_state.json` **after** spawn succeeds, or **rollback** on spawn failure (~6147-6158, ~6388-6395). | Move `_write_json_atomic(pipeline_state_path, ...)` to after `spawned["ok"]` check. On `not ok` branch, delete state file if just written. Two call sites (launch + switch-project). Note: `spawned["ok"]` only means Popen didn't raise, not that child is healthy -- accept that limit. |
| 3 | done | **C6-01** | T1 crash | Corrupt `pipeline_queue.json`: **quarantine** + halt -- never overwrite with empty in-memory queue (`_read_queue` ~338-347). | In `except (json.JSONDecodeError, OSError)`: rename file to `pipeline_queue.json.corrupt.<timestamp>`, then raise or return a sentinel. Never fall through to `_write_queue({})`. Callers that ignore the return value today need a guard added. |
| 4 | done | **C7-01** | T1 crash | Wrap `ui/config.json` `json.load` -- clear operator error on `JSONDecodeError` (`load_config` ~491-495). | Wrap in `try/except json.JSONDecodeError as e`: log path + error + line number, raise `RuntimeError`. Every endpoint 500s with a message pointing at the config file -- better than a cryptic traceback. |
| 5 | done | **C3-02** | T1 crash | Queue advance: confirm **`update_symlink` success** before committing ACTIVE + `write_state`; rollback on failure (~500-518). *(Covers **C6-04**.)* | Fix ordering: symlink first -> queue ACTIVE -> `write_state`. On symlink `False`: reset row to `READY`/`BLOCKED`, call `_write_queue`, return without calling `write_state` for new project. Currently the order is reversed. |
| 6 | done | **C3-03** | T1 crash | CLI `apply_cli_project_path`: honor `update_symlink` return; rollback or non-zero exit (~2877-2878). | Same pattern as C3-02. If `update_symlink` returns `False`, do not commit `write_state` for the new path. Log and return early. |
| 7 | done | **C3-07** | T1 crash | `executor_gate`: if `phase_base_commit` missing, **fail closed** -- do not skip deletion check (~121-123). | Replace skip path with `exit(1)` + JSON: `{"error": "ERR_MISSING_BASE_COMMIT", "detail": "cannot verify deletions"}`. This is the only automated guard against MiniMax deleting files. Never weaken it. |
| 8 | done | **C1-05** | T1 crash | Orchestrator: fail closed on corrupt `pipeline_state.json` / `phase_state.json` (~272-283, ~876-884). **Moved from BL -- same crash class as C6-01.** | On parse failure: quarantine file (rename + `.corrupt`), `sys.exit(1)` with message pointing operator to bad file. Continuing with in-memory defaults risks duplicate phase work or wrong agent routing. Fix is identical in pattern to C6-01. |
| 9 | done | **C2-01** | T2 API | `webhook_client`: validate response body if contract exists; else document enqueue-only semantics (~27-44). | **Doc-only** -- OpenClaw returns 200 on enqueue, no meaningful body. Add comment in `webhook_client.py`: "enqueue-only; 2xx = queued, not executed." Add JSON body check later only if the API contract adds one. |
| 10 | done | **C2-02** | T2 API | Ideas flows: check HTTP status before polling (reuse `_post_agent_webhook` pattern); multiple `session.post` sites. | For each bare `await session.post(...)` without a status check: add `resp.raise_for_status()` before the poll loop. The existing `_post_agent_webhook` helper already does this -- route these sites through it. At least 3 locations affected. |
| 11 | done | **C2-03** | T2 API | Readiness webhook: `raise_for_status` or equivalent; persist error for UI (~2912-2919). | In `_trigger_readiness_assessment`: after status log, add `if resp.status >= 400: write error sentinel to idea dir; return`. Persisting a structured error file lets UI surface it on next poll instead of staying silently stale. |
| 12 | done | **C6-06** | T3 clarity | `trigger-next` / queue halt: distinguish **empty queue** from blocked / dependency-hold (~5304). | At top of `_queue_run_trigger_next_logic`: `if not queue_entries: return {"ok": False, "reason": "queue_empty"}`. One-liner guard. UI shows "No items in queue" vs "Queue halted". |
| 13 | done | **C6-05** | T3 clarity | Deleted / missing queue path: record **explicit reason** on row (not silent `SKIPPED_PENDING`) (~477-495). | When setting `state = "SKIPPED_PENDING"` for missing path: also write `"skip_reason": "path_not_found"` onto the entry dict before `_write_queue`. UI can then show a tooltip or badge explaining why. |
| 14 | done | **C7-02** | T3 clarity | Orchestrator: fail fast when `openclaw.json` missing or unusable (~201-210). | After `load_config()` at startup, check required keys (`hooks_url`, `hooks_token`). If missing: print structured error + `sys.exit(1)`. Prevents a silent run that hits `AUTH_ERROR` many minutes later. |
| 15 | done | **C7-03** | T3 clarity | Skill mapping: surface **config-health** when YAML missing / disabled. | Already logs `[SKILL] Status=none_mapped`. Improve by writing a `skill_health.json` to `AUTODEV_ROOT` at startup so UI or operator can check it without reading logs. No fail-fast unless skills are strictly required. |
| 16 | done | **C7-04** | T3 clarity | Validate `AUTODEV_ROOT` / workspace paths at startup or preflight. | Add `_validate_autodev_root(root)` at orchestrator startup: check dir exists, `workspace-{planner,executor,reviewer}/` exist, `openclaw.json` present. Print per-missing-item error + `sys.exit(1)`. |
| 17 | done | **C3-01** | T4 | `write_state`: re-raise or return bool after failed atomic write (~287-302). | Change `except OSError: log + return` to `log + raise`. All `transition_state()` callers handle exceptions or propagate up the main loop. Re-raise is cleaner than a silent return that leaves caller assuming disk == memory. |
| 18 | open | **C4-02** | T4 | `_queue_preflight`: wrap `os.listdir` in try/OSError -> failed preflight (~364-381). | Wrap `os.listdir(project_path)` in `try/except OSError`: return `{"ok": False, "reason": "path_unreadable"}`. One try block, no logic change. |
| 19 | open | **C4-03** | T4 | `SkillManager._clean_workspace_skills`: fail closed if clean fails. | On `rmtree` failure: do not proceed to copy. Return early from `inject_skill` with `Status=clean_failed`. Stale skill content is worse than no skill. Optional: one retry with a short sleep before giving up. |
| 20 | open | **C4-04** | T4 | Harden legacy `autodev_repo_path` default (`DEFAULTS` / `CLAUDE.md`). | Fix `DEFAULTS["autodev_repo_path"]` fallback from `~/.openclaw` to `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` (repo root -- two levels up from `ui/`). Update `CLAUDE.md` Unresolved Items #3 to mark resolved. |
| 21 | open | **C4-05** | T4 | `phase_init` / `phase_resolver`: remove `./` fallback -- fail with explicit path error (~26-29, ~82-108). | Replace `project_path = project_path or "./"` with `if not project_path: sys.exit(1)` + message. CWD fallback silently writes metadata to the wrong tree in an orchestrator-driven subprocess. |
| 22 | open | **C6-02** | T4 | Document single-writer assumption for file queue. | **Doc-only** (decided): add comment block in `_read_queue` / `_write_queue` stating "single-writer assumed; UI writes only at human-initiated moments." Add note to `CLAUDE.md`. No lock/version field needed at this risk level. |
| 23 | open | **C6-03** | T4 | Align orchestrator preflight with server preflight or label gap in UI. | Shortest path: add `roadmap_path_valid` check to `_queue_preflight`. For full alignment, extract server symlink + gitignore checks into a shared util. At minimum, show "lightweight preflight only" note on queue entry cards. |
| 24 | open | **C2-05** | T4 | `wait_for_model_stable`: document "proceed on timeout" (~809-810). | **Doc-only**: add inline comment on the `return False` path explaining it's intentional. If blocking is ever required, return a distinct sentinel so the caller decides -- don't change behavior now. |
| 25 | open | **C2-04** | T4 | `check_traffic_cop_health`: confirm intentional silent `False` (~764-769). | Add `logger.debug("traffic_cop_health check failed: %s", e)` in except. Intentional design confirmed -- `False` = "treat as unhealthy" is the correct safe behavior. Add docstring note. |
| 26 | open | **C3-06** | T4 | `metrics.jsonl` rewrite: use temp file + `os.replace` (~2254-2284). **Moved from BL -- 5-line quick win.** | Replace `open(metrics_path, 'w')` rewrite with: write to `metrics_path + ".tmp"`, then `os.replace`. Five-line change. Do alongside any other `orchestrator.py` T4 touch. |
| 27 | open | **C5-04** | T4 | Blame L1: malformed analyst JSON -> infra/unknown, not default `impl` (~1335-1372). **Moved from BL -- direct retry cost.** | In the analyst JSON parse fallback branch: route to `"infra"` or `"unknown"` and escalate. Misrouting a broken infra phase as implementation wastes a full executor retry. One-line change in the fallback. |
| 28 | open | **C3-04** | T4 check | **Verification only:** `mkstemp` + `os.replace` pattern is correct -- no code change. | Audit any new state-writing functions in future PRs for this pattern. Make it a code review checklist item. |
| 29 | open | **C1-01** | BL | SSE stream: optional close on repeated errors (~358-361). | Add `consecutive_errors` counter; after N errors emit `{"event": "stream_error"}` and close. Client reconnects via `EventSource` auto-reconnect. Low urgency. |
| 30 | open | **C1-02** | BL | Readiness background task: persist last webhook error on idea dir (~2889-2924). | Write `readiness_error.json` in idea directory on webhook failure. Overlaps with C2-03 -- implement together. |
| 31 | open | **C1-03** | BL | Gate `utils`: do not silently pass on `phase_state` read failure (~41-42, 77-78). | Replace `except Exception: pass` with `except Exception as e: logger.warning(...)`. Losing prior retry fields silently can misroute blame attribution. |
| 32 | open | **C1-04** | BL | `_phase_resolver_indicates_pipeline_complete`: distinguish error vs not complete (~330-332). | Return 3-value or raise: `True` (complete) / `False` (not complete) / raise (resolver error). Caller should halt or escalate on error, not silently treat as "not complete." Less urgent now that C1-05 hardens state reads upstream. |
| 33 | open | **C1-06** | BL | Blame path: structured codes when context reads fail (~1289+, ~1434+, ~1476+). | Return `{"error": "ERR_CONTEXT_UNREADABLE"}` dict. Blame routing should treat unreadable context as "unknown" / escalate -- never silently default to `impl`. |
| 34 | open | **C5-01** | BL | Planner gate `load_json_safe`: by design -- doc-only. | Add docstring: "Returns FAIL on missing/malformed JSON -- retry loop handles this." No code change. |
| 35 | open | **C5-02** | BL | Ideas clarity JSON: optional schema validation before return. | Validate `clarity_result.json` has at least `{"questions": [...]}` before returning 200. Missing key causes confusing 500 downstream; pre-flight gives a clear 422. |
| 36 | open | **C5-03** | BL | Adversarial extractors: treat empty / malformed report explicitly (~4280+). | Return `{"confidence": "unknown", "reason": "parse_failed"}` on empty/error instead of benign defaults. UI or notification shows "unknown" instead of a fabricated confidence level. |
| 37 | open | **C5-05** | BL | `_parse_agent_response`: minimal schema + parse warning (~2553-2644). | Check for required top-level keys (`response`, `questions`, `assumptions`). On mismatch: log `WARNING: unexpected model shape` and continue -- don't hard fail, just surface the gap. |
| 38 | open | **P8** | BL | Phase 8 browser: re-run / expand with full agent round-trips. | Schedule when a full pipeline run is available. Focus on: queue -> trigger -> RUNNING -> WAITING_FOR_SENTINEL transitions in UI, and "Queue halted" amber pill (C6-06). |

---

## Attack surface (10 lines)

1. **`autodev/pipeline/orchestrator.py`** — main loop, state, queue advance, git, gates, webhook invoke.  
2. **`autodev/pipeline/webhook_client.py`** — sync `requests` to OpenClaw hook (only in-repo HTTP for agent wake).  
3. **`autodev/pipeline/sentinel_poller.py`** — idle + sentinel completion.  
4. **`autodev/pipeline/skill_manager.py`** — skill inject / YAML / filesystem.  
5. **`autodev/pipeline/gate_scripts/*.py`** — deterministic validation, JSON reads, atomic `phase_state`.  
6. **`ui/server.py`** — FastAPI, queue API, ideas flows, many `aiohttp` webhooks.  
7. **`ui/server.py` `load_config` / `_finalize_autodev_config_paths`** — path merge and env overrides.  
8. **`autodev/pipeline/heartbeat_cron.py`** — lock + health probes.  
9. **Atomic writers** — `mkstemp` + `os.replace` for `pipeline_state.json`, `pipeline_queue.json`, several UI JSON writes.  
10. **OpenRouter** — not called directly here; failure modes are mostly OpenClaw + in-repo webhook/poll paths.

---

## Subsystem heatmap (risk density)

| Subsystem | Density | Note |
|-----------|---------|------|
| `ui/server.py` (Ideas webhooks) | High | Several fire-and-forget `session.post` without status/body checks. |
| `orchestrator.py` (queue + state read) | Medium | Symlink result ignored after queue ACTIVE; corrupt state file leaves defaults. |
| `webhook_client.py` | Low–Med | 2xx always success; no JSON body validation (may be OK for enqueue-only API). |
| Gate scripts + `utils.py` | Low | Explicit FAIL paths; silent `except` on phase_state read loses fields. |

---

## Recommended fix order (planning only — do not treat as committed scope)

**Authoritative order and status:** [Consolidated summary action list](#consolidated-summary-action-list) (top of this file). This tier table is a compact map of themes → IDs.

| Tier | Theme | IDs (see tables) |
|------|--------|-------------------|
| **1 — Crash / corruption** | Small diffs, high risk if left open; parallelizable | **C4-01** lock probe, **C3-05** UI state before spawn, **C6-01** corrupt queue quarantine, **C7-01** `load_config` JSON parse, **C3-02** symlink after queue ACTIVE + **C3-07** executor deletion guard fail-closed |
| **2 — API correctness** | Normalize webhook handling | **C2-01**–**C2-03** (validate before sentinel / status on Ideas paths) |
| **3 — Observability / operator clarity** | Empty vs blocked queue, missing path reason, startup validation | **C6-06**, **C6-05**, **C7-02**–**C7-04** |
| **Backlog (post-launch)** | Lower blast radius or intentional resilience | **C1-*** swallowed exceptions (edge), **C5-*** model/UI parsing, **C3-06** metrics.jsonl atomic rewrite |

---

## Findings by category

### 1 — Swallowed exceptions / continue-as-success

| ID | Status | Where | Failure mode | User symptom | Likelihood | Fix / pattern |
|----|--------|-------|--------------|--------------|------------|---------------|
| **C1-01** | open | `ui/server.py` ~358–361 | SSE `_event_generator`: `except Exception` logs `Stream error` and sleeps 1s; stream continues. | Client may see gap / missed events; not a silent API success. | edge | Intentional resilience; optional: metric or close stream on repeated errors. |
| **C1-02** | open | `ui/server.py` ~2889–2924 `_trigger_readiness_assessment` | Logs webhook failure; does not surface to caller (background task). | Readiness stays `unavailable` / stale; operator must read logs. | edge | Documented fire-and-forget; optional: persist last webhook error on idea dir. |
| **C1-03** | open | `autodev/pipeline/gate_scripts/utils.py` ~41–42, 77–78 | `record_error_code_only` / `update_phase_state_error`: `except Exception` when reading `phase_state.json` → `pass`, then rewrite with partial state. | Prior retries / fields dropped; gate still runs. | rare | Log warning + preserve empty dict only if read fails; or bail with distinct error code. |
| **C1-04** | open | `autodev/pipeline/orchestrator.py` ~330–332 | `_phase_resolver_indicates_pipeline_complete`: any exception → `False` (not “unknown”). | Pipeline may not treat roadmap as complete when resolver errored. | edge | Distinguish “error” vs “not complete” (separate return or log + metric). |
| **C1-05** | done | `autodev/pipeline/orchestrator.py` ~272–283, ~876–884 | Invalid `pipeline_state.json` or `phase_state.json` caught; run continues with in-memory defaults. | Silent bad output / wrong resume | edge | Fail closed; quarantine bad file; require manual recovery. |
| **C1-06** | open | `autodev/pipeline/orchestrator.py` ~1289–1294, ~1434–1448, ~1476–1523 | Failure reading failure context, reviewer output, or gate detail swallowed; blame routing with thin evidence. | Misrouted retries / escalation | edge | Structured error code; safest fallback instead of silent continue. |

*Deduped:* skill workspace clean WARN + proceed overlaps **C4-03** — one row kept there.

### 2 — API failure paths (in-repo HTTP)

| ID | Status | Where | Failure mode | User symptom | Likelihood | Fix / pattern |
|----|--------|-------|--------------|--------------|------------|---------------|
| **C2-01** | done | `autodev/pipeline/webhook_client.py` ~27–44 | `timeout=15`; 429/5xx retried; **any 2xx** after `raise_for_status` → `SUCCESS`. Body not inspected. | False success if gateway returns 200 without enqueueing work. | rare | If API documents JSON body, validate; else document enqueue-only semantics. |
| **C2-02** | done | `ui/server.py` ~3654–3655, ~4254–4255, ~4380–4381 (and similar) | `await session.post(...)` no `raise_for_status` / status read; polling follows. | 401/503 → poll timeout → 504/408 masking root cause. | edge | Reuse `_post_agent_webhook` (~2394–2410): check status before poll. |
| **C2-03** | done | `ui/server.py` ~2912–2919 `_trigger_readiness_assessment` | Logs `resp.status` only; no `raise_for_status`; exceptions swallowed in outer `except`. | Same as C2-02 for readiness. | edge | Check `resp.status`; on failure set sentinel or structured error for UI. |
| **C2-04** | open | `autodev/pipeline/orchestrator.py` ~764–769 `check_traffic_cop_health` | `RequestException` → `False` (silent). | Caller treats unhealthy without detail — usually OK. | common | Intentional; keep. |
| **C2-05** | open | `autodev/pipeline/orchestrator.py` ~809–810 `wait_for_model_stable` | Timeout → proceeds anyway (`return False` after warn). | Reviewer may run while GPU still swapping models. | edge | Document or block reviewer until stable when policy requires. |

### 3 — Partial state writes / crash windows

| ID | Status | Where | Failure mode | User symptom | Likelihood | Fix / pattern |
|----|--------|-------|--------------|--------------|------------|---------------|
| **C3-01** | done | `autodev/pipeline/orchestrator.py` `write_state` ~287–302 | On write exception: logs, removes temp; **does not re-raise**. Caller may assume state on disk matches memory. | Restart reads old state; duplicate work or wrong phase. | rare | Raise after log, or return bool and force callers to abort transition. |
| **C3-02** | done | `autodev/pipeline/orchestrator.py` `_select_next_queue_project` ~500–518 | Queue row set **ACTIVE** and `_write_queue` **before** `update_symlink`; symlink failure only printed. | Queue + `pipeline_state` say new project; agents still read old symlink. | edge | If `update_symlink` returns `False`, revert queue row or mark BLOCKED; do not `write_state` for new project. |
| **C3-03** | done | `autodev/pipeline/orchestrator.py` `apply_cli_project_path` ~2877–2878 | `write_state` then `update_symlink` — return value ignored. | Same class as C3-02 for CLI path. | edge | Check return value; rollback or exit non-zero. |
| **C3-04** | open | Core paths | `pipeline_state.json`, `pipeline_queue.json`, gate `phase_state` use **mkstemp + os.replace** | Good — crash mid-write leaves old file. | — | Keep pattern; extend to new multi-file protocols. |
| **C3-05** | done | `ui/server.py` ~6147–6158, ~6388–6395 | `pipeline_state.json` written **before** `_spawn_orchestrator()` succeeds. Failed spawn leaves disk `RUNNING` with no process. | Misleading state, broken recovery | edge | Write after spawn succeeds, or rollback state on spawn failure. |
| **C3-06** | open | `autodev/pipeline/orchestrator.py` ~2254–2284 | Canonical `metrics.jsonl` opened with `"w"` and rewritten in place; crash mid-write truncates history. | Missing / partial metrics | edge | Temp file + `os.replace()` (backlog-friendly). |
| **C3-07** | done | `autodev/pipeline/gate_scripts/executor_gate.py` ~121–123 | If `phase_base_commit` missing, unaccounted-deletion check **skipped**; guard is no-op when state is corrupt. | Deletion guard ineffective | edge | Fail closed; surface recovery error. |

**Note:** Executor “JSON before `.done`” race is handled in `classify_executor_outcome` — documented behavior, not listed as silent corruption.

### 4 — Filesystem assumptions

| ID | Status | Where | Failure mode | User symptom | Likelihood | Fix / pattern |
|----|--------|-------|--------------|--------------|------------|---------------|
| **C4-01** | done | `ui/server.py` ~904–926 `_check_orchestrator_liveness` | Opens lock file with `'w'` → **truncates** PID/timestamp metadata on every health probe. | Heartbeat / recovery loses lock diagnostics | common | Open **read-only** or non-truncating fd for probe. |
| **C4-02** | open | `autodev/pipeline/orchestrator.py` `_queue_preflight` ~364–381 | `os.listdir` without try; broken dir can raise. | Crash during queue selection | rare | try/OSError → preflight fail. |
| **C4-03** | open | `autodev/pipeline/skill_manager.py` `_clean_workspace_skills` | `rmtree` failure logged WARN; injection may proceed into dirty tree. | Stale skill content | rare | Fail closed or retry clean before copy. |
| **C4-04** | open | `DEFAULTS` / `CLAUDE.md` | Legacy `autodev_repo_path` when env unset pointed at wrong tree; `_finalize_autodev_config_paths` mitigates repo-local `.autodev`. | Wrong orchestrator or runtime root if misconfigured | edge | Startup self-check (partially covered in tests). |
| **C4-05** | open | `autodev/pipeline/gate_scripts/phase_init.py` ~26–29, `phase_resolver.py` ~82–108 | Derived runtime project path missing → fall back to `./`; metadata may write under CWD. | Wrong tree written | rare | Fail fast with clear path error. |

### 5 — Model / agent output assumptions

| ID | Status | Where | Failure mode | User symptom | Likelihood | Fix / pattern |
|----|--------|-------|--------------|--------------|------------|---------------|
| **C5-01** | open | `planner_gate.py` + `load_json_safe` | Missing file / bad JSON → `FAIL` + error codes in `phase_state`. | Retry loop; visible | common | By design. |
| **C5-02** | open | `ui/server.py` post-ideas clarity / adversarial | Malformed `clarity_result.json` → HTTP 500 after `.done`. | User-visible error (good). | edge | Optional: validate schema before return. |
| **C5-03** | open | `ui/server.py` `_extract_adversarial_confidence` (~4280+) | Heuristics on empty/malformed report → benign defaults in notification. | Weak copy in Signal/UI | edge | Treat empty report as failure or “unknown confidence”. |
| **C5-04** | open | `autodev/pipeline/orchestrator.py` ~1335–1372 blame L1 | Malformed / truncated analyst JSON falls through; defaults toward **impl**. | Infra misrouted as implementation | edge | Treat malformed analyst output as infra/unknown; escalate. |
| **C5-05** | open | `ui/server.py` ~2553–2644 `_parse_agent_response` | Permissive parse; unexpected model shape becomes prose; questions/assumptions may drop. | Incomplete Ideas UI prompts | edge | Minimal schema + parse warning when violated. |

### 6 — Queue edge cases

| ID | Status | Where | Failure mode | User symptom | Likelihood | Fix / pattern |
|----|--------|-------|--------------|--------------|------------|---------------|
| **C6-01** | done | `autodev/pipeline/orchestrator.py` `_read_queue` ~338–347 | Corrupt `pipeline_queue.json` → empty in-memory queue; next `_write_queue` can **overwrite** file with empty structure. | Queue data loss | rare | On JSON error: quarantine file + halt; do not write back blindly. |
| **C6-02** | open | `ui/server.py` queue APIs + `orchestrator._read_queue` | Mid-pipeline add: race between server writes and orchestrator selection on file-based queue. | Duplicate activation or skipped row | edge | Document single-writer expectation; optional lock/version field. |
| **C6-03** | open | `_queue_preflight` vs server preflight | Orchestrator preflight lighter than UI; entry can advance then fail full checks. | “Started” in UI, fails later | common | Align preflight or label “orchestrator preflight only” in UI. |
| **C6-04** | open | Tied to **C3-02** | Deleted project path + symlink failure after queue ACTIVE. | Agents wrong project + queue mismatch | edge | Fix C3-02. |
| **C6-05** | done | `autodev/pipeline/orchestrator.py` ~477–495 | Manually deleted queue path → `SKIPPED_PENDING` + reposition; **no explicit reason** for operator. | Item “vanishes” without cause | edge | Record missing-path / preflight reason on row. |
| **C6-06** | done | `ui/server.py` ~5304 `_queue_run_trigger_next_logic` | Empty queue returns same generic “halted” message as blocked / dependency-hold cases. | Cannot distinguish empty vs blocked | common | Dedicated empty-queue reason or status code. |

### 7 — Configuration edge cases

| ID | Status | Where | Failure mode | User symptom | Likelihood | Fix / pattern |
|----|--------|-------|--------------|--------------|------------|---------------|
| **C7-01** | done | `ui/server.py` `load_config` ~491–495 | `json.load` on `config.json` **uncaught** — invalid JSON crashes any endpoint using `load_config()`. | FastAPI 500 everywhere | rare | try/except `JSONDecodeError` → clear operator-facing error. |
| **C7-02** | done | `autodev/pipeline/orchestrator.py` `load_config` ~201–210 | Missing or bad `openclaw.json` → `{}`. Downstream assumes keys may exist. | Empty webhook token → `AUTH_ERROR`; skills off | edge | Fail fast at orchestrator startup if required keys missing. |
| **C7-03** | done | `autodev/pipeline/skill_manager.py` | Missing YAML / PyYAML / mapping / file: graceful degradation + `[SKILL]` logs. | Phases run without injected skill | common (misconfig) | Intentional per design; optional: config-health surfacing. |
| **C7-04** | done | `AUTODEV_ROOT` missing / wrong | `os.makedirs` / symlink targets may fail with ERROR prints only. | Stuck pipeline or wrong workspace | edge | Startup validation or orchestrator preflight. |

---

## Phase 8 — Browser verification (record)

Tracker row: **#38 (P8)** in [Consolidated summary action list](#consolidated-summary-action-list) — keep **Status** in sync.

| Status | Notes |
|--------|--------|
| open | Navigated `http://127.0.0.1:18790/` — dashboard OK. Queue tab: **Loading…** then after wait, row **calculator — WAITING**, **Trigger Next** disabled. Async UI needs wait between snapshot and assertion. Full webhook round-trips not exercised (multi-minute caps). |

---

## Priority fix list

Superseded by **[Consolidated summary action list](#consolidated-summary-action-list)** — use that table as the working checklist (rows 1–7 = former Tier-1 bullets).

---

<!-- SESSION LOG 2026-04-10 (session 2) -->
## Session log — 2026-04-10 (session 2)

**Completed:** C2-01, C2-02, C2-03, C6-06, C6-05, C7-02, C7-03, C7-04

**In-progress:** none

**Skipped:** none

**Regressions found:**
- `tests/test_api_ideas_adversarial_check.py::test_returns_200_with_adversarial_report` fails in full suite but passes in isolation — same pre-existing test-ordering issue noted in session 1. Unrelated to this session's changes.
- C2-02 status check (`resp.status >= 400`) broke 4 existing test files whose mock factories used `MagicMock()` without setting `status` as an integer. Fixed all affected test files by adding `mock_response.status = 200` to their mock factories (`test_api_ideas_adversarial_check.py`, `test_api_ideas_alignment_check.py`, `test_api_ideas_convert.py`, `test_api_ideas_convert_updated.py`).
- C7-02 (fail-fast on missing openclaw.json keys) broke 3 tests in `test_orchestrator_queue.py` that used `{}` as openclaw.json. Fixed by writing the minimal valid config `{"hooks_url": ..., "hooks_token": ...}` in those fixtures.
- C7-04 (`_validate_autodev_root`) broke 3 tests in `test_orchestrator_queue.py` that called `Orchestrator()` without workspace dirs. Fixed by adding `workspace-{planner,executor,reviewer}/` mkdir in those fixtures.

**Notes for next session:**
- Findings #17–38 remain open. Next batch starts at C3-01 (T4, write_state re-raise).
- C7-03 `skill_health.json` is written atomically (mkstemp + os.replace) to AUTODEV_ROOT. Tests use the real AUTODEV_ROOT path from the environment, so the health file location is `$AUTODEV_ROOT/skill_health.json`.
- The pre-existing adversarial test ordering issue is a real bug in the test suite (test isolation) but is out of scope for this defensive pass.

<!-- SESSION LOG 2026-04-10 -->
## Session log — 2026-04-10

**Completed:** C4-01, C3-05, C6-01, C7-01, C3-02, C3-03, C3-07, C1-05

**In-progress:** none

**Skipped:** none

**Regressions found:**
- `tests/test_api_ideas_adversarial_check.py::test_adversarial_report_stored_in_session_json` fails in full suite but passes in isolation — pre-existing test-ordering issue, unrelated to session changes.
- `autodev/tests/test_done_file_logic.py::test_executor_success_state_preserved_when_reviewer_fails` failed after C3-07 fix because the test had no `phase_base_commit` in its gate fixture. Fixed by updating `_make_executor_gate_patch` to write a mock `pipeline_state.json` with a sentinel commit hash.

**Notes for next session:**
- Findings #9–38 remain open. Next batch starts at C2-01 (T2 API, doc-only).
- The pre-existing adversarial test ordering issue should be investigated separately (not introduced by this session).
- C3-02 rollback: on symlink failure the queue row stays in its current state (READY/SKIPPED_PENDING) and `_select_next_queue_project` returns `False`. The orchestrator does NOT set the row to BLOCKED — that would require knowing the failure is permanent. Operator should investigate the symlink failure reason before re-running.
- C1-05 note: `read_phase_state` raises `RuntimeError` on corrupt file (propagates to main loop → pipeline halts). `read_state` calls `sys.exit(1)` directly (startup-time fail-fast). Both quarantine the corrupt file to `.corrupt.<timestamp>`.

