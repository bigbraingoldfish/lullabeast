# PIPELINE-SPEC.md — Architecture Specification

> **Purpose:** Single source of truth for *what* the system is and *how each component behaves*.
> Organized by component, not by flow position.
> **Audience:** AI coding agents implementing the system.

---

## 1. System Overview

The pipeline is an autonomous multi-agent software development system. A Python orchestrator running on a Raspberry Pi 5 drives four LLM agents (planner, executor, reviewer, escalation) through deterministic gate scripts, producing tested code phase-by-phase against a project roadmap. Agents are invoked via OpenClaw webhook POST — not spawned as subagents. All agents share a project workspace via a stable symlink. Human notifications go exclusively via Signal.

### Component Inventory

| Component | Type | Runs On |
|---|---|---|
| `orchestrator.py` | Pure Python script | Raspberry Pi 5 |
| OpenClaw gateway | Agent gateway | Raspberry Pi 5 |
| Planner agent | LLM (cloud — OpenRouter) | OpenRouter → MiniMax M2.7 |
| Executor agent | LLM (cloud — OpenRouter) | OpenRouter → Kimi K2.6 (Moonshot AI) |
| Reviewer agent | LLM (cloud — OpenRouter) | OpenRouter → Kimi K2.6 (Moonshot AI) |
| Escalation agent | LLM (local) | Main machine via llama-server (Qwen3.6-27B, port 11434) |
| Gate scripts (×4) | Deterministic Python | Raspberry Pi 5 |

### Infrastructure Topology

```
Raspberry Pi 5                         Main Machine (Windows 11 + RTX 4090)
┌─────────────────────┐                ┌─────────────────────────────────────┐
│ orchestrator.py     │                │ llama-server :11434                 │
│ OpenClaw gateway    │──local reqs──▶ │   Qwen3.6-27B (escalation only)     │
│   :18789            │                │                                     │
│ Gate scripts        │                └─────────────────────────────────────┘
└────────┬────────────┘
         │ cloud reqs (planner, executor, reviewer)
         ▼
   OpenRouter API → MiniMax M2.7 (planner) / Kimi K2.6 (executor, reviewer)
```

- Local model requests (escalation only) go directly to llama-server at `http://<llama-server-host>:11434`
- Cloud requests route via OpenRouter: planner → MiniMax M2.7 (`openrouter/minimax/minimax-m2.7`), executor & reviewer → Kimi K2.6 (`openrouter/moonshotai/kimi-k2.6`)
- OpenClaw webhook endpoint: `POST http://localhost:18789/hooks/agent` — requires `Authorization: Bearer <token>` header. Token source: `hooks.token` in `~/.openclaw/openclaw.json`. Do not use query string auth (`?token=...` returns 400).

### Agent LLM Configuration

Current production configuration (live values per `~/.openclaw/openclaw.json`; snapshot 2026-05-29):

| Agent | Model | Provider | Inference | Config Source |
|---|---|---|---|---|
| Planner | MiniMax M2.7 (`openrouter/minimax/minimax-m2.7`) | OpenRouter | Cloud | `openclaw.json` → `agents.list[]` for `planner` (`model.primary`) |
| Executor | Kimi K2.6 / Moonshot AI (`openrouter/moonshotai/kimi-k2.6`) | OpenRouter | Cloud | `openclaw.json` → `agents.list[]` for `executor` |
| Reviewer | Kimi K2.6 / Moonshot AI (`openrouter/moonshotai/kimi-k2.6`) | OpenRouter | Cloud | `openclaw.json` → `agents.list[]` for `reviewer` |
| Escalation | Qwen3.6-27B (`llama-local/qwen3.6-27b`) | llama-local (llama-server :11434) | Local | `openclaw.json` per-agent |

(`prd-creator` and `roadmap-converter` also run `openrouter/moonshotai/kimi-k2.6`.)

> **Webhook model field:** The orchestrator does **not** send a `model` field on `POST /hooks/agent` for planner, executor, or reviewer. OpenClaw therefore uses each agent’s configured model from `openclaw.json` (same semantics as the Ideas / prd-creator webhook path). To change inference for pipeline agents, edit `agents.list[].model` in `openclaw.json` and restart the gateway if needed so new sessions pick up changes (see also § Session model is baked at creation time in operator docs).

OpenRouter is the inference provider for all cloud agents. An OpenRouter API key is required in the `openrouter` provider entry in `openclaw.json`.

#### Configuration History / Original Intent

This pipeline was originally designed as local-first, using llama.cpp on a local RTX 4090 for all agent inference. After smoke testing confirmed unacceptable latency for local planner/executor/reviewer inference and prohibitive cloud API costs via direct provider access, MiniMax M2.5 via OpenRouter was adopted for those three agents. The escalation agent remains on local inference. The local inference infrastructure is preserved and can be restored by updating the agent LLM config.

---

## 2. Orchestrator (`orchestrator.py`)

### Identity

Pure Python script running on Raspberry Pi 5. Not an LLM. Not an OpenClaw agent. Not using subagents.

### Event Loop

```
[startup] temp-file cleanup → repo init check → read state → check gate → POST webhook → poll for sentinel → repeat
```

**Startup temp-file cleanup:** On every startup, after acquiring the lock and before `run_repo_init_check()`, the orchestrator calls `cleanup_stranded_temp_files(~/.openclaw/)`. This globs `~/.openclaw/` (and the resolved `pipeline-project/` real path) for files matching `pipeline_state_????????`, `phase_state_????????`, and `current_phase_????????` — the 8-character hex suffix produced by `tempfile.mkstemp()` when an atomic write was interrupted by a prior crash. Any matches are deleted and logged at INFO level. Canonical filenames (`pipeline_state.json`, etc.) are never matched by the `????????` wildcard.

The stop sentinel check runs at the **top of every main loop iteration** via `_check_stop_requested()`, and also **inside every sentinel polling loop** (planner, executor, reviewer). If the sentinel file `pipeline_stop_requested` exists in the project directory, it is consumed (deleted) and the orchestrator writes `pipeline_status: STOPPED` atomically, logs `[STOP] Stop sentinel detected — halting pipeline cleanly`, and breaks out of the main loop. This check at the top of the main loop runs before any gate evaluation or webhook dispatch in that iteration. The intra-poll check ensures that a stop request issued while an agent is being waited on takes effect within 2 seconds rather than waiting for the full 600s timeout to expire.

```python
def _check_stop_requested(self) -> bool:
    stop_file = os.path.join(SYMLINK_TARGET, "pipeline_stop_requested")
    if os.path.exists(stop_file):
        try:
            os.remove(stop_file)   # consume — prevents re-trigger on restart
        except OSError:
            pass
        return True
    return False
```

The stop sentinel is created by the UI server `POST /api/stop` endpoint (see §14 > UI Server API Reference). The sentinel is consumed on first detection so a subsequent orchestrator restart does not re-halt.

The stop sentinel file is written to SYMLINK_TARGET (the pipeline project directory on disk), not to RAM or a temp location. This means the sentinel survives a Pi reboot or orchestrator crash. On cold restart, `_check_stop_requested()` runs on the first loop iteration before any webhook is fired — if the sentinel is present, the orchestrator writes `STOPPED` and exits cleanly without launching any agent turns. Operators should be aware that a stop request issued before a reboot will be honored on the next orchestrator start.

The repo init check runs once per startup (before the phase loop) via `run_repo_init_check()` in `orchestrator.py`. On failure it invokes escalation immediately and returns — the phase loop never starts. See §13 > Repo Initialization Check.

The orchestrator polls for output files via a simple `time.sleep()` polling loop — does not use OpenClaw session polling. **Do not use inotify or third-party file watchers** to avoid Linux symlink/inode detachment issues.

**All webhook POSTs require auth** — header: `Authorization: Bearer <token>`. Token source: `~/.openclaw/openclaw.json` → `hooks.token`. Load at orchestrator startup, not hardcoded. A POST without the header will silently fail to route.

### State Machine

`pipeline_state.json` tracks the current pipeline state. Valid states:

| State | Meaning | Heartbeat Action |
|---|---|---|
| `RUNNING` | Orchestrator is actively processing | No action needed |
| `WAITING_FOR_SENTINEL` | Agent invoked, polling for `.done` file | If elapsed > timeout → trigger recovery |
| `WAITING_FOR_HUMAN` | Escalation sent, awaiting Signal reply | Do nothing — correctly paused |
| `HALTED_SILENT` | Escalation delivery failed (all fallbacks exhausted) | Do nothing — same as WAITING_FOR_HUMAN |
| `BLOCKED` | Roadmap phase marked `[!]` | Pipeline halts entirely; manual unblock required |
| `PIPELINE_COMPLETE` | All roadmap phases complete (`[x]`); written instead of `HALTED_SILENT` at clean completion | Do nothing — terminal state; heartbeat treats same as `WAITING_FOR_HUMAN` |
| `STOPPED` | Operator-initiated clean halt via stop sentinel; written by orchestrator after `_check_stop_requested()` consumes `pipeline_stop_requested` | Do nothing — terminal halt; heartbeat treats same as `WAITING_FOR_HUMAN` |

> ⚠️ AMBIGUITY: `RUNNING` is inferred as logically necessary; the source document explicitly names the other four states but does not name an active-processing state.
> **Note:** `HALTED_SILENT` is written **only** when escalation delivery fails (all Signal and raw-webhook fallbacks exhausted). It is **not** used for clean pipeline completion. `PIPELINE_COMPLETE` is the correct terminal state for clean completion. Any description of `HALTED_SILENT` that includes "pipeline fully complete" as a trigger is incorrect.
> **Note:** `STOPPED` is written **only** when the operator requests a clean halt via the UI stop button (which writes the `pipeline_stop_requested` sentinel file). It is distinct from `HALTED_SILENT` (which is a delivery-failure fallback) and from `PIPELINE_COMPLETE` (which is the natural completion state). `STOPPED` terminates the orchestrator loop without escalation.

### `pipeline_state.json` Contents

```json
{
  "current_phase": "<int>",
  "current_phase_raw_id": "<string — full phase ID e.g. 'CORE-2'; avoids int-suffix collisions>",
  "current_agent": "<planner|executor|reviewer|escalation>",
  "planner_retries": "<int>",
  "executor_retries": "<int — per-segment budget; resets on reviewer ROUTE_EXECUTOR rejection>",
  "executor_self_failure_retries": "<int — P0 Stage H lifetime accumulator; never reset on rejection/escalation>",
  "executor_reviewer_rejection_retries": "<int — P0 Stage H lifetime accumulator; mirror of self_failure_retries>",
  "reviewer_retries": "<int>",
  "last_action": "<string — description of last webhook/gate action>",
  "last_action_timestamp": "<ISO 8601>",
  "pipeline_status": "<RUNNING|WAITING_FOR_SENTINEL|WAITING_FOR_HUMAN|HALTED_SILENT|BLOCKED|PIPELINE_COMPLETE|STOPPED|QUEUE_HALTED — the VALID_STATES enum; transition_state() raises on any other value. 'IDLE' is NOT a member: it is an external-only reset status written directly to this file by the UI/tooling, never a transition_state() target>",
  "project_path": "<string — absolute path to project directory; stored so heartbeat cron can re-pass it on restart (B4)>",
  "phase_base_commit": "<string — git SHA of HEAD before current phase branch was created; used by reset_phase() to rewind (B6)>"
}
```

### Atomic Write Requirement

Every state transition is written to `pipeline_state.json` atomically **before** the action it records — write-then-act, never act-then-write. This enables crash recovery: the heartbeat cron can resume from the last committed state.

### Lockfile

- File: `pipeline.lock`
- Locking mechanism: **`fcntl.flock` (POSIX advisory lock)**, not raw PID checking
- Orchestrator acquires an exclusive lock (`fcntl.flock(fd, LOCK_EX | LOCK_NB)`) on start; holds the file descriptor open for the lifetime of the process
- On clean exit: file descriptor closes, OS releases lock automatically
- On crash or reboot: OS drops the lock automatically — no stale lock possible
- Heartbeat cron tests liveness by attempting `fcntl.flock(fd, LOCK_EX | LOCK_NB)`:
  - If lock acquisition **fails** (`EWOULDBLOCK`) → orchestrator is alive, check `pipeline_state.json` for stuck state
  - If lock acquisition **succeeds** → orchestrator is dead, heartbeat takes ownership and resumes from last committed state in `pipeline_state.json`
- The lock file also contains PID + timestamp as metadata (for logging and diagnostics), but these are **not used for liveness detection**

> ⚠️ Do NOT use raw PID checks for liveness. After a Pi reboot, the OS resets the PID counter and an unrelated process (e.g., a system service) may be assigned the orchestrator's old PID. A PID-alive check would falsely report the orchestrator as running, causing a silent permanent halt. `fcntl.flock` is immune to this because the OS drops the lock on process death or reboot.

### What the Orchestrator is NOT

- Not an LLM orchestrator — zero model calls during gate evaluation (exception: blame attribution LLM fallback; see § Gate Scripts > Blame Attribution)
- Not using `sessions_spawn` subagent pattern — unreliable (non-blocking spawn + LLM must maintain poll loop)
- Not routing via messaging channels — webhook only for agent invocation; Signal used exclusively for human escalation notifications

---

## 3. Planner Agent

### Model

`openrouter/minimax/minimax-m2.7` (MiniMax M2.7, cloud via OpenRouter)

### Invocation Contract

```
POST /hooks/agent
  agentId: "planner"
  sessionKey: "pipeline:phase-N:{raw_id}:planner-attempt-1"
  wakeMode: "now"
```

> ⚠️ AMBIGUITY: The HTML uses `pipeline:phase-N` in the planner flow diagram but the session management section uses `pipeline:phase-N:{raw_id}:planner-attempt-X`. The session management section is the authoritative reference for session key patterns. Use `pipeline:phase-N:planner-attempt-X`.

### Input Files

- `current_phase.json` — phase detail, category, exit_criteria
- `phase_state.json` — retry count, prior failures
- Retained context: AGENTS.md, SOUL.md, session history

### Shared Workspace

`~/.openclaw/pipeline-project` (symlink) — all agents read from same directory; no file copying.

### Output Schema — `planner_output.json`

```json
{
  "implementation_plan": {
    "type": "array",
    "items": { "type": "string" },
    "minItems": 1,
    "description": "Explicit task list for the executor"
  },
  "tdd_test_structure": {
    "type": "array",
    "items": { "type": "string" },
    "minItems": 1,
    "description": "Test cases defined upfront before implementation"
  },
  "pass_criteria": {
    "type": "array",
    "items": {
      "type": "object",
      "properties": {
        "condition": { "type": "string", "description": "Explicit, verifiable condition" }
      },
      "required": ["condition"]
    },
    "minItems": 1,
    "description": "Verifiable conditions that prove the phase is complete"
  }
}
```

All three fields are **required**.

### Sentinel

`planner_output.done` — written by the agent as its final act, after `planner_output.json`.

### Retry Behavior

On gate failure: re-invoke planner with failure detail appended to context. Max 3 retries. On retry exhaustion → escalation agent.

### Crash-Recovery Skip (RR-2)

When the orchestrator restarts mid-phase with `current_agent=planner` and `planner_retries=0`, it MUST check whether valid planner output already exists on disk before invoking the webhook. The guard conditions:

```
IF planner_retries == 0
   AND planner_output_preserved == True   (written to phase_state.json after gate pass)
   AND planner_output_is_valid() == True  (sentinel + gate re-check on disk)
THEN  skip planner invocation → advance current_agent to executor
```

**`planner_output_preserved` flag lifecycle:**
- Written atomically to `phase_state.json` BEFORE `transition_state` after planner gate passes (crash-safe ordering)
- Cleared explicitly in ROUTE_PLANNER branch (reviewer's blame-plan rejection) — prevents skip from firing on intentional re-runs where the previous plan was rejected
- Cleared by `reset_phase()` — new phase starts with no preserved output
- Never touched by `reset_execution()` — executor failure does NOT invalidate planner output

**Why the flag is necessary:** Without it, both crash-recovery AND ROUTE_PLANNER result in `planner_retries=0` with stale output files on disk. The flag makes the states distinguishable:
- Crash-recovery: flag is `True` (was set on prior gate pass, not cleared)
- ROUTE_PLANNER: flag is `False` (cleared explicitly before routing back to planner)

---

## 4. Executor Agent

### Model

`openrouter/moonshotai/kimi-k2.6` (Kimi K2.6 / Moonshot AI, cloud via OpenRouter)

### Invocation Contract

```
POST /hooks/agent
  agentId: "executor"
  sessionKey: "pipeline:phase-N:{raw_id}:executor-attempt-X"
```

### Input Files

- `planner_output.json` — read directly from shared workspace, no copy step
- Task message: initial phase task only — fresh session each attempt, no failure history injected

### Executor Terminal State Classification (RR-3)

After the sentinel polling window closes, the orchestrator classifies the executor's terminal state before taking any retry action:

| State | Sentinel (`.done`) | Output JSON | Action |
|---|---|---|---|
| `executor_succeeded` | ✓ present | ✓ present | Run executor gate → if pass: advance to reviewer; if fail: `reset_execution("auto")` |
| `executor_preempted` | ✗ absent | ✓ present | Run executor gate → if pass: advance to reviewer (no `executor_retries` consumed); if fail: route to escalation (no `executor_retries` consumed) |
| `executor_crashed` | ✗ absent | ✗ absent | `reset_execution("auto")` — increments `executor_retries` |

**Preemption rule:** `executor_preempted` occurs when the executor session was interrupted (model swap, OpenClaw restart, etc.) but had already written `executor_output.json`. Since the executor did not fail — it was interrupted — the gate gets one chance to validate the output. If it passes, the pipeline advances normally. If it fails, the failure is escalated rather than burned against `executor_retries`, which is reserved for genuine executor failures.

### Two Retry Scenarios

**Self-failure retry** (executor output failed the gate, or it timed out / crashed / sentinel never appeared):
- Fresh session, fresh attempt key — no prior-attempt history loaded (prevents context overflow)
- Working tree **PRESERVED** so the executor iterates on its prior work; the hard `git reset --hard HEAD` runs **only** on an `ERR_UNACCOUNTED_DELETION` failure (to restore deleted files) — Phase 2
- On a gate failure, `failure_context.json` is preserved and tagged `source: "gate"` with a concise `retry_guidance` note plus the specific detail (`gate_error_codes`, the executor's `agent_failure_reason`, `tests_passing`, `gate_failure_detail`); the fresh session reads it and makes a targeted fix — symmetric with the reviewer-rejection path below

**Reviewer-rejection** (executor completed, reviewer rejected):
- Generate new attempt key dynamically (e.g., `pipeline:phase-N:executor-attempt-X`)
- Inject ONLY the original planner output and the reviewer `blocking_issues` into the prompt
- Fresh session context — do NOT load previous attempt's history (prevents VRAM overflow)
- Leave working tree exactly as-is — executor's files are the right starting point

### Tool Execution (OpenClaw Runtime Layer)

The executor generates tool calls; OpenClaw executes them:
- File reads/writes from LLM tool call JSON
- Shell commands: `npm test`, `pytest`, `cargo test`, etc.
- Feeds stdout/stderr back into LLM context for next loop iteration
- Tests run with minimal verbosity flags (`pytest -q` etc.) — full verbose output on final confirmation pass only

### Output Schema — `executor_output.json`

```json
{
  "status": {
    "type": "string",
    "enum": ["complete", "failed", "stuck"],
    "description": "Executor self-reported completion state. 'stuck' = hit tool-call hard stop mid-implementation. GATE-CHECKED."
  },
  "tests_written": {
    "type": "array",
    "items": { "type": "string" },
    "description": "Paths to test files written, matching tdd_test_structure from planner. GATE-CHECKED."
  },
  "test_results": {
    "type": "object",
    "properties": {
      "all_passing": { "type": "boolean" }
    },
    "required": ["all_passing"],
    "description": "Parsed from test runner exit code. GATE-CHECKED."
  },
  "file_manifest": {
    "type": "array",
    "items": { "type": "string" },
    "description": "All expected files that must exist on disk. GATE-CHECKED."
  },
  "lint_passing": {
    "type": "boolean",
    "description": "Lint check result. Not gate-checked in v1."
  },
  "failure_reason": {
    "type": "string",
    "description": "Explanation of failure if status != complete. Must include raw stderr, tracebacks, or specific compiler/interpreter error names (e.g., AttributeError, TypeError). Not gate-checked — context for reviewer/escalation/blame."
  },
  "troubleshooting_attempts": {
    "type": "array",
    "items": { "type": "string" },
    "description": "What the executor tried before giving up. Not gate-checked — context for reviewer/escalation."
  },
  "files_deleted": {
    "type": "array",
    "items": { "type": "string" },
    "description": "Optional. Pre-existing files intentionally removed during this phase. Files created and deleted within the same phase do not need to be listed. Any file present at phase_base_commit that is absent from both file_manifest and files_deleted triggers ERR_UNACCOUNTED_DELETION at the gate. GATE-CHECKED."
  },
  "lessons_appended": {
    "type": "boolean",
    "description": "Whether executor wrote to lessons.md. Not gate-checked."
  }
}
```

Fields marked `GATE-CHECKED` are validated by the executor output gate (see § Gate Scripts > Executor Output Gate). Remaining fields are written by the executor per AGENTS.md contract and available as context for reviewer and escalation agents.

### Sentinel

`executor_output.done` — written after `executor_output.json`.

### Reachability Advisory (P1 Stage F)

After every FAIL-returning blocking check in `executor_gate.py` has passed, the gate runs a static reachability check from `verification.entry_point.command`. Manifest files that cannot be reached are surfaced as one summarising `reachability_warning` pipeline event per phase; the gate exit code is unchanged (PASS). The check is **advisory only** — no `ERR_*` code is ever emitted; the phase always advances.

**Scoping — COMPLETE phases only.** The check is gated on `current_phase.raw_id.startswith("COMPLETE-")`. Reachability-from-entry is a *whole-artifact* property: orphaned code matters at the end of a build, not mid-stream. The roadmap pattern is explicitly add-then-wire (e.g. `DATA-E1` adds a localStorage utility; `DATA-E2` wires it in a later phase). Running per phase would flag the DATA-E1 utility as unreachable at the moment it lands — a false positive on the most common build pattern, exactly the cry-wolf trust erosion that the advisory posture is meant to avoid. The executor gate is the convenient seam, not the right granularity; the COMPLETE-prefix gate makes it the right one.

**Channel separation.** Advisory output lives in `executor_advisory_detail.json`, a separate artifact from the FAIL-channel `executor_gate_detail.json`. The orchestrator's `_emit_reachability_advisory(raw_id)` drains the advisory file on the executor PASS path and emits events; the file is then removed. The two channels never co-tenant. See §4.5 for the full pattern.

**Hedged copy.** Operators must not read "unreachable" as "dead code." The summary reads: *"N file(s) not reached from entry — a.py, b.py, c.py. Confirm intent: orphan vs. wiring landed elsewhere."* This phrasing is enforced by `tests/test_ui_reachability_warning_rendering.py::test_humanize_summary_uses_hedged_copy`.

**Resolver coverage.** Stage F ships Python (`python`, `python -m`, `uvicorn`, `flask`, `gunicorn`) and JS/TS (`node`, `npm`, `vite`, `tsx`, `ts-node`, plus `npx <tool>` which strips the wrapper before classification) via pure-Python regex parsing. Other languages emit a single `no_resolver` warning per phase. Test-runner entries (`pytest`, `jest`, `vitest`, `playwright`, ...) emit a distinct `reachability_not_applicable` event so visibility is preserved without polluting the warning channel. Promotion to a blocking gate (`ERR_UNREACHABLE_MODULE`) is the explicit job of **P3 Stage A**, gated on (1) per-language resolver coverage matching the demo project mix, (2) measured false-positive rate below an agreed threshold from launch data, (3) a `pure_library: true` suppression marker so known-pure files can opt out.

### Pipeline event catalogue additions (P1 Stage F)

| Event | When | `detail` shape |
|---|---|---|
| `reachability_warning` | Orchestrator emits on the executor PASS path when `executor_advisory_detail.json` contains a populated `reachability_summary` or non-empty `reachability_diagnostics`. One summary event per phase + one event per diagnostic. | `{kind, count?, files?, command?, file?, reason}` where `kind ∈ {unreachable_summary, no_resolver, resolver_limitation, resolver_error}` |
| `reachability_not_applicable` | Orchestrator emits when the gate signalled "consciously skipped" — entry command is a recognised test runner. | `{reason}` |

### Pipeline event catalogue additions (P2 — queue-lifecycle & destructive events)

These close the SILENT observability gaps: queue-lifecycle and destructive transitions that
previously changed state with **no timeline record**. Emitted by `orchestrator.py`, rendered in
the activity feed by `ui/index.html` (colour `getEventBadgeColor`, label `EVENT_TYPE_DISPLAY`,
hover `EVENT_TYPE_DESCRIPTION`, prose `humanizeSummary`). The `agent` field is `"queue"` for the
four queue events and `"escalation"` for `nuclear_reset`. Schema is additive — the UI SSE stream
and ring buffer tolerate the new types with no migration.

| Event | When | `detail` shape |
|---|---|---|
| `nuclear_reset` | `nuclear_reset_phase()` — after the `nuclear_resets` increment + `reset_log` append, before delegating to `reset_phase()` (so `phase` is the pre-reset escalated phase). Records the destructive *action*; `escalation_resolve` already records the *command*. | `{nuclear_resets, reason, phase}` (`reason` = `last_error_code`) |
| `queue_halted` | `_select_next_queue_project()` — inside the `if halt_if_no_eligible:` branch, right after `transition_state("QUEUE_HALTED", …)`. The reason-clearing `else` (caller owns final status, e.g. PIPELINE_COMPLETE) does **not** emit. | `{reason}` where reason ∈ {`all_blocked`, `all_dependency_hold`, `answered_pending_revival`, `mixed`, `all_completed`} |
| `queue_parked` | `_queue_park_active_entry()` — after the successful queue write. Single emit for all 4 call sites (BLOCKED/ESCALATION), once each. | `{reason, phase, entry_id, entry_name}` |
| `queue_revived` | `_select_next_queue_project()` revival branch — after `_apply_pending_escalation_command()` (which now returns the applied command). Guarded on `is_revival` + a real command, so the fresh-start path never emits. | `{entry_id, entry_name, command}` |
| `dependency_hold` | `_select_next_queue_project()` — after a genuine READY→DEPENDENCY_HOLD write. An already-held entry is skipped by the state gate at the top of the selection walk before reaching the assignment, so there is no re-emit. | `{parent_id, entry_id, entry_name}` |

---

## 4.5. Advisory Checks vs Blocking Gates

P1 Stage F establishes the first formal *advisory check* pattern in the pipeline. The pattern is captured here once so future advisory checks (notably P3 Stages A, D, E) extend a documented shape rather than reinventing it.

**Definition.** An advisory check runs after every blocking check in a gate has passed. It writes structured output to a dedicated *advisory* artifact file, NOT the gate's failure-detail file. The gate exit code is unchanged. The orchestrator drains the advisory file on the success path and emits pipeline events. The check NEVER blocks the phase.

**The two-channel artifact rule.** Failure detail belongs in `executor_gate_detail.json` (consumed by `write_failure_context` for executor self-heal). Advisory output belongs in `executor_advisory_detail.json` (consumed by `_emit_reachability_advisory` for events). These channels never co-tenant. Future advisory checks should add new top-level keys to the advisory file's envelope, not new entries inside the failure file.

**Promotion criteria (advisory → blocking).** An advisory check can be promoted to a blocking gate only when ALL of the following hold:

1. **Resolver coverage** for the supported languages matches the actual demo project mix (operationally, ≥80% of demo `project_type`s have a stable resolver).
2. **Measured false-positive rate** from advisory-mode operation is below an agreed threshold (operationally, <10% over 20+ demo runs).
3. **Operator escape hatch** for known-correct deviations exists. For reachability this is the `pure_library: true` suppression marker in the planner's `implementation_plan`.

Until all three hold, the check stays advisory. Promotion is a deliberate per-check decision, not a default trajectory.

**Reviewer-facing variant (Phase 3, gate-feedback methodology).** The reachability advisory is consumed by the orchestrator (events only) and its file is removed after draining. A second variant of the two-channel rule demotes *interpretive blocking* checks to warnings consumed by the **reviewer**: the executor gate writes `ERR_MANIFEST_FILE_MISSING` / `ERR_TDD_COVERAGE_MISMATCH` / `ERR_BEHAVIORAL_ARTIFACTS_MISSING` to `gate_warnings.json` and PASSes, and the reviewer adjudicates (accept, or reject into a `blocking_issue`). The only structural differences from the reachability advisory are the consumer (reviewer, not orchestrator) and the lifecycle (`_emit_gate_warnings` **preserves** the file for the reviewer rather than removing it). See **§ Executor Output Gate** for the full check list and the safety carve-out for `ERR_PATH_TRAVERSAL`.

---

## 5. Reviewer Agent

### Model

`openrouter/moonshotai/kimi-k2.6` (Kimi K2.6 / Moonshot AI, cloud via OpenRouter)

> **Model update — 2026-03-12:** Reviewer migrated from local Qwen3.5-27B to MiniMax M2.5 via OpenRouter after smoke testing confirmed unacceptable latency for local reviewer inference. The escalation agent remains on local Qwen. (Historical note. The reviewer has since moved again to Kimi K2.6 via OpenRouter — see the live model line above and the §1 inventory; `openclaw.json` is the source of truth.)

### Invocation Contract

```
POST /hooks/agent
  agentId: "reviewer"
  sessionKey: "pipeline:phase-N:{raw_id}:reviewer-attempt-1"
```

> ⚠️ AMBIGUITY: The HTML uses `pipeline:phase-N-review` in the flow diagram (line 630) but the session management section (line 941) uses the pattern `pipeline:phase-N:{raw_id}:reviewer-attempt-X`. The session management section is the authoritative reference for session key patterns. Use `pipeline:phase-N:reviewer-attempt-X`.

### Input Files

- `executor_output.json`
- `planner_output.json`
- `current_phase.json`
- `phase_state.json` (includes which pass this is)

### 3-Pass Logic

The reviewer gate runs the same script on each pass. Routing on failure varies by pass number:

| Pass | On Blocking Issues | Routing |
|---|---|---|
| 1 | Blocking issues present | Re-run executor with `blocking_issues` in context |
| 2 | Blocking issues present | Check `attribution` field: `plan` → planner, `impl` → executor (final retry) |
| 3 | Blocking issues present | → Escalation agent (no more retries) |
| Any | No blocking issues + tests passing + behavioral verdict pass (when block present) + visual verdict pass (when applicable) | → Merge |

### Output Schema — `reviewer_output.json`

```json
{
  "blocking_issues": {
    "type": "array",
    "items": {
      "type": "object",
      "properties": {
        "description": { "type": "string" },
        "attribution": { "type": "string", "enum": ["plan", "impl"] },
        "affected_file": { "type": "string" }
      },
      "required": ["description", "attribution", "affected_file"]
    },
    "description": "Empty array means pass"
  },
  "suggestions": {
    "type": "array",
    "items": { "type": "string" },
    "description": "Non-blocking suggestions, appended to suggestions.md on merge"
  },
  "integration_tests_passing": {
    "type": "boolean"
  },
  "behavioral_verification": {
    "type": "object",
    "description": "Structured replacement for the legacy phase_intent_validated boolean (removed in P0 Stage F). REQUIRED on every phase whose current_phase.json carries a populated behavioral_verification block (effectively every P0 phase).",
    "properties": {
      "verdict": { "type": "string", "enum": ["pass", "fail", "cannot_verify"] },
      "evidence": {
        "type": "array",
        "minItems": 3,
        "description": "Minimum 3 entries on verdict='pass'. Each anchor is the reviewer's independent record of one exercised public-surface claim.",
        "items": {
          "type": "object",
          "properties": {
            "claim": { "type": "string" },
            "file_or_screenshot_or_log": { "type": "string", "description": "Workspace-relative path; gate enforces both path safety and on-disk existence." },
            "method": { "type": "string", "description": "e.g. playwright_screenshot, curl_then_jq, stdout_capture, log_grep." }
          },
          "required": ["claim", "file_or_screenshot_or_log", "method"]
        }
      },
      "how_to_check_followed": { "type": "boolean", "description": "True if the reviewer ran the phase's how_to_check procedure end-to-end; false if it only inspected the executor's behavioral_smoke_artifacts." }
    },
    "required": ["verdict", "evidence", "how_to_check_followed"]
  },
  "visual_verification": {
    "type": "string",
    "enum": ["pass", "fail", "cannot_verify"],
    "description": "REQUIRED on visual phases only (UI-*, INT-*, or AUTODEV_VISUAL_PHASE_RAW_IDS). Omit on non-visual phases. See visual_smoke_artifacts."
  }
}
```

### Sentinel

`reviewer_output.done` — written after `reviewer_output.json`.

---

## 6. Escalation Agent

> **F13 / F8 / F2 update (Phase 1 escalation-recovery) — supersedes the older prose in this
> section where they conflict.**
> - **The escalation agent is NOTIFY-only.** OpenClaw wraps every `/hooks/agent` payload in an
>   "EXTERNAL, UNTRUSTED … prompt injection" preamble; the agent was refusing the orchestrator's
>   own escalation webhook as an injection attempt and producing no `escalation_output`. The agent
>   docs + webhook messages now frame a pipeline escalation as a **TRUSTED control invocation** and
>   instruct the agent only to **notify** the operator via its `message` tool. It does **not** wait
>   for / relay a reply and does **not** write `escalation_output`. The "Sentinel Pattern Bridge"
>   below (agent relays the operator's Signal reply into `escalation_output.json`) is **no longer
>   how answers arrive**: the operator answers from the **dashboard** (`POST /api/command`), which
>   writes `escalation_output` (or banks `pending_escalation_command.json`); the orchestrator polls
>   and consumes it exactly as before. (A real inbound Signal→`escalation_output` channel is a
>   future enhancement — `plans/upcomming/signal-inbound-escalation-channel.md`.)
> - **A parked `ESCALATION` no longer counts as `all_blocked` (F8).** When it is the next/only
>   remaining work, `_select_next_queue_project` revives the lowest-position one to
>   `WAITING_FOR_HUMAN` (after startable + `ESCALATION_ANSWERED` entries get priority), so it stays
>   answerable live instead of stranding in `QUEUE_HALTED` / `PIPELINE_COMPLETE`.
> - **`POST /api/stop` accepts `QUEUE_HALTED` (F1); relaunch uses `--revive <entry_id>` (F2)** to
>   resume a specific parked entry at its escalated phase rather than a phase-0 reset.

### Model

`llama-local/qwen3.5-27b` (local via llama-server)

### Triggers

- Planner retries exhausted
- Executor retries exhausted (including cloud fallback failure)
- Reviewer pass 3 still blocking
- Repo init check fails
- Tool-call-count threshold exceeded (executor context pressure)
- Unhandled exception in `orchestrator.py`
- Merge conflict on phase completion

### Tool Policy (Hard Limits)

Enforced via OpenClaw per-agent tool policy in `openclaw.json` — not just AGENTS.md instruction.

| Category | Allowed |
|---|---|
| Read tools | Workspace files, logs, `phase_state.json`, output JSONs |
| Shell read-only | Process checks, network reachability (e.g., `curl http://<llama-server-host>:11434/health`) |
| Write tools | **SANDBOXED** — `write` is allowed but scoped to the agent's declared workspace directory. Writes to absolute paths outside the workspace silently discard the data. The `pipeline-project/` symlink inside the workspace provides the only write path to shared pipeline files (`escalation_output.json`, `escalation_output.done`). All other write-adjacent tools (`edit`, `apply_patch`) remain denied. |
| Exec tools | **DENIED** — cannot restart services, run scripts, or modify pipeline state |

```json
// openclaw.json tool policy for escalation agent
"tools": {
  "allow": ["read", "write"],
  "deny": ["edit", "apply_patch", "exec", "process", "browser"]
}
```

> **Write sandboxing:** OpenClaw sandboxes the `write` tool to each agent's declared workspace directory. When `"write"` is denied entirely, the agent cannot produce its required sentinel files (`escalation_output.json`, `escalation_output.done`). No path-scoped write exception mechanism exists in OpenClaw. Write access is granted but sandboxed — the agent can only write to files within its workspace boundary. The `pipeline-project/` symlink inside the workspace provides the only write path to shared pipeline files.

### Signal Delivery

The agent reads workspace context (`phase_state.json`, failure logs, relevant output files), performs environment checks where relevant (e.g., llama-server health at `http://<llama-server-host>:11434/health` on executor failures), then sends a precise, non-verbose Signal message containing: what paused, why, what it found, open questions, available actions.

Pipeline completion is also notified via Signal (not just escalation).

### Resume Commands

| Command | Behavior | Valid For |
|---|---|---|
| `RETRY` | Re-POST the exact webhook that failed, no state change | Any transient failure (network, timeout, fluke) |
| `RESET_PHASE` | Full phase reset with cap enforcement. Resets git to `phase_base_commit`, deletes phase branch, clears all 6 output pairs, re-initializes `phase_state.json` (agent counters → 0, `escalation_resets` preserved), re-invokes planner. Increments `escalation_resets`. Cap: 3. | Plan is fundamentally flawed; start phase from scratch |
| `RESET_EXECUTION` | Partial reset. Preserves planner output (`planner_output.json/done`). Clears executor and reviewer outputs and **preserves the working tree** so the executor iterates on its prior work — the hard reset to HEAD runs only on an `ERR_UNACCOUNTED_DELETION` failure (Phase 2). Re-invokes executor. Increments `escalation_resets`. Cap: 3. (For a clean-slate restart, use `RESET_PHASE`.) | Plan is sound but executor implementation failed; preserve the plan, retry execution |
| `SKIP` | Marks phase N as `[-]` skipped in roadmap, then **re-resolves the next pending phase via the shared `_advance_to_next_pending_phase()` helper** (F3) — the same path the reviewer-PASS completion uses — clearing the per-phase pipeline artifacts and starting the genuine next pending phase (not blindly "N+1"). The git phase *branch* is not deleted (no `git reset`); manual git cleanup is still the operator's responsibility. | Only when the phase outcome is acceptable and git cleanup will be handled manually |
| `STOP` | Pipeline stays halted, full manual intervention required | Always valid |
| `PROCEED` | Skips the merge step; marks phase `[x]` in roadmap, force-tags `phase-N-complete`, then **re-resolves and advances via the shared `_advance_to_next_pending_phase()` helper** (F3) so it genuinely moves *past* this phase (previously it re-ran the just-closed phase and could loop straight back to escalation). Does not append to `suggestions.md`. | Phase branch already merged into base externally; use to advance after manual merge |
| `NUCLEAR_RESET` | **Operator escape hatch (P1 Stage G2).** Thin wrapper over `reset_phase()` — same destructive mechanics (git reset to `phase_base_commit`, delete phase branch, wipe all outputs, zero retry counters, clear `prior_blame_attributions`, re-invoke planner). Differs only in governance: increments its own `nuclear_resets` counter (cap **2**) instead of `escalation_resets`, and appends a `reset_log` entry. | Every automatic retry and operator reset is spent (`escalation_resets >= 3`) but the operator fixed an *external* cause (infra/config, outside the repo) and wants a true fresh start |

> **`RESTART PHASE` is a legacy alias for `RESET_PHASE`** — accepted by the orchestrator for backward compatibility with in-flight Signal conversations. Use `RESET_PHASE` in new invocations.

> **Invalid / empty command → `STOP` (not a halt):** if the consumed `escalation_output.json` carries an empty, missing, or unrecognised `command`, the orchestrator emits an `escalation_command_invalid` event and defaults to `STOP` (recoverable via Resume) rather than `HALTED_SILENT`. This matches the sibling fallbacks (`_apply_pending_escalation_command`, JSON-parse failure), which already default to `STOP`. (Heals PIPELINE-CONSTRAINTS.md §5.2.)

**Escalation reset cap:** `RESET_PHASE`, `RESET_EXECUTION`, and `RESET_REVIEWER` all share the same cap: `escalation_resets >= 3`. All three commands increment `escalation_resets`. After 3 escalation-triggered resets, the orchestrator sends a Signal notification and stays in `WAITING_FOR_HUMAN`. Only `PROCEED`, `SKIP`, or `STOP` can advance past the cap. The `escalation_resets` counter is NOT zeroed inside `reset_phase()` — it is only zeroed when the roadmap genuinely advances to a new phase. This prevents circumventing the cap by repeatedly triggering phase resets.

**Nuclear reset cap (P1 Stage G2):** `NUCLEAR_RESET` is governed by a **separate** `nuclear_resets` counter, capped at **2**, *independent* of `escalation_resets`. It is available **precisely because** the escalation cap is exhausted, not in spite of it — the dashboard renders its button only when `escalation_resets >= 3` and hides it again at `nuclear_resets >= 2`. `nuclear_reset_phase()` increments `nuclear_resets` and appends a `reset_log` entry, then delegates to `reset_phase()`, which **preserves** `nuclear_resets` and `reset_log` across its re-init (alongside `escalation_resets`) — so the cap accumulates and the audit trail survives. Like `escalation_resets`, `nuclear_resets` is NOT zeroed inside `reset_phase()`; it zeroes only on genuine phase advance. After 2 nuclear resets the only remaining paths are `SKIP` (Abandon Phase) or `STOP` — a legible "something is genuinely wrong here" signal. The dispatch sends the same Signal "cap reached" notice the other resets use when `nuclear_resets >= 2`.

**Separation of counters:** `executor_retries` tracks automatic retry-path resets (incremented by `reset_execution("auto")`). `escalation_resets` tracks human-triggered resets via `RESET_PHASE`/`RESET_EXECUTION` (incremented by `reset_execution("escalation")` and the `RESET_PHASE` handler). Never both in one operation.

**P0 Stage H — three retry-counter dimensions:** alongside the legacy per-segment `executor_retries`, the orchestrator persists two lifetime counters that accumulate across the whole phase:

- `executor_self_failure_retries` — incremented by `reset_execution("auto")` whenever the executor's own gate or sentinel poll fails. Never reset on reviewer rejection or operator escalation reset.
- `executor_reviewer_rejection_retries` — incremented inline at the orchestrator's `ROUTE_EXECUTOR` handler (where `set_reviewer_rejected()` already fires). Tracks reviewer-driven re-runs separately from executor self-failures.

Both lifetime counters reset only inside `reset_phase()` (true new phase). The canonical metrics row sources `executor_attempts` from these two so the invariant `executor_attempts == executor_self_failures + executor_reviewer_rejections + 1` holds across reviewer-driven mid-phase resets of the per-segment counter. The legacy `executor_retries` field stays in place for escalation/cap logic that needs the per-segment budget. The orchestrator's process-local `_current_attempt_retry_class` (values `"initial_attempt"` / `"executor_self_failure"` / `"reviewer_rejection"`) is set by `reset_phase()`, `reset_execution("auto")`, and the `ROUTE_EXECUTOR` handler, and stamped onto every `gate_fail` and `attempt_end` pipeline event's `detail.retry_class` so the UI can distinguish retry sources in the activity feed.

Resume commands trigger `orchestrator.py` operations.
**Sentinel Pattern Bridge:** The human's response from Signal is passed back to the orchestrator via the `escalation_output.json` file. The Escalation Agent has a strict tool policy carve-out in `openclaw.json` allowing it to write exactly this file and its sentinel (`escalation_output.done`). While awaiting a reply (`WAITING_FOR_HUMAN`), the orchestrator actively polls for this sentinel, parses the command, and triggers the corresponding state transition.
**`PROCEED` implementation detail (F3):** On receiving `PROCEED`, the orchestrator skips the merge step (assumes git state is already correct), then `git tag --force phase-N-complete`, updates roadmap to `[x]`, and calls the shared `_advance_to_next_pending_phase(trigger="proceed")` helper. That helper clears the per-phase pipeline artifacts, re-runs `phase_resolver`, and then either starts the next pending phase, completes the pipeline, parks on a blocked next phase, or — on a resolver failure (exit 1 / unexpected output / subprocess crash) — **routes to escalation** (`current_agent="escalation"`, `last_error_code=ERR_PHASE_RESOLVER_FAILED`, returns `"continue"`; F4), exactly as the reviewer-PASS completion path does, so the three sites cannot drift. `SKIP` uses the same helper (`trigger="skip"`) after marking `[-]`. It does NOT append to `suggestions.md`. If the tag or roadmap update fails, escalate again — do not silently advance.

### Ambiguous Reply Protocol

1. If reply is not clearly one of the six commands → escalation agent re-prompts once, restating the options explicitly
2. If second reply is also ambiguous → default to `STOP`; do not guess intent on pipeline state changes

While awaiting reply: pipeline status is `WAITING_FOR_HUMAN`; heartbeat cron does not attempt restart. No timeout on human reply — pipeline waits indefinitely (intentional).

### Escalation Agent Failure — Fallback Chain

Sequential, not parallel:

1. Orchestrator invokes escalation agent via webhook → waits for escalation agent's sentinel
2. If sentinel appears → confirmed delivery, no fallback needed
3. If sentinel does NOT appear within timeout → orchestrator sends raw Signal template directly via OpenClaw webhook (no LLM, plain text with phase/gate/error context). **Note:** this requires OpenClaw gateway to be reachable; if OpenClaw is down, skip to step 4
4. If raw Signal also fails → write `escalation_failed.json` to workspace, set pipeline status to `HALTED_SILENT`, stop

### `escalation_failed.json`

```json
{
  "timestamp": "<ISO 8601>",
  "phase": "<int>",
  "gate": "<string>",
  "original_failure_reason": "<string>"
}
```

### Silent Halt Behavior

- `HALTED_SILENT` is written **only** when escalation delivery fails — all three fallbacks (escalation agent webhook, raw Signal webhook, direct write) have been exhausted. It is **not** the terminal state for clean pipeline completion; `PIPELINE_COMPLETE` is used for that.
- An invalid / empty / unrecognised resume *command* (a consumed `escalation_output.json` whose `command` is unknown) is **not** a `HALTED_SILENT` trigger: the consumer emits `escalation_command_invalid` and defaults to `STOP` (recoverable). Only escalation *delivery* failure halts silently.
- **F10 — two former silent sinks now escalate, not halt.** An unknown/unrecognised *reviewer-gate verdict* (F10(b)) and an *activity-stamp-init failure* (F10(a), `_init_activity_stamp_or_escalate`) previously dead-ended at `HALTED_SILENT` with no notification. Both now set `current_agent="escalation"` + `transition_state("RUNNING", …)` so the next loop iteration fires the escalation dispatch (notify ⟺ escalate; only the escalation agent sends Signal). This is what makes the "delivery-failure only" invariant above hold in code.
- **F11 — `HALTED_SILENT` is operator-recoverable from the UI** via `POST /api/resume-ready` (→ `WAITING_FOR_HUMAN` + escalation), without the phase-destroying `git-recover`. See the endpoint section.
- `HALTED_SILENT` prevents heartbeat from restarting orchestrator (same as `WAITING_FOR_HUMAN`)
- Detection is by absence — no Signal activity, pipeline idle — manual check required
- No infinite notification retry loop — systematic failure will not be self-resolving

### Parked-escalation revival (P1 Stage H)

When a project escalates under an **auto-queue**, the orchestrator parks it (`_queue_park_active_entry("ESCALATION")`) and advances to the next eligible project. The operator may **bank** an answer for the parked project at any time via `POST /api/command` with `target_project_path`; the server writes only the per-project `pending_escalation_command.json` (it never writes `pipeline_queue.json`). The loop closes on the next selection:

1. **Promote.** `_promote_answered_escalations` (a pre-pass at the top of `_select_next_queue_project`) flips any `ESCALATION` row whose project has a banked `pending_escalation_command.json` to `ESCALATION_ANSWERED`. This is the single writer of that transition — orchestrator-owned (the server writes only the per-project pending file); the write itself goes through the F9 version-CAS path (see *Queue write concurrency* below), so it composes with concurrent UI writes without a lock.
2. **Revive (restore, don't restart).** Selection admits `ESCALATION_ANSWERED` as a second eligible class. Because `pipeline_state.json` is global and a fresh start resets it to phase 0, the escalated phase pointer would otherwise be lost — so `_queue_park_active_entry` snapshots it into the entry's `parked_state_snapshot` at park time, and the revival branch **restores** that snapshot instead of the phase-0 reset.
3. **Apply.** The revival branch reuses `_apply_pending_escalation_command`, which converts the banked file into `escalation_output` and sets `WAITING_FOR_HUMAN` / `current_agent="escalation"`; the next loop's escalation dispatch consumes the command against the restored phase.

`parked_state_snapshot` schema (all fields the global-state reset would destroy):

```json
{
  "current_phase": 4,
  "current_phase_raw_id": "CORE-2",
  "planner_retries": 0,
  "executor_retries": 2,
  "executor_self_failure_retries": 1,
  "executor_reviewer_rejection_retries": 0,
  "reviewer_retries": 2,
  "phase_base_commit": "<sha>",
  "phase_start_time": "<ISO 8601>"
}
```

`phase_base_commit` is **load-bearing**: `reset_phase()` guards its `git reset --hard` on it, so a revived `RESET_PHASE` (or `NUCLEAR_RESET`, which calls `reset_phase()`) without it would resume on a dirty tree. `escalation_resets` / `nuclear_resets` / `reset_log` are **not** snapshotted — they live in the per-project `phase_state.json`, which survives via the `pipeline-project` symlink. **Invariant:** in the activation block `update_symlink` runs first and is shared by both the revival and fresh-start paths (the branch splits only the `self.state` write), so the restore and the banked command always act on the *revived* project's repo.

**Bankable commands.** The seven escalation-panel commands (`RESET_PHASE`, `RESET_EXECUTION`, `RESET_REVIEWER`, `PROCEED`, `SKIP`, `STOP`, `NUCLEAR_RESET`) — all phase-level. `NUCLEAR_RESET` (P1 Stage G2) rides this same promote → revive → apply path with no Stage-H change: `_apply_pending_escalation_command` is command-agnostic, and because the revival restores the escalated-phase pointer (incl. `phase_base_commit`) before applying the banked command, a revived `NUCLEAR_RESET`'s `git reset --hard` lands on the *revived* project's repo. `RETRY` is **not** bankable (it is the `StoppedRecoveryPanel` flow, not an escalation-panel command), so no mid-agent fidelity is required in the snapshot.

**`QUEUE_HALTED` recovery.** When the escalated project is the last entry, the orchestrator exits to `QUEUE_HALTED`. A *restart* into that state carries `current_agent="escalation"`, so the startup function skips selection, and `QUEUE_HALTED` is intentionally not in the main-loop exit set (the loop stays alive to poll for an *in-place* answer). `Orchestrator._maybe_revive_on_queue_halted()` runs once at `run()` startup, before the gated startup function: it promotes banked answers and revives a parked project; with nothing to consume it returns `False` and `run()` exits cleanly (no spin), but it continues into the loop when an in-place `escalation_output.done` is already pending. The `answered_pending_revival` halt-reason (set ahead of `all_blocked`) marks the queue recoverable; the dashboard surfaces a **Resume banked answer** control that reuses `POST /api/queue/{entry_id}/relaunch`.

### Queue write concurrency (F9 — optimistic version-CAS)

`pipeline_queue.json` is written by **two independent OS processes** — the UI server (`add` / `delete` / reorder / `parent` / `mode`) and the spawned orchestrator (`ACTIVE` / `COMPLETED` / park / promote) — with **no file lock**. `os.replace` keeps each write atomic (no torn reads) but is last-full-write-wins, so a naive read→mutate→write on each side silently drops the other's update. F9 closes that with optimistic concurrency:

- **`queue_version`** — a monotonic integer stamped on every write (the CAS token). A legacy file written before F9 has no such key; readers treat that as **0** (additive schema, no migration). The single increment site is `queue_semantics.bump_queue_version`, called by the two atomic writers (`orchestrator._write_queue`, `server._write_queue_file`).
- **The CAS loop** — `queue_semantics.mutate_queue(read_fn, write_fn, current_version_fn, mutate_fn)`, driven per-process by `orchestrator._mutate_queue` and `server._mutate_queue_file`. It reads the queue, captures the base version, applies `mutate_fn` (a **pure, idempotent, id-keyed** in-memory change), re-reads the on-disk version immediately before `os.replace`, and commits `base+1` **only if unchanged** — else it re-reads and re-applies onto the fresh queue, bounded by `QUEUE_MAX_CAS_RETRIES` (8). A `mutate_fn` raises `QueueAbort` to commit nothing (used when a re-read shows the targeted row vanished).
- **Side effects stay outside the retried closure.** The two complex writers interleave non-idempotent effects with the queue write: `_select_next_queue_project` repoints the `pipeline-project` symlink (and later writes `pipeline_state`, the run manifest, the banked command); `_queue_run_trigger_next_logic` calls `_spawn_orchestrator`. These run **once**, outside `mutate_queue`, with only the id-keyed entry-state tweak inside the retried closure — so a CAS retry never double-repoints the symlink or double-spawns.
- **Residual & failure surface.** Lock-free CAS on a bare file leaves a microsecond window between the pre-write version check and `os.replace` that cannot be fully closed without a lock or a per-write nonce — negligible for two low-frequency writers on one host; the deferred `queue_write_token` nonce (writer-unique token + post-replace read-back) is the documented way to close it. On exhausted contention `mutate_queue` raises `QueueVersionConflict`: the orchestrator's best-effort internal writes log it, and the server maps it to **HTTP 503** (transient/retryable) via one `@app.exception_handler`. An advisory `flock` was rejected (extra lock + nesting risk against `pipeline.lock`).

---

## 7. Gate Scripts

All gates are deterministic Python scripts on the Pi. Zero LLM tokens (exception: blame attribution LLM fallback). Completion detected via sentinel files (`.done`), not JSON file presence. Gates poll for sentinel then parse JSON.

Gate scripts always wrap JSON load in `try/except` — unhandled parse exception must never crash the orchestrator.
Any updates back to `phase_state.json` must be written atomically (e.g., write to a tempfile then replace) to prevent corruption from power loss or race conditions across invocations.

### Planner Output Gate

**Validation checks:**
```
IF planner_output.json does NOT exist         → FAIL
IF implementation_plan missing OR empty array  → FAIL
IF tdd_test_structure missing OR empty array   → FAIL
IF pass_criteria missing OR length < 1         → FAIL
IF any pass_criteria item lacks "condition"    → FAIL
ELSE                                           → PASS
```

**On FAIL:** Increment `planner_retries` in `phase_state.json`.

**Branching:**
```
IF PASS                    → proceed to executor
IF FAIL AND retries < 3    → re-invoke planner with failure detail appended
IF FAIL AND retries >= 3   → escalation agent
```

### Executor Output Gate

**Validation checks:**
```
# --- Blocking checks (return FAIL) ---
IF executor_output.json does NOT exist                       → FAIL
IF status != "complete"                                      → FAIL
IF test_results.all_passing != true                          → FAIL
IF any paths in tests_written or file_manifest attempt path
    traversal outside the shared workspace                   → FAIL (ERR_PATH_TRAVERSAL)
IF git diff --diff-filter=D <phase_base_commit> HEAD reveals
    a deleted file absent from BOTH file_manifest AND
    files_deleted                                            → FAIL (ERR_UNACCOUNTED_DELETION)

# --- Interpretive checks (Phase 3: non-blocking WARNINGS, not FAIL) ---
# Recorded in gate_warnings.json and adjudicated by the reviewer; the gate PASSes.
IF file_manifest: an expected file is absent on disk         → WARN (ERR_MANIFEST_FILE_MISSING)
IF a tdd_test_structure entry is absent from tests_written   → WARN (ERR_TDD_COVERAGE_MISMATCH)
IF behavioral block present AND behavioral_smoke_artifacts
    missing / empty / malformed / not-on-disk                → WARN (ERR_BEHAVIORAL_ARTIFACTS_MISSING)
ELSE                                                         → PASS
```

**Interpretive checks are warnings, not FAILs (Phase 3, gate-feedback methodology).** The three checks above marked `WARN` formerly returned `FAIL` and burned an executor retry before the reviewer ever saw the work. They are now recorded as non-blocking warnings in `gate_warnings.json` (`{phase_raw_id, warnings: [{code, detail, files?/missing_tests?}]}`); the gate PASSes and the reviewer adjudicates — accept-and-proceed, or reject-with-specifics into a `blocking_issue` on the existing ROUTE_EXECUTOR loop. This is the reviewer-facing variant of the §4.5 advisory pattern: `_emit_gate_warnings` drains the file into a `gate_warning` event and a `phase_state.last_gate_warnings` stash but, unlike the reachability advisory, **preserves** the file for the reviewer to read. **The `ERR_PATH_TRAVERSAL` boundary check is NOT demoted** — it shares the manifest/behavioral loops but a path escaping the workspace is a safety failure and stays a hard `FAIL`. The reviewer's own independent checks (file_manifest existence, test quality, `behavioral_verification`) are the backstop the demotion relies on.

**`ERR_UNACCOUNTED_DELETION`:** The gate runs `git diff --name-only --diff-filter=D <phase_base_commit> HEAD` in the workspace, plus `git ls-files --deleted` for uncommitted deletions, then cross-references the union against `file_manifest` and the optional `files_deleted` array. Any file that appears in neither list triggers this error. If `phase_base_commit` is absent from `pipeline_state.json`, the gate **fails closed**, returning the stdout string `FAIL` (exit 0) with `last_error_code=ERR_MISSING_BASE_COMMIT` in `phase_state.json` — it does not skip or warn. (The executor gate is a *verdict gate*: it signals via a stdout verdict string + exit 0, never an exit code — see the two-convention "Gate Script Interface Contract" in CLAUDE.md.) Without a base commit reference the deletion check cannot run, and a silent skip would allow MiniMax file-deletion to go undetected. The orchestrator retries with a fresh session on this error. The same fail-closed contract covers `git diff` returning non-zero (`ERR_GIT_DIFF_FAILED`) and the deletion check itself crashing — git missing/killed/timeout (`ERR_DELETION_CHECK_CRASHED`); none of the three may skip-and-PASS. (Older versions of this spec described the absent-`phase_base_commit` case as non-fatal and skipped with a warning — that behaviour was removed; see `autodev/tests/test_defensive_c3_07.py` and `autodev/tests/test_executor_gate_unaccounted_deletion.py` for the fail-closed contract.) This catches models that delete files under token pressure and self-report `all_passing: true` — see PIPELINE-CONSTRAINTS.md §2 > MiniMax M2.5 File Deletion Under Token Pressure.

The `status` check runs first. An executor that self-reports `"stuck"` or `"failed"` is an immediate gate failure regardless of test results — see PIPELINE-CONSTRAINTS.md § Executor Status Corner Case for rationale.

**On FAIL:** Increment `executor_retries` in `phase_state.json`.

**Branching:**
```
IF PASS                    → proceed to reviewer
IF FAIL AND retries < 3    → re-invoke executor with failure detail
IF FAIL AND retries >= 3   → run blame attribution
```

### `failure_context.json` — Failure Context Artifact

Written atomically by the orchestrator **before every routing decision** that follows an agent failure. This happens at four call sites: planner gate fail, executor gate fail, executor retries exhausted (blame path), and reviewer gate fail. The file is cleared at phase start alongside other working files.

**Schema:**
```json
{
  "timestamp": "<ISO 8601 UTC>",
  "phase_raw_id": "<string>",
  "failing_agent": "<planner|executor|reviewer>",
  "attempt_number": "<int>",
  "gate_error_codes": ["<ERR_...>"],
  "agent_status": "<complete|failed|stuck|null>",
  "agent_failure_reason": "<string|null>",
  "agent_troubleshooting_attempts": ["<string>"],
  "blocking_issues": [{
    "description": "...", "attribution": "plan|impl", "affected_file": "...",
    "criterion_source": "behavioral|test|regression_prior_phase|free",
    "criterion_id": "behavioral_evidence[N] | tests/<path> | <prior phase raw_id> (absent on free)"
  }],
  "behavioral_verification_evidence": {
    "verdict": "pass|fail|cannot_verify",
    "how_to_check_followed": "<bool>",
    "evidence": [{"claim": "...", "file_or_screenshot_or_log": "...", "method": "..."}]
  },
  "current_phase_behavioral_verification": {
    "user_observable": "...",
    "how_to_check": "...",
    "failure_language": "..."
  },
  "tests_written": ["<path>"],
  "tests_passing": "<bool|null>",
  "file_manifest": ["<path>"],
  "files_present_on_disk": ["<path — glob of SYMLINK_TARGET, excluding pipeline metadata>"],
  "planner_retries_at_failure": "<int>",
  "executor_retries_at_failure": "<int>",
  "reviewer_retries_at_failure": "<int>",
  "prior_blame_attributions": [{"layer": 1, "fault": "...", "routing": "..."}]
}
```

`files_present_on_disk` vs `file_manifest` comparison is the primary signal for the blame analyst: missing files indicate deletion or failed write; unexpected files indicate scope creep. The file is consumed by Layer 1 blame analyst (see below).

**Self-heal feedback loop (P0 Stage G).** Three additions land here so the executor's reviewer-rejection retry pass has both halves of the failed verification in one read:

- `current_phase_behavioral_verification` is the **claimed** half — copied verbatim from `current_phase.behavioral_verification` (user-observable / how_to_check / failure_language). Captured on every failure write, regardless of which agent failed; the field is `null` only when the current phase has no behavioural block (transitional pre-P0 in-flight phases).
- `behavioral_verification_evidence` is the **observed** half — copied verbatim from `reviewer_output.behavioral_verification` when the reviewer is the failing agent. `null` otherwise so a stale reviewer verdict from a prior attempt cannot pollute the executor's failure context.
- Each `blocking_issues[i]` additionally carries `criterion_source` (four-valued enum) and `criterion_id` so the executor knows *which* anchor failed. When `criterion_source == "behavioral"`, the executor's `AGENTS.md` Scenario B (line 145) requires re-running the phase's `how_to_check` procedure after the targeted fix and re-capturing fresh `behavioral_smoke_artifacts`. Stale artifacts on retry are the bug pattern this field eliminates.

The reviewer gate (`reviewer_gate.py:_synthesize_behavioral_blocking_issues`) synthesises `criterion_source: "behavioral"` entries from `behavioral_verification.evidence` when the reviewer leaves `blocking_issues` empty on a `fail` / `cannot_verify` verdict; the orchestrator's `_write_reviewer_failure_context` defaults entries arriving without `criterion_source` to the explicit `"free"` label so downstream code branches on a complete enum. See ASSUMPTIONS.md §J for the gate-side-vs-orchestrator-side write-back decision and the `criterion_id` format rationale.

### Blame Attribution — Three-Layer System

Runs after executor retries are exhausted (retries ≥ 3). Reads `failure_context.json` from the shared workspace. Every routing decision is logged to `lessons.md` in the format:
```
[BLAME] ts=<ISO> phase=<id> attempt=<n> layer=<1|2|3> fault=<fault> confidence=<confidence> routing=<plan|impl|escalate|default|fallback> reasoning=<string>
```

**Layer 1 — LLM analyst (always runs first):**

A single structured LLM call to `qwen3.5-27b` via direct POST — not a full agent turn; one prompt, one response, parsed immediately. This is the **only** point in the pipeline where the orchestrator itself makes an LLM call.

```python
POST http://<llama-server-host>:11434/v1/chat/completions
{
  "model": "qwen3.5-27b",
  "response_format": {"type": "json_object"},
  "messages": [{"role": "user", "content": "<failure_context.json contents>"}]
}
```

The standard OpenAI-compatible payload format is required because this POST bypasses OpenClaw and goes directly to llama-server.

The analyst returns `{"fault": "<plan|impl|infrastructure|unknown>", "confidence": "<high|medium|low>", "reasoning": "<string>"}`.

**Layer 1 routing table:**

| `fault` | `confidence` | Routing |
|---|---|---|
| `"plan"` | `"high"` | → re-route to planner |
| `"impl"` | `"high"` | → `reset_execution("auto")` (re-run executor) then escalate |
| `"infrastructure"` | `"high"` or `"medium"` | → escalation agent |
| Any other combination | — | Fall through to Layer 2 |

If the Layer 1 call fails (network error, timeout, unparseable response), fall through to Layer 2 without crashing.

**Layer 2 — Deterministic heuristics (fallback when Layer 1 is inconclusive or unavailable):**
```
IF tests failing on undefined interface       → planner blame (ambiguous schema)
                                              → re-route to PLANNER with failure logged
IF tests failing on implementation logic
    with correct interface                    → executor blame
                                              → ESCALATE
IF inconclusive                               → fall through to Layer 3
```

**Layer 3 — Hard default (fallback when both Layer 1 and Layer 2 are inconclusive):**

Returns `blame: "impl"` unconditionally. Routes to escalation agent. Logs `layer=3, routing=default` in `lessons.md`. Repeated Layer 3 fires signal that planner needs tighter phase scoping or that failure context collection is insufficient.

Blame attribution history is accumulated in `phase_state.json` under `prior_blame_attributions` for use by subsequent blame calls and agents.

**Impl blame cap — escalation after 3 consecutive `impl` attributions:**

After each blame attribution that returns `"impl"`, the orchestrator counts the number of consecutive `"impl"` entries at the tail of `prior_blame_attributions`. If this count reaches **3**, the orchestrator routes to the escalation agent instead of calling `reset_execution("auto")` again. This cap prevents an infinite retry loop when the executor LLM consistently produces structurally invalid output (e.g., empty `file_manifest`, `ERR_UNACCOUNTED_DELETION`) that the blame analyst correctly attributes to implementation failure but that no amount of automatic retries will resolve.

```
IF blame == "impl":
    consecutive_impl = count of trailing "impl" entries in prior_blame_attributions
    IF consecutive_impl >= 3 → escalation agent (impl blame cap reached)
    ELSE                     → reset_execution("auto") → re-invoke executor
```

The escalation agent receives the full `failure_context.json` and can issue `RESET_PHASE`, `RESET_EXECUTION`, or `STOP` as appropriate. The `escalation_resets` counter applies to any subsequent escalation-triggered resets.

### Reviewer Output Gate

**Done-criteria pre-check (runs before reviewer agent is invoked):**

The gate checks for two mandatory completion artifacts before evaluating reviewer output. If either is absent, it returns `MISSING_ARTIFACTS` immediately without consuming `reviewer_retries`.

```
IF phases/{phase_raw_id}.md does NOT exist       → MISSING_ARTIFACTS
IF metrics.jsonl does NOT exist OR last non-empty
    line does not contain phase_raw_id           → MISSING_ARTIFACTS
ELSE                                             → proceed to validation checks
```

**Orchestrator handling of `MISSING_ARTIFACTS`:**
```
reviewer_artifacts_retries += 1
IF reviewer_artifacts_retries >= 2  → escalation agent
ELSE:
    set executor_retry_directive in phase_state.json
    reset executor_retries to 0
    re-invoke executor (directive delivered as webhook message= by _invoke_executor)
```

`reviewer_artifacts_retries` is a separate counter that does NOT consume `reviewer_retries`. It is preserved across `reset_execution()` and only zeroed by `reset_phase()`. The one-shot `executor_retry_directive` in `phase_state.json` is **delivered to the re-invoked executor as the webhook `message=`** by `_invoke_executor` (which clears it after delivery, so it is one-shot) — the executor-side counterpart of the reviewer's `reviewer_retry_directive`. It is self-contained: because delivering `message=` replaces the executor's default prompt, the directive re-asserts that the prior implementation is preserved on the branch (do not re-implement) and instructs the executor to write the phase archive and metrics row before its sentinel.

**Validation checks (in order):**
```
IF current_phase.behavioral_verification populated AND reviewer_output.behavioral_verification
   missing/malformed (contract-shape failure)    → BEHAVIORAL_UNVERIFIED   ← P0 Stage F
IF visual phase AND visual_verification missing/malformed
                                                  → VISUAL_UNVERIFIED
IF current_phase.prior_phase_raw_id AND prior_phase_how_to_check populated AND
   reviewer_output.regression_verification missing/malformed
                                                  → REGRESSION_UNVERIFIED  ← P1 Stage D
IF blocking_issues array is NOT empty            → ERR_VALIDATION_FAILED
IF integration_tests_passing != true             → ERR_VALIDATION_FAILED
IF visual_verification ∈ {fail, cannot_verify}   → ERR_VALIDATION_FAILED
IF behavioral_verification.verdict ∈ {fail, cannot_verify}
                                                  → ERR_VALIDATION_FAILED   ← replaces the legacy
                                                                              ``not phase_intent_validated``
                                                                              trigger removed in P0 Stage F
IF regression_verification.verdict ∈ {fail, cannot_verify} OR
   regression_verification.prior_phase_how_to_check_followed is False
                                                  → ERR_REGRESSION_PRIOR_PHASE  ← P1 Stage D (sole failing dim)
                                                    or ERR_VALIDATION_FAILED    ← coexisting failures
ELSE                                             → PASS
```

**Non-`reviewer_retries`-consuming verdicts (single pooled counter, cap 2 across all three before escalation — P1 Stage D):**
- `VISUAL_UNVERIFIED` / `BEHAVIORAL_UNVERIFIED` / `REGRESSION_UNVERIFIED` → `reviewer_unverified_retries`

All three contract-shape verdicts re-invoke the reviewer with a verdict-specific instruction recorded in `phase_state.json` under the pooled `unverified_instruction` field. The reviewer's main retry budget (`reviewer_retries`) is preserved so a legitimate code-quality rejection on the next pass can still drive the 3-pass attribution routing. Pooling replaces the prior per-flavour counters — sprawl is the bug the consolidation eliminates now that a third contract-shape verdict exists.

**`ERR_REGRESSION_UNVERIFIED` (P1 Stage D — contract-shape failure):** The phase carries `prior_phase_raw_id` AND `prior_phase_how_to_check` (resolver-populated when the most recent completed phase had a behavioural recipe) but the reviewer's `regression_verification` block is missing or malformed. Mirror of `ERR_BEHAVIORAL_UNVERIFIED`. Non-retry-consuming on the pooled `reviewer_unverified_retries` counter. Re-invokes the reviewer with the regression-specific instruction.

**`ERR_REGRESSION_PRIOR_PHASE` (P1 Stage D — content failure):** The reviewer ran the prior phase's `how_to_check` recipe and reported a regression: `verdict ∈ {fail, cannot_verify}` OR `prior_phase_how_to_check_followed is False`. Attribution `"impl"` (the new code broke the old feature); routes through the standard rejection path via `apply_reviewer_routing` and draws from `executor_reviewer_rejection_retries` like any other reviewer-driven executor rejection. Emitted in place of `ERR_VALIDATION_FAILED` when regression is the sole failing dimension (no behavioural rejection, no visual rejection, no pre-existing blocking_issues, integration tests passing).

**Stage D iterates exactly one phase back (N→N-1).** Full prior-phase iteration with recipe-output caching is deferred to P3 Stage B. The single-step design is bounded — one extra recipe per phase, linear runtime cost — and reuses the entire P0 Stage F+G machinery.

**On `ERR_VALIDATION_FAILED`:** Increment `reviewer_retries` in `phase_state.json`.

**Branching (pass-dependent):**
```
IF PASS                              → merge
IF FAIL AND pass == 1                → re-run executor with blocking_issues
IF FAIL AND pass == 2                → check attribution field:
                                        IF "plan" → re-run planner
                                        IF "impl" → re-run executor (final retry)
IF FAIL AND pass == 3                → escalation agent
```

**CONTRACT_FAILURE pre-check (RR-1):** *(renamed from `INFRA_FAILURE` 2026-06-02 — the "INFRA" label was a misnomer; see the rename note below.)*

`reviewer_gate.py` returns `"CONTRACT_FAILURE"` when `reviewer_output.json` is missing or unparseable — the reviewer session **ended without producing a usable verdict**, a breach of its output contract, rather than a rejection of the work. Crucially, this branch is reached whenever the session ends without a parseable output **for any reason** (a clean give-up OR an abort/crash/limit-hit): the `autodev-pipeline-signals` plugin's `agent_end` handler (`autodev/plugin/src/agent-end-handler.ts`) writes the `{agent}_output.done` sentinel **unconditionally** (no `event.success` guard) to unblock the poll backstop, so the sentinel's presence does **not** prove a clean final action — only that `agent_end` fired. The recovery is identical for give-up and abort, so the cause is **diagnostic, not routing**: the orchestrator self-heals by re-invoking the reviewer in a FRESH session with a self-contained corrective directive (delivered as the webhook `message=` by `_invoke_reviewer`):

```
IF gate_result == "CONTRACT_FAILURE":
    IF provider-rejected (defensive re-check)  → escalation (ERR_PROVIDER_REJECTED)
    reviewer_contract_retries += 1
    IF reviewer_contract_retries >= 3  → escalation (CONTRACT_FAILURE_SOFT_RETRY_EXHAUSTED)
    ELSE                               → set reviewer_retry_directive; re-invoke reviewer (fresh session)
```

Genuine transport/provider failures (case a) are **partly** peeled off upstream — stall detection (`_handle_stall_outcome`), dead-on-arrival (`_check_session_dead_on_arrival`), and provider-rejection (`_escalate_if_provider_rejected`, also re-checked defensively at the top of this branch). Aborts/crashes that match none of those heuristics still land here and recover identically (fresh session + directive), which is why "ambiguous → default to contract" is safe. There is **no readable give-up-vs-abort signal** today (the plugin only logs `event.success`); if one is added later it should only enrich the escalation message, never gate behaviour.

CONTRACT_FAILURE does NOT increment `reviewer_retries` — that counter is reserved for genuine LLM rejections. The `reviewer_contract_retries` soft-retry counter is preserved across `reset_execution()` and only zeroed by `reset_phase()`.

There is no model-health probe and no SSH recovery. Agent/model liveness is owned by the OpenClaw activity-stamp hooks — `startup_grace` / `no_first_activity` for a cold start, `stalled` for a mid-turn death (`poll_for_sentinel`) — which fire identically for cloud and local agents, so a genuinely dead model surfaces as "no activity" and is caught there rather than by a pre-invocation probe.

> **Historical note (retired 2026-06-01):** an earlier design branched on a local llama-server `/health` check and, when it reported "unhealthy", attempted an SSH restart of the GPU box (`recovery` section of `openclaw.json`) with a 10-minute cooldown and a `reviewer_infra_recovery_attempts` counter. That machinery was removed: all pipeline agents are cloud-routed (OpenRouter), so a local-GPU probe was inapplicable — in practice it false-negatived a healthy cloud reviewer (probing `127.0.0.1` while the model ran on the configured `llama-local` host) into a dead-end SSH path that escalated. See CHANGELOG.

---

### Mandatory Completion Artifacts

Two artifacts must be written by the executor **before** `executor_output.done` on every phase. Their presence is verified by the reviewer gate done-criteria pre-check. Write ordering is strict:

```
1. phases/{phase_raw_id}.md   ← phase archive (write first)
2. metrics.jsonl              ← append metrics row (write second)
3. executor_output.done       ← sentinel (write last)
```

**Phase archive — `phases/{phase_raw_id}.md`**

Written to `pipeline-project/.autodev/pipeline/phases/` (create directory if absent). Documents what was built so future agents and operators have per-phase history without reading git logs.

```markdown
# {phase_raw_id} — {goal from current_phase.json detail field}
**Completed:** {ISO 8601 UTC timestamp}
**Duration:** {elapsed if known, else "unknown"}
**Executor attempts:** {executor_retries + 1}
**Reviewer passes:** {reviewer_retries + 1}

## What was built
## Tests
## Files changed
## Files deleted
## Lessons
```

Read `phase_state.json` for `executor_retries` and `reviewer_retries`; default to 0 if absent.

**Metrics row — `metrics.jsonl`**

Append one JSON line to `pipeline-project/.autodev/pipeline/metrics.jsonl`:

```json
{"ts": "<ISO 8601 UTC>", "phase": "<phase_raw_id>", "goal": "<detail from current_phase.json>", "executor_attempts": <int>, "executor_self_failures": <int>, "executor_reviewer_rejections": <int>, "reviewer_passes": <int>, "blame_fires": 0, "escalations": 0, "duration_seconds": null, "skill_used": "<discipline name or null>", "escalation_resets": <int>, "nuclear_resets": <int>, "reviewer_unverified_retries": <int>, "reachability_summary": <obj|null>, "reset_log": [<entries>]}
```

- `executor_attempts` = `executor_self_failure_retries + executor_reviewer_rejection_retries + 1` (P0 Stage H — lifetime, sourced from `phase_state.json`; reflects total attempts across reviewer-driven mid-phase resets)
- `executor_self_failures` = `executor_self_failure_retries` (P0 Stage H additive breakdown; default 0 for pre-Stage-H rows)
- `executor_reviewer_rejections` = `executor_reviewer_rejection_retries` (P0 Stage H additive breakdown; default 0 for pre-Stage-H rows)
- Invariant: `executor_attempts == executor_self_failures + executor_reviewer_rejections + 1` (the +1 is the initial attempt)
- `reviewer_passes` = `reviewer_retries + 1`
- `blame_fires` / `escalations`: 0 unless definitive evidence otherwise
- `duration_seconds`: `null` unless computable from timestamps
- `skill_used`: discipline name string from `phase_state.json → skill_injected` (e.g. `"core-logic"`, `"infra-config"`), or `null` if no skill was injected. Written by the orchestrator's canonical post-merge row; read from `phase_state.json` before it is deleted at phase completion.
- **Phase 3 — per-phase pain signals** (all read from `phase_state.json` at row-write time, on the reviewer-PASS path before that file is deleted on advance; additive, default-safe):
  - `escalation_resets` / `nuclear_resets` / `reviewer_unverified_retries`: the per-phase reset/contract-retry counters (default `0`).
  - `reset_log`: snapshot of the operator-reset audit trail (`[]` when none) — captured into the durable row because the live `reset_log` is wiped on phase advance.
  - `reachability_summary`: compact `{kind, count?, files?, command?/reason?}` (or `null` when no advisory drained that phase) — stashed onto `phase_state.last_reachability_summary` by `_emit_reachability_advisory` before it removes the advisory file, then surfaced here.

(The orchestrator's actual `canonical_row` also carries the W1-G token-accounting fields — `planner_tokens` / `executor_tokens` / `reviewer_tokens` / `cost_total` — and `blame_verdict`; they are omitted from this representative example.)

The reviewer gate verifies that the last non-empty line of `metrics.jsonl` contains `phase_raw_id` — this confirms the row was written for the current phase, not a prior one.

> **Metrics write authority — dual-write design:** The executor writes an interim row to `metrics.jsonl` during execution. This row is required for the reviewer gate done-criteria pre-check (which verifies the row exists before running validation). After the reviewer gate passes and the merge commit completes, the **orchestrator** writes a single canonical row for the phase, stripping any prior rows for the same `phase_raw_id` written by the executor. The orchestrator's canonical row is the authoritative record — it uses final counts from `pipeline_state.json` and computes `duration_seconds` from the recorded `phase_start_time`. On phases where the executor was retried multiple times, the executor may have written multiple rows; the orchestrator deduplication step ensures exactly one row per phase in the final `metrics.jsonl`.

---

## 8. ~~Traffic Cop~~ Retired 2026-03-04

Replaced by direct llama-server endpoint at `http://<llama-server-host>:11434`. All local model requests (executor, reviewer, escalation) now go directly to llama-server. See §1 Infrastructure Topology.

---

## 9. OpenClaw Configuration

### `openclaw.json` — Complete Structure

```json
{
  "agents": {
    "defaults": {
      "skipBootstrap": true,
      "model": {
        "primary": "anthropic/claude-sonnet-4-6"
      },
      "models": {
        "anthropic/claude-sonnet-4-6": {
          "alias": "sonnet",
          "params": {
            "cacheRetention": "short"
          }
        }
      }
    },
    "list": [
      {
        "id": "planner",
        "workspace": "~/.openclaw/workspace-planner"
      },
      {
        "id": "executor",
        "workspace": "~/.openclaw/workspace-executor",
        "model": "llama-local/qwen3-coder-next"
      },
      {
        "id": "reviewer",
        "workspace": "~/.openclaw/workspace-reviewer",
        "model": "llama-local/qwen3.5-27b"
      },
      {
        "id": "escalation",
        "workspace": "~/.openclaw/workspace-escalation",
        "default": true,
        "tools": {
          "allow": ["read", "write"],
          "deny": ["edit", "apply_patch", "exec", "process", "browser"]
        }
      }
    ]
  },
  "models": {
    "providers": {
      "llama-local": {
        "baseUrl": "http://<llama-server-host>:11434/v1",
        "apiKey": "no-key",
        "api": "openai-completions",
        "models": [
          {
            "id": "qwen3-coder-next",
            "name": "Qwen3-Coder-Next",
            "reasoning": false,
            "input": ["text"],
            "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
            "contextWindow": 65536,
            "maxTokens": 16384,
            "params": {
              "temperature": 0.7,
              "top_p": 0.8,
              "top_k": 20,
              "min_p": 0.0,
              "repeat_penalty": 1.05
            }
          },
          {
            "id": "qwen3.5-27b",
            "name": "Qwen3.5-27B (Q6_K)",
            "reasoning": false,
            "input": ["text"],
            "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
            "contextWindow": 65536,
            "maxTokens": 16384,
            "params": {
              "temperature": 0.6,
              "top_p": 0.95,
              "top_k": 20,
              "min_p": 0.0,
              "presence_penalty": 0.8
            }
          },
          {
            "id": "darkqwen3.5-27b",
            "name": "DarkQwen3.5-27B",
            "reasoning": false,
            "input": ["text"],
            "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
            "contextWindow": 65536,
            "maxTokens": 16384,
            "params": {
              "temperature": 0.6,
              "top_p": 0.95,
              "top_k": 20,
              "min_p": 0.0,
              "presence_penalty": 0.8
            }
          },
          {
            "id": "qwen3.5-9b",
            "name": "Qwen3.5-9B (Q8_0)",
            "reasoning": false,
            "input": ["text"],
            "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
            "contextWindow": 65536,
            "maxTokens": 16384,
            "params": {
              "temperature": 0.6,
              "top_p": 0.95,
              "top_k": 20,
              "min_p": 0.0,
              "presence_penalty": 0.8
            }
          },
          {
            "id": "darkqwen3.5-9b",
            "name": "DarkQwen3.5-9B",
            "reasoning": false,
            "input": ["text"],
            "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
            "contextWindow": 65536,
            "maxTokens": 16384,
            "params": {
              "temperature": 0.6,
              "top_p": 0.95,
              "top_k": 20,
              "min_p": 0.0,
              "presence_penalty": 0.8
            }
          }
        ]
      }
    }
  },
  "hooks": {
    "enabled": true,
    "token": "<shared-secret>",
    "allowedAgentIds": ["planner", "executor", "reviewer", "escalation"],
    "allowRequestSessionKey": true,
    "allowedSessionKeyPrefixes": ["pipeline:"],
    "defaultSessionKey": "pipeline:default"
  },
  "bindings": [
    {
      "agentId": "escalation",
      "comment": "Route all inbound Signal messages exclusively to the escalation agent. Only escalation has messaging rights in the pipeline.",
      "match": { "channel": "signal" }
    }
  ]
}
```

> **`defaultSessionKey` prefix requirement:** The `defaultSessionKey` must satisfy the `allowedSessionKeyPrefixes` restriction. The original value `"hook:pipeline"` does not match the `"pipeline:"` prefix and causes a gateway startup validation error.

### Key Configuration Notes

**Per-agent workspaces:** Do NOT use `agents.defaults.workspace` — each agent must be explicit to avoid collisions with any default agent.

**`agents.defaults.skipBootstrap: true`:** Applied globally to all pipeline agents via `agents.defaults`. Without this, OpenClaw reports `BOOTSTRAP.md` as missing on first run and attempts the identity ritual, overwriting hand-crafted `IDENTITY.md` / `SOUL.md` / `USER.md`. With `skipBootstrap: true`, the warning is suppressed and pre-seeded files are used as-is.

**Prompt caching:** Controlled via `agents.defaults.models["anthropic/claude-sonnet-4-6"].params.cacheRetention`. Set to `"short"` (5-minute TTL) for the Anthropic model used by planner and escalation agents. Local model (Qwen3.5-27B) has zero API cost, so caching is irrelevant — its cost fields are set to `0` in the `models.providers` registration. Files that should benefit from cache reuse: `SOUL.md`, `USER.md`, `IDENTITY.md`, `AGENTS.md`, `TOOLS.md` — all stable between invocations, never updated mid-run. Batch planner/escalation invocations within the 5-minute cache window where possible.

**Per-agent tool policies:** OpenClaw supports per-agent `tools.allow`/`tools.deny` with individual tool names and `group:*` shorthand. This is granular, not all-or-nothing.

**Webhook `agentId` routing:** The `POST /hooks/agent` endpoint accepts an `agentId` field in the request body, which routes the request to the matching agent from `agents.list[]`. This is a built-in feature — no custom `hooks.mappings` entry is needed. The `hooks.allowedAgentIds` array restricts which agents can be targeted via webhook.

**Local model registration:** All local models are registered under the `llama-local` provider in `models.providers`. This gives OpenClaw the provider prefix, endpoint, and cost/context metadata. When invoking via webhook, use the `provider/model` format (e.g., `llama-local/qwen3-coder-next`). All local models are registered at `contextWindow: 65536` (64K) in OpenClaw config. Server-side `--ctx-size` by model (as of 2026-03-10): `qwen3-coder-next` 65536 (bumped from 32K), `qwen3.5-27b` / `darkqwen3.5-27b` 65536, `qwen3.5-9b` / `darkqwen3.5-9b` 32768. Active role assignments: executor → `qwen3-coder-next`, reviewer → `qwen3.5-27b`, escalation → `qwen3.5-27b`. Registered but not currently utilized: `darkqwen3.5-27b`, `qwen3.5-9b`, `darkqwen3.5-9b`. Per-model sampling parameters are set via `params` blocks in the model registration. Qwen3.5 family uses `presence_penalty`; Qwen3-Coder-Next uses `repeat_penalty`. Do not cross-apply — they are different algorithms.

**`apiKey: "no-key"` is required on the `llama-local` provider** — even though llama-server has no authentication. Without this field, OpenClaw falls back to the `anthropic:default` auth profile, which injects an Anthropic API key into the request header. The local server rejects this as an auth failure, triggering a silent cloud fallback to `anthropic/claude-sonnet-4-6`. The value `"no-key"` is a placeholder that satisfies the provider auth requirement without sending a real credential. This is the root cause of Signal DM sessions landing on Sonnet 4.6 despite correct per-agent model config.

**Default agent:** The escalation agent must be marked `"default": true` in its `agents.list` entry. Without this, the first agent in the list (`planner`) becomes the implicit default. Any unrouted inbound Signal message would land in a planner session — the wrong agent. Explicit `default: true` on escalation, combined with the `bindings` entry below, provides two layers of defence.

**Signal bindings:** A top-level `"bindings"` array hard-wires Signal channel messages to the escalation agent regardless of default-agent configuration. Structure: `{"agentId": "escalation", "match": {"channel": "signal"}}`. This is a second layer — even if the default agent ever changed, Signal DMs still route exclusively to escalation.

**Memory/vector index:** Disabled for all pipeline agents. Fresh context per phase is by design; agents read explicit JSON files, not memory search.

**Hook authentication:** Configured via `hooks.token`. Every webhook POST must include this token as `Authorization: Bearer <token>` or `x-openclaw-token: <token>` header. Query-string tokens are rejected (`?token=...` returns 400). Use a dedicated hook token — do not reuse gateway auth tokens.

**Session key policy:** `hooks.allowRequestSessionKey: true` is required because the orchestrator passes a unique `sessionKey` per agent invocation (e.g., `pipeline:phase-N:planner`). Without this, the field is silently rejected and all hooks share the `defaultSessionKey`. The `allowedSessionKeyPrefixes: ["pipeline:"]` restriction ensures only pipeline-prefixed keys are accepted.

---

## 10. Infrastructure

### File Paths

| File | Location | Purpose |
|---|---|---|
| `pipeline.lock` | Working directory on Pi | Concurrency lock via `fcntl.flock`, PID + timestamp as metadata |
| `pipeline_state.json` | Working directory on Pi | Orchestrator state, atomically written |
| `current_phase.json` | Shared workspace (via symlink) | Phase detail, category, exit_criteria |
| `phase_state.json` | Shared workspace | Retry counts, blame context, error codes |
| `planner_output.json` | Shared workspace | Planner deliverable |
| `planner_output.done` | Shared workspace | Planner sentinel |
| `executor_output.json` | Shared workspace | Executor deliverable |
| `executor_output.done` | Shared workspace | Executor sentinel |
| `reviewer_output.json` | Shared workspace | Reviewer deliverable |
| `reviewer_output.done` | Shared workspace | Reviewer sentinel |
| `escalation_output.json` | Shared workspace | Human resume command logged by escalation agent |
| `escalation_output.done` | Shared workspace | Sentinel for human resume command |
| `escalation_failed.json` | Shared workspace | Written when escalation delivery fails |
| `suggestions.md` | Project directory | Accumulated reviewer suggestions |
| `lessons.md` | Project directory | Blame attribution LLM call log |
| `pipeline_stop_requested` | Project directory (symlink target) | Stop sentinel written by `POST /api/stop`; consumed and deleted by `_check_stop_requested()` on next loop iteration |

> **Workspace-relative paths:** Agents access these files via the workspace-relative path `pipeline-project/filename.json`. The absolute paths listed in this table are from the orchestrator's perspective. Agents must not use absolute paths for writes due to the workspace sandbox restriction.

### Symlink Pattern

```bash
ln -sfn /path/to/project ~/.openclaw/pipeline-project
```

Updated by orchestrator at project start (one-time per project). All agents already point here via absolute paths in their config. Shared project files (JSON outputs, sentinels, source code) live at this symlink target — not in any agent workspace.

### Agent Workspaces (Separate Per Role)

Each agent gets its own `AGENTS.md`, `SOUL.md`, `IDENTITY.md`, `USER.md`, `TOOLS.md` — different behavioral constraints per role require different files.

```
~/.openclaw/workspace-planner/
~/.openclaw/workspace-executor/
~/.openclaw/workspace-reviewer/
~/.openclaw/workspace-escalation/
```

### Workspace Write Sandbox and Symlinks

OpenClaw sandboxes each agent's `write` tool to its declared workspace directory. Writes targeting absolute paths outside the workspace silently report success but discard the file — this is platform behavior, not configurable.

Each agent workspace contains a symlink providing write access to the shared project directory:

```
~/.openclaw/workspace-planner/pipeline-project    → ~/.openclaw/pipeline-project
~/.openclaw/workspace-executor/pipeline-project   → ~/.openclaw/pipeline-project
~/.openclaw/workspace-reviewer/pipeline-project   → ~/.openclaw/pipeline-project
~/.openclaw/workspace-escalation/pipeline-project → ~/.openclaw/pipeline-project
```

The outer symlink `~/.openclaw/pipeline-project` resolves to the actual project directory. When the orchestrator swaps projects (via `update_symlink`), all four workspace symlinks follow automatically.

Agent `AGENTS.md` files instruct agents to use the workspace-relative symlink `pipeline-project/` to reach the target repo. **Pipeline state and sentinels** (`planner_output.*`, `phase_state.json`, `*_output.done`, `phases/`, `metrics.jsonl`, etc.) are under `pipeline-project/.autodev/pipeline/`, not the project root. Do not use the absolute path `~/.openclaw/pipeline-project/` in agent write instructions.

**Orchestrator symlink reconcile (executor and reviewer):** Immediately before each executor and reviewer webhook, the orchestrator calls `_verify_symlinks_consistent(pipeline_state["project_path"], self.update_symlink)`. If both outer `pipeline-project` symlinks (under `AUTODEV_PIPELINE_ROOT` and `OPENCLAW_ROOT`) already resolve to `project_path`, the call is a no-op. If they diverge, it invokes `update_symlink` to repoint both to `project_path` (same **Policy A — state wins** model as `POST /api/resume-orchestrator`), logs `[RECONCILE]` on the attempt and on successful confirmation, and re-verifies. Empty `project_path`, failed `update_symlink`, or persistent mismatch yields `[WARN]` or `[ERROR]` and `False`; the main loop still proceeds to the webhook (the reconcile is best-effort; operators should treat persistent `[WARN]` after `[RECONCILE]` as a signal to inspect symlinks and disk permissions). **`update_symlink` is transactional (T6.5):** it stages both links at unique temp names and commits them with atomic `os.replace`, rolling the first back to its prior target if the second fails — so the AUTODEV-side and OpenClaw-side links are never left permanently divergent (replacing the prior two non-atomic `ln -sfn` calls).

### Git Operations

**Strict timing rule:** No git operations during planner, executor, or reviewer turns — agents write only to shared workspace.

Git operations happen only after reviewer gate passes:

```bash
git add .
git commit -m "phase({phase_id}): {goal_summary}"
git checkout main
git merge phase/N --no-ff
git tag phase-N-complete
```

**Branch creation:**
```bash
git checkout phase/N 2>/dev/null || git checkout -b phase/N
```
This handles both first-run and restart cases safely.

**On RESTART PHASE — working tree handling depends on restart reason:**

| Scenario | Working Tree Action |
|---|---|
| Failed-to-complete (timeout/crash) | `git reset --hard HEAD` then `git clean -fd` |
| Reviewer-rejection | Leave as-is — executor's files are the starting point |

**Merge conflict:** → escalation agent immediately; do not attempt auto-resolve. After manual resolution via SSH, use the `PROCEED` resume command to trigger post-merge cleanup (tag, roadmap update, working file clear). See § Escalation Agent > Resume Commands.

### Sentinel Pattern

1. **Pre-Webhook Cleanup**: Before transitioning state to `WAITING_FOR_SENTINEL` and before POSTing the webhook (including on retries), the orchestrator MUST explicitly delete the target `.done`, target `.json`, and target `{agent}_activity.stamp` files (`missing_ok=True`). The workspace must be completely clear of prior outputs before the agent is invoked.
2. **Activity bootstrap**: The orchestrator immediately writes a fresh empty `{agent}_activity.stamp` for planner, executor, and reviewer attempts. This seed is the first stall clock tick; OpenClaw hooks refresh it on model/tool activity.
3. Agent writes output JSON first
4. Agent writes sentinel file (`.done`) as final act
5. Orchestrator polls for `.done` file — not the JSON itself. Must use a simple `time.sleep()` loop (e.g., checking every 2 seconds).
6. `JSON present + sentinel present` = safe to read
7. `JSON present without sentinel` = agent still writing, do not parse

Sentinel is a soft dependency on agent instruction-following — requires careful AGENTS.md guidance. Orchestrator enforces long per-agent infrastructure backstop timeouts (4500 s / 75 min per agent), but normal completion is driven by the `agent_end` plugin hook writing `.done`.

**Tier A stall detection (updated 2026-05-07):** For planner, executor, and reviewer, the orchestrator uses `sentinel_poller.poll_for_sentinel()` with `stall_detection_path={agent}_activity.stamp` and an agent-specific `stall_threshold_seconds`. The orchestrator seeds the stamp at attempt start so a missing first OpenClaw hook still becomes detectable; `autodev-pipeline-signals` then refreshes the stamp on `model_call_started`, `model_call_ended`, and `after_tool_call`. If the stamp mtime goes quiet beyond the threshold with no sentinel, the poll exits early (treated identically to timeout/stop → existing retry path). Defaults: a 300 s stall threshold for all three agents (override `AUTODEV_STALL_TIMEOUT_PLANNER`, `AUTODEV_STALL_TIMEOUT_EXECUTOR`, `AUTODEV_STALL_TIMEOUT_REVIEWER`), plus a separate 600 s startup-grace window before first activity (override `AUTODEV_STARTUP_GRACE_PLANNER`, `AUTODEV_STARTUP_GRACE_EXECUTOR`, `AUTODEV_STARTUP_GRACE_REVIEWER`).

Additionally, the orchestrator records a `min_sentinel_mtime` (wall-clock time captured immediately before `cleanup_output_files()`) and passes it to the poller. If a `.done` sentinel is found with an mtime older than this value, it is discarded as belonging to an orphaned prior session — this prevents stale sentinels from consuming retry budget while the reset-cleaned working tree causes an inevitable gate failure.

The same "ignore this `.done`, keep waiting" mechanism is extended by an optional `sentinel_acceptor` predicate (Layer 2 — context-overflow discarded-verdict race). The three phase-agent poll sites pass `Orchestrator._make_overflow_aware_acceptor(role, session_key, attempt_start)`, which HOLDS a fresh `.done` written by a **recoverable context-overflow** turn (the session's last assistant row is `stopReason:"error"` with a `"Context overflow …"` errorMessage and no verdict is on disk yet) until OpenClaw's compact-and-resume lands the real verdict — closing a race where the gate would otherwise read a missing verdict, escalate, and discard the verdict the resumed session produces moments later. The predicate accepts immediately for a fresh, parseable `{role}_output.json`, a spent hold budget (`AUTODEV_OVERFLOW_HOLD_BUDGET`, default 900 s), or **any** non-overflow termination (so the common path is byte-identical); the hold stays bounded by the stall / startup-grace / 75-min backstop timers and emits one `sentinel_overflow_hold` event per episode. A raising acceptor fails open (accepts). The completion-review poll is deliberately not wired.

The `stop_sentinel_path` parameter is checked on every loop iteration — if the file exists the poll returns `False` immediately, and the caller's next main-loop iteration calls `_check_stop_requested()` to consume the sentinel and transition to `STOPPED`.

**Executor → Reviewer handoff:** After the executor gate passes, the orchestrator transitions straight to the reviewer. (Historical — OB-6: when pipeline agents ran on the local GPU, a `wait_for_model_stable()` poll guarded against an HTTP 500 cascade from the traffic cop swapping models mid-eviction. That wait was retired with the traffic-cop machinery — all pipeline agents are cloud-routed (OpenRouter), so there is no local GPU model swap between the executor and reviewer to wait on.)

Sentinel files cleared by orchestrator at phase start alongside working JSONs.

### Session Key Naming

**Dynamic Session Keys Required:**
For all agent invocations, dynamically append the current attempt/retry counter as a suffix.

```
pipeline:phase-N:{raw_id}:planner-attempt-1
pipeline:phase-N:{raw_id}:executor-attempt-1
pipeline:phase-N:{raw_id}:reviewer-attempt-1
```

> **Note:** The key includes `{raw_id}` (e.g., `CORE-2`) to avoid session key collisions between phases with the same phase number across different projects. The `executor-fallback-attempt-N` variant shown in earlier documentation is not used in the current implementation — all executor retries (both automatic and reviewer-rejection retries) use `executor-attempt-N` with the current `executor_retries + 1` counter. The `attempt_label` field visible in orchestrator logs (e.g., `"Local"`) is a display string for log output, not part of the session key itself.

New attempt → new suffix in key → completely fresh session, zero prior context loaded. This prevents VRAM overflow on local models.

### Heartbeat Cron

Runs on Pi. Every 30 minutes (tunable to 15).

- **Crash recovery, restart-only:** The cron never SIGTERMs a live orchestrator. When the pipeline lock is held (orchestrator alive) it logs status and exits — no intervention. The earlier SIGTERM-on-15-min-`WAITING_FOR_SENTINEL` check was removed: `last_action_timestamp` is stamped once on entering `WAITING_FOR_SENTINEL` and not updated until the agent finishes, so a legitimately long phase would have been killed mid-work.
- The Heartbeat Cron acts strictly as a safety net. It intervenes only when the lock is **free** (orchestrator dead) AND the state claims active work whose `last_action_timestamp` is older than `STALE_FLIGHT_THRESHOLD_MINUTES` (3 min) — then it restarts the orchestrator. A fresh-but-dead state is left for the next cycle.

```
1. Attempt fcntl.flock(pipeline.lock, LOCK_EX | LOCK_NB)
2. IF lock acquisition FAILS (EWOULDBLOCK):
     Orchestrator is alive
     Read pipeline_state.json
     IF status == WAITING_FOR_SENTINEL
        AND elapsed > 15 min threshold → kill PID, loop back to step 1
     ELSE → do nothing (alive + healthy)
3. IF lock acquisition SUCCEEDS:
     Orchestrator is dead (crashed or rebooted)
     Read pipeline_state.json
     Query local llama-server (http://<llama-server-host>:11434) for RESUME/WAIT/NOTIFY decision
       → RESUME  : restart orchestrator.py --project-path <project_path from state>
       → WAIT    : log and exit (pipeline is correctly paused: WAITING_FOR_HUMAN, HALTED_SILENT, etc.)
       → NOTIFY  : send raw Signal notification, do not restart
     IF model unreachable → send Signal notification, do not restart (fail safe, not fail open)
4. Recovery re-invokes last recorded webhook action
   — does NOT re-run gate checks that already passed
   — project_path is re-passed from pipeline_state.json (B4) so symlink is correctly set
```

**B7 Model Decision Rules (system prompt):**
- `RESUME` — pipeline_status is `RUNNING` or `WAITING_FOR_SENTINEL` and lock is free (orchestrator confirmed dead)
- `WAIT` — pipeline_status is `WAITING_FOR_HUMAN`, `HALTED_SILENT`, `BLOCKED`, `PIPELINE_COMPLETE`, or `STOPPED`; do not intervene
- `NOTIFY` — state does not clearly match RESUME or WAIT; alert human
- Unexpected output → treated as NOTIFY (conservative default)

### Session Cleanup Cron

Runs on Pi, once daily.

- Prunes OpenClaw session JSONs directly and deletes associated `.jsonl` files
- Deletes any session older than 30–60 days
- Dead sessions don't affect runtime (never loaded), but disk accumulation is real over time
- Do NOT delete escalation agent sessions automatically — may be needed for audit trail
- **Log Rotation**: Includes steps to rotate/truncate `heartbeat.log` (keeping up to ~5MB) to prevent SD card exhaustion

### Audit Archive

```
$OPENCLAW_ROOT/pipeline-audit/{project-name}/phase-N/
```

Archive written **before** clearing working files. Project name in path prevents cross-project conflicts. Archive failure is a non-blocking informational escalation only (logs a warning to stdout/log file, does NOT trigger the Signal webhook).

---

## 11. Output Schemas

Complete JSON schemas for all pipeline data files. Schemas in §3–§6 define agent output contracts. Additional state files:

### `current_phase.json`

> ⚠️ AMBIGUITY: `phase_number` field name is inferred; source describes "phase N" context without naming this field explicitly.

```json
{
  "phase_number": { "type": "integer" },
  "detail": { "type": "string", "description": "Phase description from roadmap" },
  "category": { "type": "string" },
  "raw_id": { "type": "string", "description": "Phase identifier (e.g. 'CORE-E1')" },
  "status": { "type": "string", "enum": ["PENDING", "BLOCKED"] },
  "exit_criteria": {
    "type": "array",
    "items": { "type": "string" },
    "description": "Body text of every `> ...` line under the phase header (preserved for back-compat with reviewer-gate consumers)."
  },
  "behavioral_verification": {
    "type": ["object", "null"],
    "description": "Stage D — the per-phase Behavioral Verification block. Null when absent (transitional case for in-flight pre-P0 runs; preflight refuses to stage projects whose roadmaps are missing the block).",
    "properties": {
      "user_observable": { "type": "string", "description": "One sentence in plain English describing what a human can do/see after this phase." },
      "how_to_check": { "type": "string", "description": "Concrete procedure the reviewer follows to exercise the artifact (route, command, file, etc.)." },
      "failure_language": { "type": "string", "description": "One sentence the executor's retry feedback and escalation advisory surface when verification cannot be completed. P1 Stage G1: the escalation advisory surfaces this on EVERY escalation that carries it — it is fed to the advisory LLM whenever present, not only on reviewer-rejection escalations (the old reviewer_retries >= 2 gate)." }
    }
  },
  "entry_criteria": { "type": "string", "description": "Stage D — body of the `**Entry Criteria:**` markdown block, verbatim." },
  "exit_criteria_block": { "type": "string", "description": "Stage D — body of the `**Exit Criteria:**` markdown block. Distinct from the legacy `exit_criteria` list above." },
  "tdd_requirements": {
    "type": "array",
    "items": {
      "type": "object",
      "properties": {
        "file": { "type": "string", "description": "Path to the test file." },
        "description": { "type": "string", "description": "What this test validates." }
      }
    },
    "description": "Stage D — parsed `- \\`{test_file}\\`: {description}` bullets under `**TDD Requirements:**`."
  },
  "done_criteria": {
    "type": "array",
    "items": { "type": "string" },
    "description": "Stage D — `- [ ] ...` checkbox bodies under `**Done Criteria:**`."
  },
  "verification_path": { "type": "string", "description": "Stage D — absolute path to the project-level verification.md (typically `<project_root>/verification.md`). Agents read this doc to understand the project type, entry point, public surface, and verification stack." }
}
```

### `phase_state.json`

```json
{
  "planner_retries": { "type": "integer", "default": 0 },
  "executor_retries": { "type": "integer", "default": 0, "description": "Incremented by reset_execution('auto') — automatic retry path only." },
  "reviewer_retries": { "type": "integer", "default": 0, "description": "Genuine LLM rejection counter. Zeroed by reset_execution() and reset_phase(). Cap: 3 passes before escalation." },
  "reviewer_rejected": { "type": "boolean", "default": false, "description": "Set by reviewer gate on ROUTE_EXECUTOR. Cleared by reset_execution()." },
  "reviewer_contract_retries": { "type": "integer", "default": 0, "description": "Incremented on every reviewer-gate CONTRACT_FAILURE (session ended without a parseable reviewer_output.json — give-up or abort/crash). Soft retry: re-invoke the reviewer in a FRESH session with a one-shot reviewer_retry_directive. Cap: 3 → escalation (CONTRACT_FAILURE_SOFT_RETRY_EXHAUSTED). Zeroed by reset_phase() only — NOT by reset_execution(). Distinct from reviewer_retries (which tracks genuine LLM rejections). Renamed from reviewer_infra_retries 2026-06-02." },
  "reviewer_retry_directive": { "type": "string|null", "description": "One-shot corrective instruction delivered to the NEXT reviewer session as the webhook message= (overriding the default), written by the CONTRACT_FAILURE branch and the UNVERIFIED handler and consumed+cleared by _invoke_reviewer. The reviewer's single directive channel; absent when no retry directive is pending." },
  "planner_output_preserved": { "type": "boolean", "default": false, "description": "RR-2: Set to true atomically after planner gate passes. Enables crash-recovery skip: if current_agent=planner, planner_retries=0, and this flag is true, the orchestrator skips planner re-invocation and advances directly to executor. Cleared by ROUTE_PLANNER (intentional reviewer-reject re-run) and by reset_phase(). Never set by reset_execution()." },
  "escalation_resets": { "type": "integer", "default": 0, "description": "Incremented by RESET_PHASE and RESET_EXECUTION resume commands. Cap: 3. NOT zeroed inside reset_phase() — only zeroed when roadmap genuinely advances to a new phase. Distinct from executor_retries." },
  "nuclear_resets": { "type": "integer", "default": 0, "description": "P1 Stage G2. Incremented by the NUCLEAR_RESET command via nuclear_reset_phase(). Cap: 2, independent of escalation_resets. Preserved inside reset_phase() (like escalation_resets) so the cap accumulates; zeroed only on genuine phase advance. The dashboard shows the nuclear-reset button only when escalation_resets >= 3 and hides it at nuclear_resets >= 2." },
  "blame_context": { "type": "string", "description": "Appended by blame attribution" },
  "last_error_code": { "type": "string", "description": "Distinct codes for parse vs. structural failures" },
  "skill_injected": { "type": "string|null", "description": "Discipline name of the phase-prefix skill injected for the most recent agent turn (e.g. 'core-logic', 'infra-config'). null if no skill applied (prefix unmapped, phase_raw_id empty, source file missing, or kill switch suppressed injection). Written atomically by _record_injected_skill() immediately after each inject_skill() call." },
  "skill_agent": { "type": "string", "description": "Agent role for which the skill_injected value was recorded ('planner', 'executor', or 'reviewer'). Always written alongside skill_injected." },
  "escalation_trigger_reason": { "type": "string", "description": "Internal, possibly blame-framed reason the pipeline transitioned to WAITING_FOR_HUMAN (e.g. the impl-blame-cap string). Written atomically immediately before transition_state('WAITING_FOR_HUMAN', ...) at all three escalation trigger points. P1 Stage G1: NO LONGER the UI command-panel headline — it is demoted into the panel's collapsible 'Internal reason' disclosure (and the audit log). Preserved until phase_state.json is deleted at phase completion." },
  "escalation_headline": { "type": "string", "description": "P1 Stage G1 — clean, deterministic, non-blame headline for the escalation panel (e.g. 'Phase REND-E1 needs your input'). Derived from the phase id by _clean_escalation_headline(), so it can never echo the blame-cap string. Written alongside escalation_trigger_reason at every escalation trigger; served by GET /api/state. The UI renders the LLM advisory summary when escalation_advisory_status == 'ready', else this headline." },
  "last_phase_outcome": { "type": "string|null", "description": "Phase 3 — terminal phase outcome: 'completed' / 'escalated' / 'nuclear_reset' (absent while in-progress). Set by _record_phase_outcome ('completed', on the reviewer-PASS path right after the metrics row and before the audit archive copies phase_state) and directly at the single escalation chokepoint + the repo-init escalation block ('escalated') and in nuclear_reset_phase ('nuclear_reset'). Preserved across reset_phase() so a nuclear reset's outcome survives the reset it delegates to; cleared on genuine phase advance. DURABILITY: 'completed' is wiped when phase_state.json is deleted on advance — the canonical metrics row + phase_complete event are its durable record; 'escalated'/'nuclear_reset' persist live because those states do not advance." },
  "last_reachability_summary": { "type": "object|null", "description": "Phase 3 — compact copy of the executor reachability advisory ({kind, count?, files?, command?/reason?}), stashed by _emit_reachability_advisory before it removes executor_advisory_detail.json so the canonical metrics row (written later on the reviewer-PASS path, after that file is gone) can surface it as reachability_summary. Absent when no advisory drained this phase." }
}
```

> `phase_state.json` is deleted at phase completion and re-created lazily on first counter increment. On re-creation the fallback init includes `escalation_resets: 0` and `nuclear_resets: 0` — so both counters genuinely reset only when a new phase begins, never on a phase reset.

**P1 Stage G1 — escalation advisory de-blame.** The pre-escalation LLM advisory (`_generate_escalation_advisory`) is grounded only in user-facing failure data: `failure_context`, the project's `failure_language`, and the retry counts. The blame-framed keys `escalation_trigger_reason` and `prior_blame_attributions` are **not** sent to the advisory LLM, so the summary cannot parrot internal blame-attribution jargon. The `failure_language` block is included whenever `failure_context` carries it — regardless of `reviewer_retries` — so executor-self-failure escalations surface the project's user-voice copy too (previously gated on `reviewer_retries >= 2`). The advisory result is stored as `escalation_message` / `escalation_recommended_action`; the clean `escalation_headline` is what the UI shows as the panel headline (the advisory summary when `escalation_advisory_status == "ready"`, the headline otherwise). `run_blame_attribution()` is unchanged — G1 governs only what the advisory is *fed* and what the UI *renders*.

**Advisory dispatch ordering + honest fallback (escalation loader).** The escalation panel renders only at `WAITING_FOR_HUMAN`, so all three escalation dispatch sites (reviewer, repo-init, crash handler) transition to `WAITING_FOR_HUMAN` with `escalation_advisory_status="generating"` **before** the ≤30 s `_generate_escalation_advisory()` call, via the shared `_generate_and_record_advisory(ps)` helper. This makes the `"generating"` loader actually visible — the dashboard shows the Ideas-chat pending loader + elapsed timer while the advisory is produced, then flips to it (`status="ready"`) on the next 3 s poll. The advisory dict is still produced before the escalation webhook message is built, so the operator Signal notification is unchanged. When the advisory hangs/fails (`None` → `status="fallback"`), `_generate_and_record_advisory` records a **deterministic, factual** `escalation_message` via `_compose_fallback_reason(ps)` — built from hard signals (`last_error_code`, the already-honest `escalation_trigger_reason`, and `failure_context.json`'s `failing_agent`/`gate_error_codes`/`attempt_number`) and **never** from the phase's `failure_language` (presenting that expected-failure description as observed reality is the fabrication that produced the misleading "blank white page" message). The UI fallback branch renders this `escalation_message`, falling back to a generic line only when it is empty.

**Counter reset matrix:**

| Counter | `reset_execution()` | `reset_phase()` | Phase complete (new phase) |
|---|---|---|---|
| `planner_retries` | — | ✓ zeroed | ✓ zeroed |
| `executor_retries` | — (auto only increments, does not zero) | ✓ zeroed | ✓ zeroed |
| `reviewer_retries` | ✓ zeroed | ✓ zeroed | ✓ zeroed |
| `reviewer_rejected` | ✓ cleared | ✓ cleared | ✓ cleared |
| `reviewer_contract_retries` | ✗ preserved | ✓ zeroed | ✓ zeroed |
| `planner_output_preserved` | — | ✓ cleared | ✓ cleared |
| `escalation_resets` | — | ✗ preserved | ✓ zeroed |
| `nuclear_resets` (P1 Stage G2) | — | ✗ preserved | ✓ zeroed |
| `reset_log` (P1 Stage G2) | — (appended) | ✗ preserved | ✓ cleared |
| `last_phase_outcome` (Phase 3) | — | ✗ preserved | ✓ cleared |

### `pipeline_state.json`

See § Orchestrator > `pipeline_state.json` Contents.

### `escalation_failed.json`

See § Escalation Agent > `escalation_failed.json`.

---

## 12. Error Classification

All error types and their retry counter behavior, consolidated in one place.

### Webhook POST Failures (Inner Retry Loop)

**Note:** Both webhook POST failures and Anthropic API infra failures (`429`, `5xx`) are handled via an **in-memory synchronous loop** directly surrounding the webhook invocation. They do not persist an `infra_retries` counter to `phase_state.json`. If this loop exhausts all attempts, it routes directly to the escalation agent.

**Webhook Return Protocol:**
The `invoke_agent_webhook` function returns one of three structured string statuses:
- `"SUCCESS"`: The webhook fired successfully and returned `200 OK`.
- `"AUTH_ERROR"`: The webhook returned `401` or `403`. The orchestrator must immediately transition to the Escalation path without attempting an infra retry.
- `"INFRA_ERROR"`: The webhook failed due to network exhaustion, `429`, or `>=500` after the in-memory backoff loop was exhausted. The orchestrator transitions to the Escalation path.

| Condition | Action | Counter |
|---|---|---|
| POST fails (gateway down, network drop, DNS) | Retry up to 3 times, 30-second backoff between each | Own counter (infra, in-memory loop), does NOT burn agent retry |
| All 3 POST attempts fail (`INFRA_ERROR`) | → Escalation agent | Infra failure, not agent retry |

### Anthropic API Errors

| Condition | Action | Counter |
|---|---|---|
| Timeout / 5xx | Infrastructure retry, 3 attempts | Own counter (infra, in-memory loop), does NOT burn agent retry |
| 401 / 403 | Config problem, no retry | → Escalation immediately |
| 429 rate limit | Backoff + retry | Own counter (infra, in-memory loop), does NOT burn agent retry |
| Bad model output (parse fail, validation fail) | Agent retry | Agent retry counter increments |

### JSON Parse Errors

- Unparseable JSON (malformed, truncated) → treated identically to structural validation failure — same retry counter, same branch logic
- Parse error type logged to `phase_state.json` with distinct error code so audit trail distinguishes parse failures from structural validation failures
- Gate script always wraps JSON load in `try/except` — unhandled parse exception must never crash the orchestrator

### Inference provider session rejections (`ERR_PROVIDER_REJECTED`)

When the OpenClaw session JSONL for planner, executor, or reviewer ends with an assistant `errorMessage` that matches the orchestrator's provider-rejection heuristic (HTTP 402 billing/credits, HTTP 429 rate limit, HTTP 401 / unauthorized / invalid API key, and common OpenRouter affordability strings), the orchestrator sets `last_error_code` to **`ERR_PROVIDER_REJECTED`**, writes `escalation_trigger_reason` with the truncated provider message, sets `current_agent` to **`escalation`**, and transitions to `RUNNING` pending escalation — **without** incrementing that agent's retry counter for that turn. The heuristic may be evaluated more than once per attempt (post-poll and post-gate) so errors flushed to JSONL after the first read are still detected.

For sessions that terminate immediately (`runtimeMs == 0`, `stopReason == "error"`), the orchestrator continues to use **`ERR_SESSION_DEAD_ON_ARRIVAL`** (distinct from `ERR_PROVIDER_REJECTED` when the session never meaningfully started).

### Roadmap Checkbox States

| Checkbox | Meaning | Orchestrator Behavior |
|---|---|---|
| `[ ]` | Pending | Pick first pending phase and run it |
| `[x]` | Complete | Skip, move to next pending |
| `[-]` | Skipped | Discard working files, clear sentinels, mark `[-] skipped — reason`, no git commit, advance |
| `[!]` | Blocked | Pipeline halts entirely; requires manual unblock (edit to `[ ]` or `[-]`); heartbeat does not auto-resume |

Blocked is a true halt — programmatic detection is worth the cost: false alarms are informative signals worth investigating.

---

## 13. Pure Script Inventory

Components that require no LLM at all:

- Repo initialization check (folder structure, support docs, roadmap file)
- Roadmap validation + phase identification
- Symlink update (`update_symlink`, transactional `os.symlink` + atomic `os.replace`) per new project
- Git add / commit / merge / tag (reviewer gate pass only)
- Cycle counter + sentinel + working file cleanup
- Audit archive write (before clear, non-blocking on failure)
- Lockfile management (`pipeline.lock` via `fcntl.flock`)
- Daily session cleanup cron (30–60 day TTL)
- Heartbeat cron (30 min, tunable to 15)

### Repo Initialization Check

- `os.path.exists()` checks on required folder structure
- Verify support docs present (`AGENTS.md`, `TOOLS.md`, `SOUL.md`, `USER.md`, `IDENTITY.md`) in all four agent workspaces
- Verify `.gitignore` exists in the project root (shared workspace symlink target)
- Verify roadmap file exists via glob patterns (`*oadmap*.md`, `*Roadmap*.md`, `*oadmap*.yaml`, `*oadmap*.json`)
- Exit 0 → proceed to roadmap gate | Exit 1 → escalate immediately (no retry)

**Implementation:** `gate_scripts/repo_init_check.py` — exposes a `check_repo_init()` function and calls `sys.exit(0)`/`sys.exit(1)` directly, so it **must** be called as a subprocess (not imported as a module — `sys.exit` would kill the orchestrator process). Called via `subprocess.run([sys.executable, gate_script], capture_output=True, text=True)` in `orchestrator.py::run_repo_init_check()`.

**Call location:** `orchestrator.py` method `run()` — runs after `self.read_state()`, before `while True:` (the phase loop). Runs on every startup including heartbeat cron resume.

**Failure behavior:** On non-zero exit, `run()` writes `current_agent = "escalation"` and `last_action = "Repo init check failed: <stdout+stderr>"` to state, invokes the escalation webhook directly (session key: `pipeline:phase-N:repo-init-failure`), then `return`s — the phase loop never executes. No retry counter is incremented. If escalation webhook fails, writes `escalation_failed.json` and transitions to `HALTED_SILENT`.

**Stdout format on failure:** The script prints `[ERROR] <specific check message>` identifying exactly which check failed (missing symlink, missing roadmap, missing `AGENTS.md` in which workspace, missing `.gitignore`). This text is captured and included verbatim in the escalation context so the operator knows what to fix.

### Roadmap Validation Gate

- Parse roadmap file — extract phases array
- Find first phase where `status !== "complete"`
- If none found → `PIPELINE_COMPLETE`
- If found → write phase context to `current_phase.json` → identify phase

### Identify Next Phase + Project Init

- Read `current_phase.json` — load detail, category, exit_criteria
- Initialize `phase_state.json`: `planner_retries=0`, `executor_retries=0`, `reviewer_retries=0`
- Update shared workspace symlink
- `git checkout -b phase/N`
- Write phase context file → POST webhook to invoke planner

### Merge & Commit — Phase N

- `git merge phase/N --no-ff`
- Write `[x]` checkbox to `roadmap.md` in-place (atomic: phase N must be marked complete in the merge commit itself)
- `git add roadmap.md`
- `git commit --amend --no-edit` ← folds the checkbox update into the merge commit; no separate commit
- `git tag --force phase-N-complete` ← placed AFTER amend so tag points to the amended commit
- Append `reviewer_output.suggestions` to `suggestions.md`
- Clear `phase_state.json`, `planner_output.json`, `executor_output.json`, all sentinels
- Loop back → roadmap gate

> **Why amend, not a separate commit?** `git checkout -b phase/NEXT` resets the working tree to HEAD. If the checkbox write is not part of HEAD (the merge commit), the next branch checkout silently discards it. Folding it into the merge commit via `--amend` ensures `roadmap.md` shows `[x]` on every branch derived from the merge. This is a behavioral fix, not a style choice.

### Signal Notification Implementation

- Orchestrator sends via OpenClaw webhook with `channel: "signal"` targeting registered Signal number
- Message payload: phase N, failing gate, failure reason, retry counts, timestamp
- Signal account must be registered in OpenClaw channels config — verify with `openclaw channels status` before first pipeline run

---

## §Skills — Optional Discipline Skill Injection

> **Status: Validated in production E2E run (2026-03-14).** All 6 phases of a cli-snake project completed with correct skill injection — discipline switched from `infra-config` → `core-logic` → `ui-frontend` → `core-logic` at each subsystem boundary, confirmed via `[SKILL] Status=loaded` log lines on every agent invocation.

### Overview

The pipeline injects a single per-agent, per-phase discipline skill (validated in a production E2E run 2026-03-14). Before invoking each agent webhook, the orchestrator calls `inject_skill()` once. It cleans the workspace skills directory at the start of the call, then writes the one discipline derived from `phase_raw_id` via `skill_mapping.yaml` (or nothing, when the prefix is unmapped). OpenClaw's `loadWorkspaceSkillEntries` walks the `skills/` tree at session start and loads the `SKILL.md` it finds. Skills are supplemental domain guidance — they do NOT replace or modify AGENTS.md, SOUL.md, TOOLS.md, IDENTITY.md, or USER.md.

> **Historical note (P1 Stage A + refactor).** P1 Stage A briefly added an "always-injected base skills" layer here — `integration-wiring` and `testing-quality` written to every workspace via a `SkillManager.BASE_DISCIPLINES` constant. That was refactored out: an always-applied rule is standing identity, not a per-phase skill, so those rules moved into each role's `autodev/agents/{role}/AGENTS.md` under `## Always-Apply: Integration Wiring` and `## Always-Apply: Testing Quality`. The two skill-library directories were deleted and the `INTEGRATION` / `TEST` / `E2E` mappings removed. Git history at the relevant commits shows the layered model if needed.

### File Locations

| Path | Role |
|------|------|
| `~/.openclaw/skill-library/{discipline}/{role}/SKILL.md` | Source library — operator-maintained, never modified by orchestrator |
| `~/.openclaw/config/skill_mapping.yaml` | Subsystem → discipline mapping (YAML, operator-editable) |
| `~/.openclaw/skill_manager.py` | `SkillManager` class — all injection logic |
| `~/.openclaw/workspace-{agent}/skills/{discipline}-{role}/SKILL.md` | Active injection target for live agent sessions |

> **Critical:** `~/.openclaw/skills/` is OpenClaw's global tier (loads for ALL sessions). Discipline skills must NOT be placed there. Only workspace-level `workspace-{agent}/skills/` is used.

### Config Schema (`openclaw.json`)

```json
"pipeline": {
  "skills": {
    "enabled": true,                    // Global kill switch — false disables all skills
    "planner_skills_enabled": true,     // Per-agent toggle
    "executor_skills_enabled": true,
    "reviewer_skills_enabled": true
  }
}
```

All flags default to `true` when absent. Config is read from the in-memory `openclaw_config` dict (loaded at orchestrator startup). Flag changes require orchestrator restart.

### Skill Resolution Algorithm

```
Input: phase_raw_id = "CORE-E2", agent_role = "executor"

Step 1: Check config flags
        If skills.enabled == false → clean workspace, log Status=disabled, done
        If executor_skills_enabled == false → clean workspace, log Status=disabled, done

Step 2: Clean workspace once
        Clean workspace-executor/skills/ entirely (rmtree + recreate).
        On rmtree failure → log Status=clean_failed, done.

Step 3: Extract subsystem
        If phase_raw_id is empty → log Status=none_mapped Reason=empty_phase_id, done
          (workspace stays empty — nothing injected).
        subsystem = "CORE-E2".split("-")[0].upper() → "CORE"

Step 4: Look up mapping
        discipline = skill_mapping.yaml["CORE"] → "core-logic"
        If no entry → log Status=none_mapped Reason=no_mapping_for_{subsystem}, done.

Step 5: Locate source and inject
        source = skill-library/core-logic/executor/SKILL.md
        If missing → log Status=none_found Reason=missing_file, done.
        Copy → workspace-executor/skills/core-logic-executor/SKILL.md
        Log Status=loaded
```

Final workspace contents per phase: 0 subdirectories (kill switch active, rmtree failed, empty/unmapped prefix, or missing source) or 1 subdirectory (the mapped prefix discipline). The single-cleanup-at-start contract guarantees no stale skill from any prior phase ever survives.

### Skill Mapping File Format (`config/skill_mapping.yaml`)

```yaml
INFRA: infra-config
CORE: core-logic
DATA: data-persistence
API: api-service
AUTH: auth-security
UI: ui-frontend
INTEGRATION: integration-wiring
TEST: testing-quality
E2E: testing-quality
CLI: cli-tooling
# Unmapped subsystems run without skills — MCP, HOOK, APPR, CTX, WORK, GIT, DASH, OPS
```

Keys are uppercase subsystem prefixes. Values are discipline directory names in `skill-library/`. Keys are case-normalised at load time. Missing file or bad YAML → empty mapping → all phases run without skills (graceful degradation).

### Orchestrator Integration Points

Three calls in `orchestrator.py`, each immediately after `cleanup_output_files()` and before `invoke_agent_webhook()`:

```python
# Planner (~line 814):
self.skill_manager.inject_skill(
    self.state.get("current_phase_raw_id", ""), "planner", self.openclaw_config
)

# Executor (~line 916):
self.skill_manager.inject_skill(
    self.state.get("current_phase_raw_id", ""), "executor", self.openclaw_config
)

# Reviewer (~line 1004):
self.skill_manager.inject_skill(
    self.state.get("current_phase_raw_id", ""), "reviewer", self.openclaw_config
)
```

**Post-inject phase_state.json write:** Immediately after each `inject_skill()` call, `_record_injected_skill(agent_role)` reads the workspace skills directory and writes `skill_injected` (the injected phase-prefix discipline, or `null` if nothing was injected) and `skill_agent` (role string) to `phase_state.json` atomically. The directory is clean-then-write, so any subdirectory present after the call is the current phase's skill. This makes the injected skill visible to the UI and audit log without parsing workspace directories externally.

### Graceful Degradation Contract

Every failure mode results in "run normally without skills":

| Failure | Behaviour |
|---------|-----------|
| `config/skill_mapping.yaml` missing | Warn once at init, all phases run without skills |
| Bad YAML in mapping file | Log error at init, all phases run without skills |
| Subsystem not in mapping | Clean workspace, log `Status=none_mapped`, continue |
| Skill file not in library | Clean workspace, log `Status=none_found`, continue |
| Copy fails (OSError) | Clean workspace, log `Status=none_found`, continue |
| `pipeline.skills` absent from config | All flags default to `true` |

### Log Format

```
[SKILL] ts={ISO8601} Phase={raw_id} Agent={role} Skill={path or NONE} Status={status} [Reason=...]
```

Status values: `loaded` | `none_mapped` | `none_found` | `disabled`

---

## 14. UI Server API Reference

The pipeline dashboard runs a FastAPI server (`ui/server.py`) at `http://localhost:18790`. All endpoints are local-only — no auth, no multi-user.

### Core State Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Returns `{"ok": true}` — liveness check |
| `GET` | `/api/state` | Returns `pipeline_state.json` contents |
| `GET` | `/api/roadmap` | Returns parsed roadmap phases array |
| `GET` | `/api/events` | Returns recent activity feed events (ring buffer, last 200) |
| `GET` | `/api/phase-state` | Returns `phase_state.json` contents |

### Completion & Metrics

#### `GET /api/metrics-summary`

Reads `metrics.jsonl` from `project_dir_path`. Deduplicates phase entries (keeping the latest row per `phase` ID — the orchestrator's canonical post-merge row). Returns aggregated totals and a per-phase breakdown.

**Response schema:**
```json
{
  "total_phases": 6,
  "total_duration_seconds": 1980,
  "total_executor_attempts": 9,
  "total_reviewer_passes": 7,
  "total_blame_fires": 2,
  "total_escalations": 0,
  "phases": [
    {
      "phase": "INFRA-1",
      "goal": "...",
      "duration_seconds": 180,
      "executor_attempts": 1,
      "reviewer_passes": 1,
      "blame_fires": 0,
      "escalations": 0,
      "skill_used": "infra-config"
    }
  ]
}
```

If `metrics.jsonl` is absent or empty, returns all-zero totals with an empty `phases` array. `null` duration values count as 0 in the sum.

**Total time semantics.** `total_duration_seconds` is the **sum** of per-phase `duration_seconds` — each phase's `phase_start`→PASS wall-clock, which already includes that phase's in-phase escalation hold. It is deliberately **not** `run_summary.json`'s calendar wall-clock (`run_start`→`run_end`); that span includes idle gaps across days and would inflate the figure (e.g. 74h calendar vs ~19h of real phase work). The response also carries `total_hold_seconds` (escalation waits, paired from `escalation_trigger`/`escalation_resolve` events in `pipeline_events.jsonl`) and `total_active_seconds = max(0, total_duration_seconds − total_hold_seconds)`. **Known limitation:** a phase's duration counts retried/reset work as active (it is real time the operator experienced), and the rare *non-escalation* mid-phase downtime (a hard crash, a manual STOP, or machine sleep while a phase is mid-flight) is also counted as active rather than excluded.

**UI usage:** Displayed in the Current Phase panel when `pipeline_status` is `PIPELINE_COMPLETE`, and in expanded rows of completed phases in the Roadmap panel.

### Pipeline Control

#### `POST /api/stop`

Requests a clean halt of the running orchestrator. The orchestrator detects and consumes the sentinel at the top of its next main loop iteration and transitions to `STOPPED`.

**Preconditions:** `pipeline_status` must be `RUNNING` or `WAITING_FOR_SENTINEL`.

**Success response (HTTP 200):**
```json
{"ok": true, "message": "Stop requested — pipeline will halt after current agent completes"}
```

**Error response (HTTP 409) — pipeline not in a stoppable state:**
```json
{"error": "Pipeline is not in a stoppable state"}
```

**Side effect:** Writes an empty file `{project_dir_path}/pipeline_stop_requested`. The orchestrator's `_check_stop_requested()` method removes this file when it detects it, preventing re-trigger on restart.

**UI behavior:** The stop button is visible only during `RUNNING` / `WAITING_FOR_SENTINEL`. Clicking shows a confirmation modal. On confirm, `POST /api/stop` is called and the button enters a pulsing "Stopping..." disabled state. The button disappears once `pipeline_status` changes away from the stoppable states.

#### `POST /api/command`

Issues a resume command to the escalation handler. Writes `escalation_output.json` then `escalation_output.done` atomically to the project directory (write order is critical — orchestrator reads `.done` as the signal that `.json` is complete).

**Preconditions:** `pipeline_status` must be `WAITING_FOR_HUMAN`. `escalation_resets` must be `< 3` if the command is `RESET_PHASE` or `RESET_EXECUTION`. `nuclear_resets` must be `< 2` if the command is `NUCLEAR_RESET` (its own cap, independent of `escalation_resets` — `NUCLEAR_RESET` is intentionally NOT gated on the escalation cap server-side; the "only when `escalation_resets >= 3`" rule is enforced UI-side).

**Request body:**
```json
{"command": "RETRY"}
```

**Valid commands:** `RETRY`, `RESET_EXECUTION`, `RESET_PHASE`, `RESET_REVIEWER`, `SKIP`, `PROCEED`, `STOP`, `NUCLEAR_RESET`. Any other value returns HTTP 400.

**Success response (HTTP 200):**
```json
{"ok": true}
```

**Error responses:**
- HTTP 409 — `{"error": "Pipeline is not waiting for human input"}` — status is not `WAITING_FOR_HUMAN`
- HTTP 409 — `{"error": "Reset cap reached"}` — `escalation_resets >= 3` and command is `RESET_PHASE` or `RESET_EXECUTION`
- HTTP 409 — `{"error": "Nuclear reset cap reached"}` — `nuclear_resets >= 2` and command is `NUCLEAR_RESET`
- HTTP 400 — `{"error": "Unknown command"}` — unrecognized command token

#### `POST /api/resume-ready`

Transitions `pipeline_status` from `STOPPED` **or `HALTED_SILENT`** to `WAITING_FOR_HUMAN` atomically (F11). Also sets `current_agent: "escalation"` so the restarted orchestrator enters the escalation command handler regardless of what agent was active when the pipeline stopped or halted. This is the clean operator recovery from a silent halt — `POST /api/pipeline/git-recover` remains the heavy, phase-destroying fallback.

**Preconditions:** `pipeline_status` must be `STOPPED` or `HALTED_SILENT`.

**Success response (HTTP 200):**
```json
{"ok": true}
```

**Error response (HTTP 409) — pipeline not in a resumable state:**
```json
{"detail": "Pipeline is not in a resumable state (current: <status>). Resume is available from STOPPED or HALTED_SILENT."}
```

**UI usage:** Called by the recovery panel (`StoppedRecoveryPanel`, reused for both `STOPPED` and `HALTED_SILENT` — with distinct "Intervention Required" copy for the latter) immediately before `POST /api/command` when the operator clicks Resume, Reset Execution, or Reset Phase. The header Resume button uses the same flow.

#### `POST /api/resume-orchestrator`

Spawns a new `orchestrator.py` process non-blocking. Reads `project_path` from `pipeline_state.json` and `autodev_repo_path` from `config.json`. When `project_dir_path` (or `symlink_target` / `project_dir`) resolves to a realpath that **differs** from the resolved `project_path` in `pipeline_state.json`, the server **repoints the pipeline-project symlink** to match state (**Policy A — state wins**), logs `[RESUME] reconcile symlink_to_state …`, then continues to lock check and spawn. Logs to `/tmp/orchestrator.log`.

**Success response (HTTP 200):**
```json
{
  "ok": true,
  "reconciled": false,
  "reconcile_action": null,
  "previous_symlink_real": null,
  "canonical_project_real": "<resolved project_path>"
}
```

When a repoint ran, `reconciled` is `true`, `reconcile_action` is `"symlink_to_state"`, and `previous_symlink_real` is the symlink’s realpath before replacement (if known).

Returns immediately — does not wait for the orchestrator to reach a stable state. The UI's polling loop detects the state transition.

**Error response (HTTP 409) — `pipeline.lock` held (orchestrator already running):**

`detail` is a string: `"Orchestrator is already running"`.

**Error response (HTTP 422) — symlink path cannot be safely updated:**

`detail` is a string (e.g. `project_dir_path` is a real directory or regular file, target path missing, parent not creatable). The orchestrator is not spawned.

**Error response (HTTP 503) — spawn failed after a successful repoint:**

JSON body (not FastAPI `detail` wrapper), so the client can show recovery copy and retry:

```json
{
  "ok": false,
  "reconciled": true,
  "reconcile_action": "symlink_to_state",
  "previous_symlink_real": "<path or null>",
  "canonical_project_real": "<resolved project_path>",
  "error": "<spawn error>"
}
```

**Error response (HTTP 503) — spawn failed with no repoint:** `detail` is a string (unchanged from other failure paths).

**UI usage:** Called after `POST /api/command` as the final step of the resume flow from the StoppedRecoveryPanel; also called from the Pipeline Monitor “Restart Orchestrator” control. The Pipeline Monitor header strip may show a one-line notice when `reconciled` is true or when the 503-after-reconcile body above is returned.

**Dashboard (L-38):** The UI maps known `detail` strings and spawn `error` text from this endpoint to **operator-facing** sentences in `index.html` (`resumeOrchestratorErrorPresentation`, `mapResumeOrchestratorFriendlyMessage`), with the raw server message optionally shown on a second monospace line. Exact strings live in code and `tests/test_ui_l38_resume_orchestrator_errors.py`; path issues steer to **Switch project** / Setup.

#### `GET /api/queue`

Returns the project queue plus dependency metadata. **Response ordering:** after merging any synthetic **ingested** row for `pipeline_state.json` projects missing from the queue file, entries whose `project_path` realpath matches `pipeline_state.json` `project_path` are listed **first** (stable order), then all other rows. This is a read-only presentation sort; the authoritative persisted order is still `position` on each row after reconciliation writes (`_queue_mark_matching_entry_active` pins the active project to `position: 1`).
