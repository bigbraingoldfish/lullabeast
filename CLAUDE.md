# CLAUDE.md — AutoDev Repository Guide

This file is the complete orientation for a contributor or Claude Code session working in this repo. Read it before touching anything else. All facts are drawn directly from source files; check the cited paths if anything is surprising.

---

## What This Repo Is

AutoDev is an autonomous multi-agent software development pipeline that orchestrates four LLM agents (planner, executor, reviewer, escalation) through a deterministic gate-based loop to iteratively build software from a roadmap. It depends on OpenClaw as external infrastructure (webhook server, agent session management, workspace directories). AutoDev does not embed OpenClaw — it calls it.

This repo contains:
- The pipeline orchestration code (previously lived in `~/.openclaw/`, now migrated here)
- The UI dashboard (FastAPI backend + single-file React frontend)
- Agent identity documents deployed into OpenClaw workspaces at install time
- The full skill library (27 SKILL.md files)
- Pipeline tests and UI tests in separate directories

---

## Repository Structure

```
autodev-ui/
├── autodev/
│   ├── pipeline/
│   │   ├── orchestrator.py        # Main pipeline loop, state machine, all agent coordination
│   │   ├── sentinel_poller.py     # Sentinel + idle detection for agent completion
│   │   ├── skill_manager.py       # Per-phase skill injection into agent workspaces
│   │   ├── webhook_client.py      # OpenClaw webhook invocation
│   │   ├── heartbeat_cron.py      # Crash recovery watchdog (run by system cron)
│   │   ├── session_cleanup.py     # Session TTL pruning cron
│   │   └── gate_scripts/
│   │       ├── utils.py            # Shared gate utilities, error codes, atomic writes
│   │       ├── phase_resolver.py   # Roadmap parser + phase identification (formerly roadmap_parser.py)
│   │       ├── phase_init.py       # Phase initialisation gate
│   │       ├── repo_init_check.py  # Git repo readiness gate
│   │       ├── planner_gate.py     # Evaluates planner output
│   │       ├── executor_gate.py    # Evaluates executor output, file manifest, unaccounted deletions
│   │       └── reviewer_gate.py    # Evaluates reviewer output, 3-pass attribution routing
│   ├── skill-library/
│   │   ├── {discipline}/{role}/SKILL.md   # 27 files: 9 disciplines × 3 roles
│   │   └── legacy/                        # Pre-discipline-library skills (historical)
│   ├── agents/
│   │   ├── {planner,executor,reviewer,escalation,prd-creator}/
│   │   │   ├── IDENTITY.md, SOUL.md, TOOLS.md, AGENTS.md, USER.md
│   │   │   └── HEARTBEAT.md  # planner, executor, reviewer only
│   ├── config/
│   │   ├── skill_mapping.yaml      # Maps roadmap subsystem prefixes → skill disciplines
│   │   ├── setup_session.json      # OpenClaw session setup configuration
│   │   └── mcp.json                # MCP server configuration
│   ├── tests/                      # Pipeline-level tests (orchestration, sentinel, skills)
│   └── docs/
│       ├── PIPELINE-SPEC.md        # Architecture spec — single source of truth (1631 lines)
│       ├── PIPELINE-CONSTRAINTS.md # Known issues, hardware limits, model bugs (533 lines)
│       ├── AUTODEV-UI-PRD.md       # Full product requirements for the dashboard UI
│       ├── ASSUMPTIONS.md          # Live spec divergences and resolved ambiguities
│       └── ...                     # Vision docs, roadmaps, templates
├── ui/
│   ├── server.py                   # FastAPI server (3562 lines) — all API endpoints
│   ├── roadmap_parser.py           # Display roadmap parser (all phases → list)
│   ├── index.html                  # Single-file React frontend (CDN React, no build step)
│   ├── config.example.json         # Template for local ui/config.json (committed)
│   ├── config.json                 # Local overrides — gitignored; copy from config.example.json
│   ├── requirements.txt            # fastapi, uvicorn, python-multipart, aiohttp
│   ├── autodev-ui.service          # systemd unit file (Linux / WSL2)
│   └── com.autodev.ui.plist        # macOS LaunchAgent — mirrors the systemd unit
├── tests/                          # UI server tests (~50 pytest files)
├── install.sh                      # Deployment script (14 steps, see SETUP.md)
├── SETUP.md                        # Human-facing setup guide
└── .env                            # Local path config (gitignored, written by install.sh)
```

### Architectural note: intentional single-file design

`ui/server.py` is deliberately a single large file (3,562 lines). The UI was built TDD across 23 phases; keeping all FastAPI routes in one file avoids cross-module import complexity in a single-process server. Do not split it into sub-modules without a deliberate refactoring decision.

`autodev/pipeline/orchestrator.py` is similarly monolithic by design: the pipeline state machine, all agent invocations, git operations, blame attribution, and escalation logic live together to make the control flow auditable in one place. Extracting pieces to helper modules requires understanding all the shared state.

---

## The Two Path Constants

Every pipeline file resolves two constants at module load time. Use these — never hardcode paths.

### `OPENCLAW_ROOT` (env: `OPENCLAW_ROOT`)

```python
# autodev/pipeline/env_resolvers.py
def resolve_openclaw_root() -> str:
    v = (os.environ.get("OPENCLAW_ROOT") or "").strip()
    return os.path.expanduser(v or "~/.openclaw")

# Pipeline modules:
OPENCLAW_ROOT = resolve_openclaw_root()
```

Points to the OpenClaw installation directory. **This is not the repo directory.** It is where OpenClaw lives: workspace directories, `openclaw.json`, `workspace-{agent}/` trees, and OpenClaw session state.

The legacy `AUTODEV_ROOT` alias has been removed — it is no longer read. Set `OPENCLAW_ROOT` (the canonical name) instead.

Used for: `workspace-{agent}/`, `openclaw.json`, `cron/jobs.json`, `pipeline-audit/` default.

### `AUTODEV_PIPELINE_ROOT` (env: `AUTODEV_PIPELINE_ROOT`)

```python
def resolve_pipeline_root(repo_path: str) -> str:
    v = (os.environ.get("AUTODEV_PIPELINE_ROOT") or "").strip()
    return os.path.expanduser(v) if v else os.path.join(repo_path, ".autodev")
```

Points to the AutoDev pipeline state directory. Default is `<AUTODEV_REPO_PATH>/.autodev/`. Holds `pipeline.lock`, `pipeline_state.json`, `pipeline_queue.json`, `pipeline_events.jsonl`, `orchestrator.log`, `ideas/`, and the `pipeline-project` symlink.

The legacy `AUTODEV_RUNTIME_ROOT` alias and the `AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME` flag have been removed. To reproduce the old layout where pipeline state lived alongside OpenClaw data, set `AUTODEV_PIPELINE_ROOT=$OPENCLAW_ROOT` explicitly.

### `AUTODEV_REPO_PATH`

```python
# In autodev/pipeline/*.py (3 dirname calls from autodev/pipeline/ to reach repo root):
AUTODEV_REPO_PATH = os.environ.get(
    "AUTODEV_REPO_PATH",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
```

Points to the repo root (the directory containing this CLAUDE.md). Used for resolving gate scripts, skill library, and config files that now live inside the repo.

Used for:
- Gate scripts: `os.path.join(AUTODEV_REPO_PATH, "autodev", "pipeline", "gate_scripts", "*.py")`
- Skill library: `os.path.join(AUTODEV_REPO_PATH, "autodev", "skill-library")`
- Skill mapping: `os.path.join(AUTODEV_REPO_PATH, "autodev", "config", "skill_mapping.yaml")`
- Orchestrator (from heartbeat_cron.py): `os.path.join(AUTODEV_REPO_PATH, "autodev", "pipeline", "orchestrator.py")`

**The depth rule:** Every file in `autodev/pipeline/` uses 3 `os.path.dirname` calls to reach repo root. A file at repo root would use 0. A file at `autodev/` depth would use 1. Do not copy a path derivation formula from one depth to another without adjusting the count.

### `ui/server.py` path constants

`ui/server.py` has its own constants at lines 21–24 and uses a `DEFAULTS` dict (lines 362–375). See the config dual-source rule below.

```python
ORCHESTRATOR_FILENAME = "orchestrator.py"
WEBHOOK_AGENT_ID = "prd-creator"
```

---

## Config Dual-Source Rule (`ui/server.py`)

The UI server's configuration is a two-layer merge:

1. **`DEFAULTS` dict** (lines 362–375 of `ui/server.py`) — hardcoded fallbacks including all `~/.openclaw/…` path defaults.
2. **`ui/config.json`** — optional local overrides adjacent to `server.py` (not committed; copy from `ui/config.example.json`). If this file exists, any key it contains overwrites the corresponding DEFAULTS value. **`AUTODEV_HOOKS_TOKEN`** (environment) overrides `hooks_token` when set, so the webhook Bearer secret need not live in the file. **`AUTODEV_IDEAS_IDLE_THRESHOLD`** (environment) overrides `ideas_idle_threshold` when set (Ideas chat sentinel poll; the chat send has no startup-grace knob — it waits for the definitive stall/backstop verdict).

`load_config()` applies this merge and expands `~` in all path values. Every endpoint that reads a file path calls `load_config()` (or receives the result) rather than referencing DEFAULTS directly.

The `autodev_repo_path` key in DEFAULTS reads from the `AUTODEV_REPO_PATH` environment variable first, then falls back to `~/.openclaw` (which is wrong after migration but preserved for backward compatibility). The correct value is the repo root. `install.sh` writes `.env` with the correct value; source `.env` before starting the server or set it in `ui/config.json`.

```json
{
  "autodev_repo_path": "/path/to/your-project/autodev-ui"
}
```

When `_spawn_orchestrator` (called by `/api/setup/launch`) looks for `orchestrator.py`, it constructs:
```python
orchestrator_script = os.path.join(autodev_repo_path, ORCHESTRATOR_FILENAME)
# = os.path.join(autodev_repo_path, "orchestrator.py")
```
This is **wrong unless `autodev_repo_path` is set to the repo root** — the correct path is `autodev/pipeline/orchestrator.py`. This is a known unfixed issue; see Unresolved Items below.

---

## How to Run Tests

**Pipeline tests** (orchestration logic, no running OpenClaw required):

```bash
source .env
pytest autodev/tests/ -q
```

**UI server tests** (FastAPI + frontend):

```bash
source .env
pytest tests/ -q
```

Some tests (`test_skill_mode_a_symlink_and_validation.py`, `test_skill_mode_b_symlink_and_validation.py`) resolve `AUTODEV_REPO_PATH/autodev/skill-library/`. If you run without `.env` loaded:

```bash
AUTODEV_REPO_PATH=$(pwd) pytest tests/ -q
```

**Pipeline tests do not** require a live OpenClaw instance. They use in-process mocks and `tmp_path` fixtures. See `autodev/tests/conftest.py` for the shared fixtures: `planner_state`, `executor_state`, `reviewer_state`, `orchestrator_instance`, `mock_config`.

**Do not** run `pytest` from inside `autodev/pipeline/` — conftest.py is at `autodev/tests/`. Run from repo root.

---

## State Machine Rules

### The 8 valid pipeline states

Defined as a list in `orchestrator.py` (`VALID_STATES`):

```python
VALID_STATES = [
    "RUNNING",
    "WAITING_FOR_SENTINEL",
    "WAITING_FOR_HUMAN",
    "HALTED_SILENT",
    "BLOCKED",
    "PIPELINE_COMPLETE",
    "STOPPED",
    "QUEUE_HALTED",
]
```

`QUEUE_HALTED` is written when the project queue is active but all remaining entries are blocked, in dependency hold, or fail preflight. The `pipeline_state.json` will additionally contain `queue_halted_reason: "all_blocked" | "all_dependency_hold" | "mixed" | "answered_pending_revival"`. In the dashboard, the **pipeline status pill** for this state is labeled **Queue stalled** (`ui/index.html` `PIPELINE_LIVE_PILL`). Do not confuse that with the header **Queue: halted** *chip* (navigation to the queue when `queue_halted` is true) — different control, different copy. `POST /api/queue/trigger-next` returns `queue_halted_reason` when it cannot start a project so the queue UI can show a matching **Queue stalled — …** toast. The orchestrator exits cleanly on an initial halt; an `answered_pending_revival` reason means a parked `ESCALATION_ANSWERED` entry has a banked answer and is recoverable via the **Resume banked answer** control (P1 Stage H — see the Queue System section for the restart-recovery hook).

`pipeline_state.json`'s canonical status field is **`pipeline_status`**. `transition_state()` is the only writer — it sets `pipeline_status`, and `write_state()` atomically persists it. There is no separate `status` field: a legacy `status` co-field was removed (it was written only by the git-recover path and read by no one), so all readers use `pipeline_status`.

### `transition_state()` — the only correct way to change state

```python
def transition_state(self, new_status, action_description):
    ...
    self.write_state()  # atomically commits state before anything else happens
```

`write_state()` uses `mkstemp` + `os.replace` (atomic rename). The state file is committed **before** any action is taken. This is the write-then-act pattern: if the process crashes after writing state but before completing the action, crash recovery can resume from the committed state.

**Do not** call `self.state["pipeline_status"] = "..."` and then act without calling `write_state()`. Any code that updates state dict fields must end with a `write_state()` or `transition_state()` call.

### State reset protocol

When resetting `pipeline_state.json` to `IDLE` for a fresh run, set `pipeline_status` to `"IDLE"`. Also set `current_agent` to `"planner"` and `current_phase` to `0`. If `current_agent` is `null`, the orchestrator exits immediately with `"Agent None logic not reached"`.

### Terminal states

`HALTED_SILENT`, `BLOCKED`, and `PIPELINE_COMPLETE` are checked at the top of the main loop and cause the orchestrator to exit cleanly. `STOPPED` is a clean halt triggered by the stop sentinel file — or by the escalation consumer defaulting an empty / unrecognised resume command to STOP (emitting `escalation_command_invalid`) rather than dead-ending at `HALTED_SILENT`. The orchestrator does **not** attempt recovery from these states — only a manual reset or operator command resumes the pipeline.

---

## Gate Script Interface Contract

Gate scripts are in `autodev/pipeline/gate_scripts/`. They are invoked by the orchestrator via `subprocess.run()`. All communication is via exit codes and stdout.

### Exit codes

| Exit code | Meaning |
|-----------|---------|
| `0` | Pass — agent output accepted, advance pipeline |
| `1` | Fail — agent output rejected, JSON error detail on stdout |
| `2` | Blocked — roadmap phase is marked `[!]` (only `phase_resolver.py`) |

### Stdout protocol

On exit 1, the gate must print a JSON object to stdout. The orchestrator reads stdout only when exit code is non-zero. The JSON is written to `failure_context.json` for the next retry attempt. Gate scripts must **not** print partial or malformed JSON on failure.

### Advisory output channel (P1 Stage F)

On PASS, a gate may write structured advisory output to a **separate** artifact file `executor_advisory_detail.json`. The orchestrator drains it on the executor PASS path and emits pipeline events; the file is removed after consumption. This is architecturally distinct from `executor_gate_detail.json` (the FAIL channel consumed by `write_failure_context`). The two channels never co-tenant. Stage F's COMPLETE-phase reachability advisory is the first instance — see `autodev/docs/PIPELINE-SPEC.md` §4.5 for the full pattern and promotion criteria.

### Determinism requirement

Gate scripts must be deterministic — they receive filesystem state and return a verdict. They must not invoke LLMs, make network calls, or produce variable output. The orchestrator calls them at specific points in the loop and expects a stable result.

### The `phase_resolver.py` rename

The gate that reads `roadmap.md` and identifies the next pending phase was renamed from `roadmap_parser.py` to `phase_resolver.py` during migration. All references in `orchestrator.py` already use `phase_resolver.py`. The `ui/roadmap_parser.py` is a **different file** — it's the display parser for the frontend, not a gate script. They share an input format but have entirely different output contracts.

---

## Sentinel Polling Rules

### Use `poll_for_sentinel()` with Tier A stall arguments

The primary function is `poll_for_sentinel()` in `sentinel_poller.py`. It watches the `.done` sentinel, the stop sentinel, stale sentinel mtimes, and the Tier A activity stamp.

### Critical parameters

```python
poll_for_sentinel(
    sentinel_path=...,
    timeout_seconds=4500,                # 75-min infrastructure backstop (gateway-dead failsafe)
    stall_detection_path=...,            # {agent}_activity.stamp
    stall_threshold_seconds=300,         # post-first-hook silence → "stalled"
    startup_grace_seconds=600,           # pre-first-hook wait → "no_first_activity"
    min_sentinel_mtime=time.time(),      # captured BEFORE cleanup_output_files()
)
```

Returns a structured `PollResult` (truthy on success, `__bool__` delegates to `.success` for backwards-compat with existing `if sentinel_found:` callers). Inspect `.reason` to distinguish outcomes:

- `"succeeded"` — `.done` observed
- `"stalled"` — stamp advanced once then went silent > `stall_threshold_seconds`
- `"no_first_activity"` — `startup_grace_seconds` exceeded without any stamp advance
- `"stopped"` — operator wrote the stop sentinel
- `"timeout"` — infrastructure backstop fired (gateway unreachable)

**Two-knob design (Section 1 of can-you-look-at-generic-thunder).** A single threshold previously had to cover both legitimate slow OpenClaw boots (3–10 min) and mid-turn silence detection (3–5 min). Splitting into `startup_grace_seconds` (pre-first-hook) and `stall_threshold_seconds` (post-first-hook) lets each be tuned independently. The CORE-E6 incident pattern — agent runs for ~5 min then goes quiet — fires `"stalled"` within ~5 min of last activity rather than waiting the old 30-min threshold.

**Inline abort on stall/no_first_activity/timeout.** When `poll_for_sentinel` returns `PollResult` with `reason in {"stalled", "no_first_activity", "timeout"}`, the orchestrator calls `_handle_stall_outcome(...)` which:
1. Invokes `abort_agent_session` against the *current* attempt's session key. The helper itself retries up to `ABORT_MAX_ATTEMPTS` (3) times with `ABORT_RETRY_BACKOFF_SECONDS` (2.0 s) between attempts before declaring failure — a single 8-second WS handshake against a busy gateway proved too brittle in CORE-E6. The handshake is a 3-step dance (the OpenClaw gateway sends a `connect.challenge` event first, then accepts the `connect` request, then returns `hello-ok`); the helper waits for the challenge before sending `connect`. The HTTP-upgrade `Origin` header is suppressed (`suppress_origin=True`) because Python's `websocket-client` lib otherwise auto-adds one, and the gateway treats any non-empty `Origin` as a browser request and refuses to grant `operator.write` scope to the resulting session — even on loopback.
2. Captures the return value and logs `[ABORT] result=ok|FAILED ...`.
3. On a successful abort, calls `verify_session_stopped` to confirm the agent stopped streaming. If it has not (gateway acknowledged abort but stamp still advancing), the helper emits the `[ABORT][VERIFY_FAILED]` print and the `abort_verify_failed` pipeline event (so the activity feed shows the situation in red) and then **soft-continues** — the orchestrator launches the next attempt anyway. Rationale: 90%+ of long runs eventually resolve on retry, whereas a forced `HALTED_SILENT` state always requires human intervention. The `"timeout"` reason — the 75-minute infrastructure backstop firing — now also routes through this same helper; previously it bypassed abort+verify and let attempt N+1 launch on top of a still-streaming attempt N (the original CORE-E6 cascade). The retry-start abort block (`orchestrator.py:3916`) follows the same soft-continue contract.

**Activity stamp bootstrap**: After `cleanup_output_files()` removes stale output and before the webhook is invoked, call `_init_activity_stamp_or_halt(agent)`. The return value is checked — a False result (workspace dir unwritable) transitions the orchestrator to `HALTED_SILENT` rather than silently disabling stall detection. The plugin refreshes this stamp on `model_call_started`, `model_call_ended`, and `after_tool_call`.

**Plugin build & sessionKey shape (gotchas that bit us live).** The `autodev-pipeline-signals` plugin source lives in `autodev/plugin/`. OpenClaw ≥ 2026.5.x **refuses to load plugins from TypeScript source** — it requires a compiled `dist/index.js`. `install.sh` runs `npm install && npm run build` (esbuild bundle, see `autodev/plugin/package.json`) before `openclaw plugins install`; the resulting `dist/` and `node_modules/` are gitignored. Verify the plugin actually loaded by grepping the gateway journal for `http server listening (N plugins: autodev-pipeline-signals, …)` — if the name is missing, the gateway is running blind and the activity stamp will never refresh. Separately: the gateway delivers `hookCtx.sessionKey` with the OpenClaw `agent:{role}:` prefix (e.g. `agent:executor:pipeline:phase-4:ui-e1:executor-attempt-1` for the pipeline, `agent:prd-creator:ideas:{ideaId}:session-{n}` for the Ideas chat), so the matchers in `autodev/plugin/src/utils.ts` — `isPipelineSession`, `isIdeasSession`, `extractIdeasIdFromSessionKey`, `parseIdeasTurnSession` — each accept both the bare form and the `agent:{role}:…` form. Removing either branch causes the stamp to silently stop refreshing in production while unit tests (which historically used the bare form only) keep passing. The Ideas variant of this bug was caught live on the Untitled Balloon Popping Game chat: `startup_grace` fired at 30 s while the agent was actively working because the production-prefixed sessionKey skipped the Ideas branch in `recordPipelineActivity`. Relatedly, the per-idea stamp is split by session role: only the foreground chat turn (`ideas:{id}:session-{n}`) writes `prd_creator_activity.stamp` — the file `_poll_sentinel_with_idle_detect` watches and its sole consumer — while the background readiness assessment (`ideas:{id}:readiness`, auto-fired fire-and-forget after every turn) writes its own `prd_creator_readiness_activity.stamp` via `isIdeasReadinessSession` / `ideasActivityStampFilename`. Both keys yield the same `ideaId`, so before the split an overlapping readiness run could warm the chat stamp and mask a stalled foreground turn. `clarity` / `convert` / `format-correction` sessions still share the chat stamp (user-initiated, lower overlap risk).

**`min_sentinel_mtime`**: Capture `time.time()` **before** calling `cleanup_output_files()`. This timestamp is compared against the mtime of any `.done` file found. If the `.done` file is older than this timestamp, it is treated as orphaned output from a prior session and discarded. Without this guard, an orphaned session writing its `.done` file after the current attempt starts will burn the retry. The pattern in orchestrator.py is:

```python
_attempt_start_time = time.time()          # BEFORE cleanup
cleanup_output_files(SYMLINK_TARGET, "executor")
self.skill_manager.inject_skill(...)
if not self._init_activity_stamp_or_halt("executor"):
    return  # workspace unwritable — HALTED_SILENT already set
self.transition_state("WAITING_FOR_SENTINEL", ...)
# ... [POLL][CONFIG] log line + invoke webhook ...
sentinel_found = poll_for_sentinel(
    ...,
    min_sentinel_mtime=_attempt_start_time,
    stall_detection_path=_executor_stamp,
    stall_threshold_seconds=_stall_timeout_seconds("AUTODEV_STALL_TIMEOUT_EXECUTOR", "300"),
    startup_grace_seconds=_startup_grace_seconds("AUTODEV_STARTUP_GRACE_EXECUTOR", "600"),
)
if getattr(sentinel_found, "reason", None) in ("stalled", "no_first_activity"):
    if not self._handle_stall_outcome("executor", session_key, _executor_stamp, sentinel_found.reason):
        return  # verify failed — HALTED_SILENT already set
```

**`AUTODEV_STALL_TIMEOUT_*`** (default `300`s): seconds of no Tier A stamp refresh **after first activity** before retry. Tighter values catch mid-turn deaths sooner; the bootstrap guard ensures pre-first-hook silence does not trigger here. Do not raise them to work around missing hook writes — fix the plugin event path instead.

**`AUTODEV_STARTUP_GRACE_*`** (default `600`s): seconds to wait for the **first** stamp advance before declaring `"no_first_activity"`. Tune this for cold OpenClaw session creation time, not for in-session model latency.

---

## Pipeline Events (`pipeline_events.jsonl`)

The orchestrator emits structured events via `_write_pipeline_event(event_type, phase, agent, detail)` (defined at `orchestrator.py:520`). Each call appends one JSONL line to `<AUTODEV_PIPELINE_ROOT>/pipeline_events.jsonl`:

```json
{"ts": "<ISO8601 UTC>", "event": "<name>", "project": "<name>", "phase": "<raw_id>", "agent": "<role>", "detail": {...}}
```

The UI server tails this file via `_poll_pipeline_events_file()` (`ui/server.py:171`) and streams new lines through SSE (`/api/events/stream`); the activity tab renders events from that stream in real time. An in-memory ring buffer (50 entries, `_ring_buffer` in `ui/server.py:54`) serves as a synthetic fallback when the file is missing.

### Event catalogue

| Event name                 | Where emitted                                                     | Key `detail` fields                                                                                  | Section |
|----------------------------|-------------------------------------------------------------------|------------------------------------------------------------------------------------------------------|---------|
| `gate_pass`                | Reviewer gate returns `PASS`                                      | `{}`                                                                                                 | pre-existing |
| `gate_fail`                | Planner/executor/reviewer gate returns non-`PASS`                  | `{exit_code}` / `{gate_result}` / `{retry_class}` (Stage H — executor: always present; reviewer: `"reviewer_rejection"` on ROUTE_EXECUTOR else `None`) | pre-existing + Stage H |
| `escalation_trigger`       | Escalation agent invoked                                          | `{reason}`                                                                                           | pre-existing |
| `escalation_resolve`       | Operator resume command consumed                                  | `{command}`                                                                                          | pre-existing |
| **`escalation_command_invalid`** | Escalation consumer read an empty / missing / unrecognised `command`; defaulted to `STOP` (recoverable) instead of `HALTED_SILENT` | `{received_command, defaulted_to}` | escalation heal |
| `phase_complete`           | Canonical metrics row written (post-merge)                        | `{executor_attempts, blame_fires}`                                                                   | pre-existing |
| **`poll_start`**           | Before each `poll_for_sentinel()` invocation (3 sites)             | `{startup_grace, stall_threshold, infra_backstop, session_key, attempt}`                              | 6.1.a |
| **`poll_outcome`**         | After `poll_for_sentinel()` returns (3 sites)                      | `{reason, stamp_mtime, duration_s, session_key, attempt}`                                            | 6.1.a |
| **`attempt_end`**          | Companion to `[ATTEMPT_END]` dense print (3 sites)                  | `{reason, duration_s, attempt, session_key, retry_class}` (Stage H added `retry_class`)               | 6.3 + Stage H |
| **`abort_attempted`**      | After every `abort_agent_session()` call (retry-start + inline)    | `{session_key, result, agent_role, reason, source}`                                                   | 6.1.b |
| **`abort_verify_failed`**  | When `verify_session_stopped()` returns `False` (no longer halts — soft-continue) | `{session_key, stamp_path, agent_role, reason}`                                                       | 6.1.b |
| **`reviewer_verdict`**     | On every reviewer-gate consumption                                 | `{verdict, pass_number, next_agent}`                                                                  | 6.1.c |
| **`stamp_init_failed`**    | When `_init_activity_stamp_or_halt` returns False                  | `{agent_role, stamp_path, reason}`                                                                    | 6.1.d |
| **`reachability_warning`** | On the executor PASS path, when `executor_advisory_detail.json` has a populated `reachability_summary` or non-empty `reachability_diagnostics`. One summary event per phase + one event per diagnostic. _(Phase 3: a compact copy of the drained advisory is also stashed to `phase_state.last_reachability_summary` and surfaced in the metrics row's `reachability_summary` field before the file is removed.)_ | `{kind, count?, files?, command?, file?, reason}` where `kind ∈ {unreachable_summary, no_resolver, resolver_limitation, resolver_error}` | P1 Stage F |
| **`reachability_not_applicable`** | On the executor PASS path, when the entry-point command is a recognised test runner (pytest, jest, vitest, ...) | `{reason}` | P1 Stage F |
| **`nuclear_reset`** | Inside `nuclear_reset_phase()`, after the `nuclear_resets` increment + `reset_log` append, before delegating to `reset_phase()` (so `phase` is the pre-reset escalated phase) | `{nuclear_resets, reason, phase}` (`reason` = `last_error_code`) | P2 Observability |
| **`queue_halted`** | Inside `_select_next_queue_project()`, in the `if halt_if_no_eligible:` branch right after `transition_state("QUEUE_HALTED", …)` (the reason-clearing `else` does not emit) | `{reason}` (`all_blocked` / `all_dependency_hold` / `answered_pending_revival` / `mixed` / `all_completed`) | P2 Observability |
| **`queue_parked`** | Inside `_queue_park_active_entry()`, after the successful queue write (all 4 call sites route through this single emit, once each) | `{reason, phase, entry_id, entry_name}` | P2 Observability |
| **`queue_revived`** | Inside `_select_next_queue_project()` revival branch, after `_apply_pending_escalation_command()` returns the applied command (guarded on `is_revival` + a real command, so the fresh-start path never emits) | `{entry_id, entry_name, command}` | P2 Observability |
| **`dependency_hold`** | Inside `_select_next_queue_project()`, after a genuine READY→DEPENDENCY_HOLD write (an already-held entry is skipped by the state gate before reaching here, so no re-emit) | `{parent_id, entry_id, entry_name}` | P2 Observability |

The bold entries are Section 6 additions; existing UI consumers handle them transparently because the JSONL schema is additive. The five **P2 Observability** rows make previously-SILENT queue-lifecycle / destructive transitions first-class events (emitted by `orchestrator.py`, rendered in the activity feed by `ui/index.html` — colour `getEventBadgeColor`, label `EVENT_TYPE_DISPLAY`, hover `EVENT_TYPE_DESCRIPTION`, prose `humanizeSummary`); the `agent` field is `"queue"` for the four queue events and `"escalation"` for `nuclear_reset`.

### Phase-state outcome fields (Section 6.4)

`phase_state.json` now also persists the latest poll/abort outcome **and the terminal phase outcome** so a restarted orchestrator and the dashboard can render "what happened last" without scraping logs. Written by `_record_phase_outcome(**fields)` (defined near `write_phase_state_atomic`):

| Field                  | Values                                                                |
|------------------------|-----------------------------------------------------------------------|
| `last_poll_reason`     | `succeeded` / `stalled` / `no_first_activity` / `stopped` / `timeout` |
| `last_abort_result`    | `ok` / `FAILED` / `verify_failed`                                     |
| `last_attempt_summary` | Dense one-line string mirroring the `[ATTEMPT_END]` log line          |
| `last_phase_outcome`   | `completed` / `escalated` / `nuclear_reset` (absent while in-progress; Phase 3) |

**`last_phase_outcome` durability caveat (Phase 3).** The field persists live only for the *non-advancing* outcomes — `escalated` (set once at the single main-loop escalation chokepoint and at the repo-init escalation block) and `nuclear_reset` (set in `nuclear_reset_phase` and **preserved across `reset_phase`'s** re-init, otherwise the reset it delegates to would wipe it). On a `completed` phase, `phase_state.json` is deleted on advance, so the `completed` value — written right after the canonical metrics row and **before** the audit archive copies `phase_state.json` — survives only in the per-phase audit archive and a brief restart window. The **durable** completion record is the canonical metrics row + the `phase_complete` event; the dashboard reads completion from the metrics row, not live `phase_state`. Reachability is deliberately **not** an outcome value — it is non-terminal (a phase can be `completed` *and* carry a reachability advisory) and is captured by the metrics row's `reachability_summary` field instead.

### In-poll heartbeat (Section 6.2)

`poll_for_sentinel()` accepts `heartbeat_interval_seconds` (60 s in the three orchestrator call sites). During a long wait it prints one line per interval:

```
[POLL][HEARTBEAT] elapsed=120s stamp_age=12s checked_in=True
```

This distinguishes "alive, agent making progress" from "alive, agent stopped" from "orchestrator hung" — the three states that were indistinguishable in the pre-Section-6 logs.

### Metrics history file (Section 6.0)

`metrics.jsonl` write history is preserved in an orchestrator-private append-only file at `$AUTODEV_PIPELINE_ROOT/metrics_history/<project_name>.jsonl`, written by `_write_canonical_metrics_row()`. The agent cannot reach this directory; even if the executor overwrites the project's `metrics.jsonl` to a single row, the orchestrator rebuilds the full history on the next phase completion. On first deploy the file is bootstrapped from the live `metrics.jsonl` so existing history is preserved across the upgrade.

**Canonical metrics row — Phase 3 pain-signal fields.** `_write_canonical_metrics_row()` also persists per-phase "pain signals" read from the fresh on-disk `phase_state` at row-write time. The row is written on the reviewer-PASS path **before** `phase_state.json` is deleted on advance, so these are still available: `escalation_resets`, `nuclear_resets`, `reviewer_unverified_retries` (counters, default `0`); `reset_log` (the operator-reset audit-trail snapshot — `[]` when none, bounded by the 3-escalation + 2-nuclear caps, captured here because the live `reset_log` is wiped on phase advance); and `reachability_summary` (compact `{kind, count?, files?, command?/reason?}`, or `null` when no advisory drained that phase — stashed onto `phase_state.last_reachability_summary` by `_emit_reachability_advisory` before it removes the advisory file, since that file is gone long before this row is written). All additive — the metrics reader and UI tolerate unknown fields, so no migration is needed.

---

## Skill Injection — End-to-End

### What happens per agent invocation

Immediately after `cleanup_output_files()` and before `invoke_agent_webhook()`, the orchestrator calls:

```python
self.skill_manager.inject_skill(
    phase_raw_id,    # e.g. "CORE-E2"
    agent_role,      # "planner", "executor", or "reviewer"
    self.openclaw_config
)
```

### Source of truth

Skill files live in `autodev/skill-library/{discipline}/{agent_role}/SKILL.md`. One discipline is injected per phase — the **variable** layer keyed on the phase prefix.

**The phase-injectable disciplines:** `api-service`, `auth-security`, `cli-tooling`, `core-logic`, `data-persistence`, `infra-config`, `ui-frontend` (each with planner / executor / reviewer SKILL.md), plus the special `completion` discipline (COMPLETE phases) and the agent-owned `prd-creator` / `roadmap-converter` skill sets used outside the pipeline loop.

**Universal rules live in AGENTS.md, not here.** The always-apply wiring discipline ("read the entrypoint before wiring") and testing-quality discipline (TDD) are not skills — they are standing identity. They live in each role's `autodev/agents/{role}/AGENTS.md` under the `## Always-Apply: Integration Wiring` and `## Always-Apply: Testing Quality` sections, which OpenClaw injects as primary context every turn — **but only if the truncation caps reach them.** OpenClaw's default per-file bootstrap cap (`bootstrapMaxChars`) is 12000 and these sections begin past byte ~10k, so AutoDev raises the cap to 32000 and points the post-compaction refresh (`postCompactionSections` / `postCompactionMaxChars`) at the section names; otherwise the rules are truncated at injection and dropped on every compaction. See the truncation rows in **Operational Constants** and `setup_helpers.ensure_openclaw_context_limits`. The `integration-wiring` and `testing-quality` skill-library directories were removed in the P1 Stage A refactor; the `INTEGRATION` / `TEST` / `E2E` prefixes are intentionally unmapped (see `skill_mapping.yaml`).

### Mapping mechanism

`autodev/config/skill_mapping.yaml` maps roadmap subsystem prefixes (uppercase) to discipline directory names. Example:

```yaml
CORE: core-logic
UI: ui-frontend
API: api-service
```

`SkillManager._load_mapping()` reads this file at construction time, normalises keys to uppercase. If PyYAML is not installed, mapping is disabled entirely (graceful degradation — no crash, no skill injection).

**Do not add speculative mappings.** The YAML file's header comment is explicit: incorrect skill context is worse than no skill context. Only map a subsystem if the relationship is direct and unambiguous.

### Trigger mechanism

The subsystem is extracted from the phase ID by `phase_raw_id.split("-")[0].upper()`. `"CORE-E2"` → subsystem `"CORE"` → discipline `"core-logic"` → source `autodev/skill-library/core-logic/{role}/SKILL.md`.

### Destination

```
OPENCLAW_ROOT/workspace-{agent_role}/skills/{discipline}-{agent_role}/SKILL.md
```

The directory holds **at most one** subdirectory per phase — the phase-prefix discipline when one maps, or nothing. Example for a `CORE-E2` executor phase:

```
~/.openclaw/workspace-executor/skills/core-logic-executor/SKILL.md
```

`_clean_workspace_skills()` runs **exactly once at the start of every `inject_skill()` call** (`shutil.rmtree` + `os.makedirs`). The mapped prefix skill (if any) is written into the freshly prepared directory. This ensures no stale skill from a previous phase can survive into the next phase. OpenClaw's `loadWorkspaceSkillEntries` walks the `skills/` tree at session start and surfaces the `SKILL.md` it finds.

### The `~/.openclaw/skills/` (global tier) is intentionally untouched

`skill_manager.py` only writes to `workspace-{agent}/skills/`. The global tier at `~/.openclaw/skills/` would load all skills simultaneously, which is not the intent. Per-phase injection via workspace-level skills is the design.

### Conditions under which injection is silently skipped

`inject_skill()` always cleans the workspace skills directory first (removing any prior skill), then injects nothing (leaving the directory empty) if any of these holds:
1. `pipeline.skills.enabled` is `false` in `openclaw.json`
2. `pipeline.skills.{agent_role}_skills_enabled` is `false`
3. `_clean_workspace_skills()` failure (workspace directory unwritable)
4. `phase_raw_id` is empty
5. Subsystem prefix has no entry in `skill_mapping.yaml` (includes the deliberately-unmapped `INTEGRATION` / `TEST` / `E2E`)
6. `autodev/skill-library/{discipline}/{role}/SKILL.md` does not exist on disk
7. `OSError` during file copy
8. PyYAML not installed (mapping disabled entirely)

In all cases a `[SKILL]` log line is emitted to stdout with `Status=loaded`, `Status=none_mapped`, `Status=disabled`, or `Status=none_found`.

`phase_state.skill_injected` (consumed by metrics row, UI label, snapshot endpoint) records the injected phase-prefix discipline, or `null` when nothing was injected.

---

## Agent LLM Configuration and Known Failure Modes

### How models are resolved

**Planner, executor, and reviewer** do not receive a `model` field on `POST /hooks/agent`. OpenClaw uses each agent’s `agents.list[].model.primary` from the live `openclaw.json` (same as the Ideas agent path). The orchestrator still implements `_get_agent_model(agent_id)` for any code paths that need to read the configured model string from disk; it is not passed into the webhook payload. Model changes in `openclaw.json` take effect on the next invocation (config is re-read each time).

To change which model runs for a pipeline role, update that agent’s entry in `openclaw.json`. Remember that **session model is baked at session creation** — existing sessions keep their model until removed (see below).

### OpenClaw `thinking` on pipeline webhooks

`invoke_agent_webhook` adds `"thinking"` for **planner**, **executor**, and **reviewer** only (default level `"medium"` in `webhook_client.py`), so MiniMax M2.7 gets an explicit OpenClaw thinking level on `POST /hooks/agent`. OpenClaw otherwise defaults MiniMax to `thinking: { type: "disabled" }` on the Anthropic-compatible path. **Escalation** calls omit `thinking` so local models stay on OpenClaw defaults. Set env `AUTODEV_PIPELINE_THINKING` to another OpenClaw level (`low`, `high`, …) or to an empty string to omit the JSON field entirely. Pass `thinking=` to `invoke_agent_webhook` to override or use `thinking=""` for a one-off omit on cloud agents.

### `apiKey: "no-key"` is mandatory for local providers

If the `llama-local` (or any local) provider in `openclaw.json` does not have an explicit `apiKey` field, OpenClaw inherits the `anthropic:default` auth profile. The local llama-server rejects this, and OpenClaw silently falls back to cloud Sonnet. **No error is shown.** The only way to detect this is checking `fallbackNoticeReason: auth` in `~/.openclaw/agents/{agent}/sessions/sessions.json`.

Fix: ensure every local provider entry in `openclaw.json` includes `"apiKey": "no-key"`.

### Session model is baked at creation time

A session's model is set when the session is created. Restarting the OpenClaw gateway or changing `openclaw.json` does not affect existing sessions. To force a session to use a new model: stop the gateway, delete the session entry (and its `.jsonl` file) from `sessions.json`, restart the gateway.

### Qwen model parameter requirements

Qwen3.5-27B and Qwen3-Coder-Next both require specific llama-server flags to suppress thinking token generation. Without `--chat-template-kwargs {"enable_thinking":false}` and `--reasoning-budget 0`, `<think>` tokens are emitted inline and corrupt JSON tool call parsing. These flags must be present in the llama-server startup command, not in the Python code.

---

## The MiniMax File Deletion Bug and Its Guard

**The bug:** MiniMax M2.x models delete existing project files when approaching their context window limit. This is not a gate misconfiguration — the model actively removes files as a context-management strategy.

**The guard:** `executor_gate.py` runs `git diff --diff-filter=D` after the executor completes to detect files that were in the manifest but are no longer present. If any unaccounted deletions are found, the gate returns `ERR_UNACCOUNTED_DELETION` (exit 1 with this error code in the JSON). The executor retry mechanism then creates a fresh session and retries.

**Do not remove or weaken this check.** It is the only automated defence against the model silently destroying project state. If `executor_gate.py`'s git diff check is removed, MiniMax will occasionally leave the project repository in an irreparable state mid-pipeline.

**Executor retry and OpenClaw:** When `executor_retries` > 0 and the orchestrator is about to invoke attempt N+1, it first calls `abort_agent_session()` (best-effort, with the 3x retry loop described above) to stop attempt N in the OpenClaw gateway via WebSocket `sessions.abort`, so the prior run does not keep streaming or refreshing `executor_activity.stamp` after the orchestrator has moved on. If the abort succeeds but `verify_session_stopped` reports the stamp is still advancing, the orchestrator emits `abort_verify_failed` and launches attempt N+1 anyway (soft-continue, same contract as `_handle_stall_outcome`).

---

## Session and State Management Rules

### Session key format

All session keys must start with `pipeline:` — this is enforced by `allowedSessionKeyPrefixes` in `openclaw.json`. The orchestrator uses:

```
pipeline:phase-{phase_number}:{raw_id}:{agent}-attempt-{N}
```

Example: `pipeline:phase-2:CORE-1:executor-attempt-1`

OpenClaw normalises session keys to lowercase internally. The orchestrator accounts for this when looking up sessions.

### Separate session key per retry

Each retry attempt uses a distinct session key. This prevents the failure history from attempt 1 from polluting the agent context in attempt 2. Never reuse a session key across retry attempts for the same phase.

### `pipeline_state.json` reset checklist

When resetting for a fresh run, all of these must be set:

```json
{
  "pipeline_status": "IDLE",
  "current_agent": "planner",
  "current_phase": 0,
  "current_phase_raw_id": ""
}
```

Also delete all pipeline metadata files in the project directory: `*.done`, `current_phase.json`, `planner_output.json`, `executor_output.json`, `reviewer_output.json`, `failure_context.json`, any `phase_state_????????` temp files. Delete old phase branches (`phase/N`) for a clean re-run.

### Atomic write rule for all output files

All files written by the orchestrator that will be read by a downstream step must use `mkstemp` + `os.replace`. The sentinel (`.done`) file must be written **after** the payload file. Never reverse this order — a reader finding the sentinel before the payload is ready produces a race condition. This applies to: `pipeline_state.json`, `phase_state.json`, `current_phase.json`, any gate output files.

---

## Operational Constants

These values appear throughout the codebase. Do not change them without understanding all downstream effects.

| Constant | Value | Where it matters |
|----------|-------|-----------------|
| Planner sentinel backstop | 4500 seconds (75 min) | `poll_for_sentinel(timeout_seconds=4500)` — infrastructure-failure backstop; normal completion comes from `agent_end` |
| Executor sentinel backstop | 4500 seconds (75 min) | `poll_for_sentinel(timeout_seconds=4500)` — infrastructure-failure backstop; normal completion comes from `agent_end` |
| Reviewer sentinel backstop | 4500 seconds (75 min) | `poll_for_sentinel(timeout_seconds=4500)` — infrastructure-failure backstop; caps at 3 failed polls then escalates |
| Heartbeat cron interval | 30 minutes | `cron/jobs.json`, `heartbeat_cron.py` |
| Pipeline lock file | `pipeline.lock` | `fcntl.flock`, advisory, exclusive |
| SSE heartbeat | 15 seconds | `/api/events/stream` keep-alive |
| Event ring buffer size | 50 entries | In-memory, not persisted across server restart |
| Escalation reset cap | 3 resets | `escalation_resets` counter in `phase_state.json`; UI disables command buttons at ≥ 3 |
| Nuclear reset cap (P1 Stage G2) | 2 resets | `nuclear_resets` counter in `phase_state.json`, governing the `NUCLEAR_RESET` command (`nuclear_reset_phase()`, a thin destructive wrapper over `reset_phase()`). **Independent of `escalation_resets`** — `NUCLEAR_RESET` is NOT in `RESET_CAP_COMMANDS`. The dashboard renders the "Reset Everything & Restart Phase" button **only** when `escalation_resets >= 3` (available precisely once the normal recover budget is spent) and hides it at `nuclear_resets >= 2`. `reset_phase()` preserves `nuclear_resets` + `reset_log` (alongside `escalation_resets`) so the cap accumulates and the audit trail survives; all three zero only on genuine phase advance. Server enforces only the `nuclear_resets >= 2 → 409` cap (the `escalation_resets >= 3` visibility is UI-side). |
| Reviewer contract-shape retry cap (P1 Stage D) | 2 retries | `reviewer_unverified_retries` counter in `phase_state.json`, pooled across `VISUAL_UNVERIFIED` / `BEHAVIORAL_UNVERIFIED` / `REGRESSION_UNVERIFIED`. Single parameterised orchestrator handler escalates when the pool hits the cap. Independent of `reviewer_retries` so contract-shape failures do not burn code-quality retry slots. Replaces the prior per-flavour counters; pooling is the anti-sprawl design now that a third contract-shape verdict exists |
| Behavioural evidence anchors (min on `verdict: "pass"`) | 3 anchors | `_MIN_BEHAVIORAL_EVIDENCE_ANCHORS` in `reviewer_gate.py`; `_check_behavioral_verification` rejects shorter evidence arrays. Hard rule, not configurable per-project |
| Executor lifetime retry counters (P0 Stage H) | `executor_self_failure_retries`, `executor_reviewer_rejection_retries` | Both in `phase_state.json`, both accumulate across the whole phase. The legacy `executor_retries` field stays as the per-segment budget (resets on reviewer rejection). The two new counters never reset on reviewer rejection or escalation — only on `reset_phase()`. Feed the canonical metrics row so the invariant `executor_attempts == executor_self_failures + executor_reviewer_rejections + 1` holds across reviewer-driven re-runs. `reset_execution('auto')` increments `executor_self_failure_retries`; the orchestrator's ROUTE_EXECUTOR handler increments `executor_reviewer_rejection_retries` |
| `_current_attempt_retry_class` (P0 Stage H) | `"initial_attempt"` / `"executor_self_failure"` / `"reviewer_rejection"` | Process-local tracker on `Orchestrator` set by `reset_phase` (initial), `reset_execution('auto')` (self-failure), and the ROUTE_EXECUTOR handler (rejection). Stamped onto every `gate_fail` and `attempt_end` event's `detail.retry_class` so the UI activity feed can distinguish retry sources |
| Session TTL | 30 days | `session_cleanup.py`; escalation sessions are exempt |
| UI server port | 18790 | `DEFAULTS["port"]`; OpenClaw gateway is on 18789 |
| Webhook endpoint | `http://localhost:18789/hooks/agent` | `DEFAULTS["hooks_url"]`; requires Bearer token |
| `gateway_token` / `gateway_ws_url` | from `openclaw.json` → `gateway.auth.token` and `gateway.port` | Orchestrator `load_config()`; used by `abort_agent_session()` to authenticate Gateway WebSocket `sessions.abort` before a new executor attempt. Distinct from `hooks.token` (Bearer for `/hooks/agent`). |
| Base branch override | optional `base_branch` config key (empty = auto-detect) | Used by orchestrator git checkout/reset paths, `/api/pipeline/git-recover`, and `GET /api/state` field **`git_recover_suggested_branch`** (UI prefills the recover dialog). **`git-recover`** stashes (including untracked) then **`git checkout`** — it does not run **`git reset`**. |
| `prd-creator` agent ID | `"prd-creator"` | `WEBHOOK_AGENT_ID` in `ui/server.py` — used in all idea-to-PRD webhook calls |
| `AUTODEV_LLAMA_BASE` | default `http://127.0.0.1:11434` | Orchestrator `check_traffic_cop_health`, `wait_for_model_stable`, blame L1, and `heartbeat_cron.py` — HTTP origin when `openclaw.json` has no `llama-local` `baseUrl` |
| `AUTODEV_AUDIT_ARCHIVE_DIR` | unset → `$OPENCLAW_ROOT/pipeline-audit`; empty string → disabled | Phase-complete snapshot copies in `orchestrator.py` |
| `AUTODEV_HOOKS_TOKEN` | optional | Overrides `hooks_token` from `ui/config.json` / `DEFAULTS` for UI → OpenClaw webhook calls |
| `agents.list[].bootstrapMaxChars` | `32000` (all six AutoDev agents) | Per-file bootstrap injection cap in `openclaw.json`. OpenClaw's default is `12000`, which truncated every pipeline role's `AGENTS.md` (planner 15.5k, executor 20.5k, reviewer 23k) and silently dropped the Stage A `## Always-Apply:` rules (they begin past byte ~10k). Seeded for new agents by `register_agent._build_new_entry`; ensured on existing agents (and the live config) by `setup_helpers.ensure_openclaw_context_limits` (install.sh step 8). Do not lower below the largest `AGENTS.md`. |
| `agents.list[].contextLimits.postCompactionMaxChars` | `8000` (planner/executor/reviewer only) | After a context compaction, OpenClaw re-injects only the `postCompactionSections` of `AGENTS.md`, capped per-agent here (OpenClaw default `1800`). The two Always-Apply sections measure ≤4.6k combined; 8k holds them with headroom. Pipeline roles only — they are the agents that carry the Always-Apply sections. Guarded by `test_postcompaction_cap_covers_largest_always_apply_block`. |
| `agents.defaults.compaction.postCompactionSections` | `["Always-Apply: Integration Wiring", "Always-Apply: Testing Quality", "Session Startup", "Red Lines"]` | Global-only (the schema has no per-agent `compaction` block). Names the `AGENTS.md` H2 sections the post-compaction refresh re-injects. OpenClaw's default `["Session Startup","Red Lines"]` matches **no** header in our `AGENTS.md`, so without this the Always-Apply rules are dropped on every compaction. Our two headers are seeded first; OpenClaw's defaults are preserved. Drift-guarded against the real headers by `test_postcompaction_sections_match_real_agents_md_headers`. |
| `AUTODEV_STALL_TIMEOUT_PLANNER` | default `300` (seconds) | **Post-first-hook** silence: max silence on `planner_activity.stamp` mtime *after* the plugin has touched it at least once, before `poll_for_sentinel` returns `PollResult(False, "stalled")`. Catches mid-turn model deaths. Independent of startup grace below. |
| `AUTODEV_STALL_TIMEOUT_EXECUTOR` | default `300` | Same for executor poll. Was `1800` before the two-knob split; the longer value (which forced 30 min to also cover slow boots) caused the CORE-E6 mid-turn-silence-undetected pattern. |
| `AUTODEV_STALL_TIMEOUT_REVIEWER` | default `300` | Same for reviewer poll. |
| `AUTODEV_STARTUP_GRACE_PLANNER` | default `600` (seconds) | **Pre-first-hook** wait: how long `poll_for_sentinel` tolerates a non-advancing stamp before declaring `PollResult(False, "no_first_activity")`. Catches OpenClaw session-creation hangs and provider-auth failures distinct from mid-turn stalls. |
| `AUTODEV_STARTUP_GRACE_EXECUTOR` | default `600` | Same for executor poll. |
| `AUTODEV_STARTUP_GRACE_REVIEWER` | default `600` | Same for reviewer poll. |
| `AUTODEV_IDEAS_IDLE_THRESHOLD` | default `300` (seconds) | **Post-first-activity** silence threshold for the Ideas chat poll: max silence on `prd_creator_activity.stamp` mtime *after* the plugin has touched it at least once, before `_poll_sentinel_with_idle_detect` returns `PollResult(False, "stalled")`. Mirrors the pipeline's `AUTODEV_STALL_TIMEOUT_*` knobs. **300 s, not 120 s:** OpenClaw delivers model calls opaquely (`model_call_started` → silence → `model_call_ended`, no reliable mid-call event for the OpenRouter path), so a single thorough PRD-draft call runs with the stamp silent for its whole duration — a 118 s silent draft was measured live. The threshold must exceed the longest legitimate single model call. **There is no Ideas startup-grace knob:** the chat send passes `startup_grace=None`, so a slow cold start is never declared a *premature* `no_first_activity` timeout. The only definitive Ideas-poll timeout signals are `stalled` (this knob) and the `poll_timeout` backstop — the UI keys its text-revert off those, never a frontend timer. (The pipeline's separate `poll_for_sentinel` still uses `AUTODEV_STARTUP_GRACE_*`; only the Ideas chat opted out.) |

### `pipeline.lock` locking mechanism

The lock uses `fcntl.flock(fd, LOCK_EX | LOCK_NB)`. This is an **advisory lock**, not a PID file. Liveness is determined by attempting to acquire the lock — if successful, the holder is dead. This is immune to PID reuse (a new process with the same PID will not have the file descriptor open). Do not replace this with PID-file locking.

`heartbeat_cron.py` only queries the traffic cop when the pipeline lock is **free**. During an active pipeline run, the cron does nothing. This prevents unnecessary GPU load on the traffic cop machine during active runs.

---

## Security Constraints on Agent Tool Policies

These constraints are defined in `openclaw.json` under `agents.list[].tools` and must not be changed without deliberate review.

- **Escalation agent**: Restricted to `read` and `write` only. It must not have `edit`, `exec`, `browser`, or `apply_patch` permissions. The escalation agent communicates with the human via Signal and routes commands back to the orchestrator — it does not modify code.
- **Executor gate**: Validates that all file paths in the executor's file manifest stay within the project directory boundary (`os.path.realpath` comparison). This prevents path traversal attacks and accidental writes outside the project. Do not weaken or skip this check.
- **Auth profiles**: If `~/.openclaw/agents/` is recreated from scratch, `auth-profiles.json` must be regenerated. Without it, API calls fail silently and OpenClaw may fall back to the wrong provider.

---

## Install and Deployment

```bash
./install.sh          # Linux and macOS (POSIX fcntl); WSL2 supported
```

The script writes `.env` with the canonical names only (`OPENCLAW_ROOT`, `AUTODEV_PIPELINE_ROOT`, `AUTODEV_REPO_PATH`). It then appends a **comment-only** Tier A stall block (`AUTODEV_STALL_TIMEOUT_*` placeholders) once per file so operators see those knobs without enabling them. Legacy aliases (`AUTODEV_ROOT`, `AUTODEV_RUNTIME_ROOT`) have been removed and are ignored at runtime. Source it before running any pipeline code:

```bash
source .env
uvicorn ui.server:app --host 127.0.0.1 --port 18790
```

The UI API has **no authentication**; prefer loopback binding. See **Security and network exposure** in [SETUP.md](SETUP.md) before using `--host 0.0.0.0`.

Agent identity docs (`IDENTITY.md`, `SOUL.md`, etc.) are deployed by `install.sh` step 5 from `autodev/agents/{agent}/` into `~/.openclaw/workspace-{agent}/` (skipped when destination is already newer). The source of truth for these files is now this repo, not `~/.openclaw/workspace-*`.

### Service file drift guard

**When you change `ui/autodev-ui.service`, also update `ui/com.autodev.ui.plist` (and vice versa).** The two declare equivalent `WorkingDirectory`, executable, restart policy, and log paths. The static-lint tests `tests/test_infra3_systemd_unit.py` and `tests/test_infra3_launchd_plist.py` enforce structural parity — a change to one that is not reflected in the other will fail CI.

---

## Queue System

`~/.openclaw/pipeline_queue.json` holds the project queue. All writes are atomic (`mkstemp + os.replace`). The queue is managed by `ui/server.py` endpoints (`/api/queue/*`) and by `orchestrator.py` methods (`_read_queue`, `_write_queue`, `_select_next_queue_project`, `_queue_update_active_entry`).

**Single-writer assumption:** The queue file is written by two parties — the UI server (human-initiated moments: add, reorder, parent, remove) and the orchestrator (state transitions). These two writers operate in alternating windows (UI writes while pipeline is idle; orchestrator writes while running) and are **not** protected by an explicit file lock. This is safe at the current risk level. If you add a third writer or allow concurrent UI + orchestrator writes, add an advisory `flock` or a version/ETag field before relaxing this assumption.

### Orchestrator lightweight preflight (`_queue_preflight`)

The orchestrator's `_queue_preflight()` checks: directory exists, `.git` present, `roadmap*.md` present. This is intentionally lighter than the server's `_run_preflight_checks()`, which also validates symlink, `.gitignore`, agent workspace files, etc. **Known MVP limitation:** a project that passes `_queue_preflight` may still fail mid-pipeline if the full server-side preconditions are not met. The server's `_run_preflight_checks` is used at queue-add time and at `trigger-next` time; the orchestrator's check runs only on auto-advance between queue entries.

### DEPENDENCY_HOLD state

`DEPENDENCY_HOLD` is a valid entry state. It is applied:
- **Server-side**: when a parent is assigned via `PATCH /api/queue/{entry_id}/parent` (if parent is not COMPLETED), and when a project is added via `POST /api/queue/add` with a non-COMPLETED parent.
- **Orchestrator-side**: enforced in `_select_next_queue_project` before activating a queued project.

Clearing a parent (`parent_id: null`) via the API restores a `DEPENDENCY_HOLD` child to `READY`.

### `ESCALATION_ANSWERED` entry state (P1 Stage H — parked-escalation revival)

`ESCALATION_ANSWERED` is a queue-**entry** state (a `pipeline_queue.json` entry's `state`), **not** a `pipeline_status` — it is deliberately absent from `VALID_STATES` (those eight govern `pipeline_state.json`). It is the missing link that closes the auto-queue **bank → revive → apply** loop: when a project escalates under an auto-queue it parks as `ESCALATION` and the queue advances; the operator banks an answer (the server writes only the per-project `pending_escalation_command.json`); on the next selection the **orchestrator** promotes that row `ESCALATION → ESCALATION_ANSWERED` and **revives** it. The full entry-state set is now: `READY, SKIPPED_PENDING, ACTIVE, DEPENDENCY_HOLD, ESCALATION, ESCALATION_ANSWERED, BLOCKED, COMPLETED, FAILED`.

- **Orchestrator-owned flip (single-writer preserved).** `_promote_answered_escalations` (a pre-pass at the top of `_select_next_queue_project`, also called by the recovery hook) is the only writer of this transition. The server still writes **only** the per-project pending file — it never writes `pipeline_queue.json` for this path — so the two-writer model above is unchanged (no new locks). `ESCALATION_ANSWERED` is in `queue_semantics.PARENT_BLOCKS_CHILD_STATES` (an answered-but-not-yet-resumed parent has not COMPLETED, so children still hold).
- **Restore, don't restart.** Because `pipeline_state.json` is global and selection resets it to a blank phase-0/planner state for a fresh start, `_queue_park_active_entry` snapshots the escalated phase pointer into the entry's `parked_state_snapshot` (`current_phase`, `current_phase_raw_id`, the five retry counters, `phase_base_commit`, `phase_start_time`). The revival branch restores that snapshot **instead of** the phase-0 reset, so the banked command (`RESET_PHASE` / `PROCEED` / `SKIP` / …) acts on the *escalated* phase. `phase_base_commit` is load-bearing — `reset_phase()` guards its `git reset --hard` on it. `escalation_resets`/`reset_log` are **not** snapshotted (they live in the per-project `phase_state.json`, which survives via the symlink). **Invariant:** in the activation block `update_symlink` runs first and is shared by both the revival and fresh-start paths — the branch splits only the `self.state` write — so the restore + the banked command always act on the *revived* project's repo, never the previously-active one. A *pre-phase* escalation (repo-init / phase 0) parks with an empty snapshot — correct-by-design: there is no escalated phase, so the revival restores phase 0 and re-resolves.
- **UI surfacing of an un-promoted bank.** The promotion is orchestrator-owned, so when the orchestrator is dead (the common `QUEUE_HALTED`-then-bank case) a banked row stays `ESCALATION` until the next selection. To keep the operator surface honest, `GET /api/queue` exposes a read-only `has_banked_answer` per parked-escalation entry (probes `pending_escalation_command.json`; never writes the queue), and the dashboard treats `ESCALATION + has_banked_answer` the same as `ESCALATION_ANSWERED` for the **Answer banked** pill (which wins over a stale `live_pipeline_status`), the **Resume banked answer** control, and the answered detail card.

### `QUEUE_HALTED` pipeline status

`QUEUE_HALTED` is one of the 8 valid `pipeline_status` values (see State Machine Rules). It is set by the orchestrator when all remaining queue entries are blocked, in dependency hold, or fail preflight. The `pipeline_state.json` also contains `queue_halted_reason: "all_blocked" | "all_dependency_hold" | "mixed" | "answered_pending_revival"`. The dashboard pill label is **Queue stalled**; the header **Queue: halted** chip is separate (see `QUEUE_HALTED` paragraph in State Machine Rules above).

**Two distinct halt moments (P1 Stage H reconciliation).** An *initial* halt with nothing pending exits cleanly: `_select_next_queue_project` finds no eligible row, transitions to `QUEUE_HALTED`, and the orchestrator returns. But a *restart into* `QUEUE_HALTED` is different — the persisted state carries `current_agent="escalation"`, so the startup function returns `enter_main_loop` without re-running selection, and `QUEUE_HALTED` is **deliberately not** in the main-loop exit set (`HALTED_SILENT`/`BLOCKED`/`PIPELINE_COMPLETE`) because the loop legitimately stays alive in `QUEUE_HALTED` to poll for an *in-place* escalation answer to the last project (`_should_invoke_escalation_agent` treats it like `WAITING_FOR_HUMAN`). Before Stage H, that restart path therefore polled `escalation_output` forever when the only pending answer was a *deferred* bank (in `pending_escalation_command.json`, never converted). `Orchestrator._maybe_revive_on_queue_halted()` (run once at `run()` startup, before the gated startup function) is the restart-recovery: it promotes banked answers and revives a parked project; if there is genuinely nothing to consume it returns `False` and `run()` exits cleanly instead of spinning — but it continues into the loop when an in-place `escalation_output.done` is already pending (preserving the legacy in-place-answer recovery). `answered_pending_revival` (set ahead of `all_blocked` in both the orchestrator halt block and `_queue_trigger_next_halted_reason`) marks the queue as recoverable, not dead-stalled; the dashboard surfaces a **Resume banked answer** control that reuses `POST /api/queue/{entry_id}/relaunch`.

---

## Unresolved Items

### 1. ~~`_spawn_orchestrator` path construction in `ui/server.py`~~ — **RESOLVED**

`_spawn_orchestrator` now correctly constructs:

```python
orchestrator_script = os.path.join(autodev_repo_path, "autodev", "pipeline", ORCHESTRATOR_FILENAME)
```

`autodev_repo_path` should be set to the repo root (written by `install.sh` into `.env`). The workaround of pointing `autodev_repo_path` at `{repo_root}/autodev/pipeline` is no longer necessary or correct.

### 2. Multi-project switcher (`/api/setup/switch-project`)

`POST /api/setup/switch-project` has a test file (`tests/test_api_setup_switch_project.py`) and is implemented in `ui/server.py`. The `AUTODEV-UI-PRD.md` explicitly marks a multi-project switcher as a **v1 non-requirement**. It is unclear whether this endpoint was added intentionally during the build or was included as scaffolding. Before documenting it in `SETUP.md` or exposing it in the UI, confirm whether it is production-ready.

### 3. ~~`autodev_repo_path` DEFAULTS fallback~~ — **RESOLVED**

`DEFAULTS["autodev_repo_path"]` now falls back to `_AUTODEV_UI_ROOT` (i.e.
`os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))` — the repo root, two
levels up from `ui/server.py`). The stale `~/.openclaw` fallback was also removed from
`_spawn_orchestrator`. Fresh installs without `.env` sourced now correctly derive the
repo root from file location rather than assuming `~/.openclaw`.

---

## Key Reference Documents

Operator setup and dashboard terminology: **`SETUP.md`** and **`GLOSSARY.md`** at repo root. Technical specs in **`autodev/docs/`**:

| Document | What it is | When to read it |
|----------|-----------|-----------------|
| `PIPELINE-SPEC.md` | Architecture spec — state machine, gate interfaces, component behaviors, infrastructure topology | Before modifying orchestrator.py or any gate script |
| `PIPELINE-CONSTRAINTS.md` | Known issues, hardware limits, model-specific bugs, mitigations | Before changing model config, sentinel timing, or gate logic |
| `AUTODEV-UI-PRD.md` | Full product requirements for the dashboard | Before adding or modifying any API endpoint or UI behavior |
| `ASSUMPTIONS.md` | Resolved spec ambiguities, divergences from original design | When PIPELINE-SPEC and live code appear to contradict each other |
| `Dev_Roadmap_template-v3-...md` | Canonical roadmap format for target projects | When creating or validating a user project's roadmap |
