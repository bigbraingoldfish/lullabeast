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
2. **`ui/config.json`** — optional local overrides adjacent to `server.py` (not committed; copy from `ui/config.example.json`). If this file exists, any key it contains overwrites the corresponding DEFAULTS value. **`AUTODEV_HOOKS_TOKEN`** (environment) overrides `hooks_token` when set, so the webhook Bearer secret need not live in the file. **`AUTODEV_IDEAS_IDLE_THRESHOLD`** and **`AUTODEV_IDEAS_STARTUP_GRACE`** (environment) override `ideas_idle_threshold` and `ideas_startup_grace` when set (Ideas chat sentinel poll).

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

`QUEUE_HALTED` is written when the project queue is active but all remaining entries are blocked, in dependency hold, or fail preflight. The `pipeline_state.json` will additionally contain `queue_halted_reason: "all_blocked" | "all_dependency_hold" | "mixed"`. In the dashboard, the **pipeline status pill** for this state is labeled **Queue stalled** (`ui/index.html` `PIPELINE_LIVE_PILL`). Do not confuse that with the header **Queue: halted** *chip* (navigation to the queue when `queue_halted` is true) — different control, different copy. `POST /api/queue/trigger-next` returns `queue_halted_reason` when it cannot start a project so the queue UI can show a matching **Queue stalled — …** toast. The orchestrator exits cleanly; the queue screen shows which projects are blocked and why.

`pipeline_state.json` has **two** status fields: `status` and `pipeline_status`. Both must be updated together on any state transition — `transition_state()` writes both. Never update one without the other.

### `transition_state()` — the only correct way to change state

```python
def transition_state(self, new_status, action_description):
    ...
    self.write_state()  # atomically commits state before anything else happens
```

`write_state()` uses `mkstemp` + `os.replace` (atomic rename). The state file is committed **before** any action is taken. This is the write-then-act pattern: if the process crashes after writing state but before completing the action, crash recovery can resume from the committed state.

**Do not** call `self.state["pipeline_status"] = "..."` and then act without calling `write_state()`. Any code that updates state dict fields must end with a `write_state()` or `transition_state()` call.

### State reset protocol

When resetting `pipeline_state.json` to `IDLE` for a fresh run, set **both** `status` and `pipeline_status` to `"IDLE"`. Also set `current_agent` to `"planner"` and `current_phase` to `0`. If `current_agent` is `null`, the orchestrator exits immediately with `"Agent None logic not reached"`.

### Terminal states

`HALTED_SILENT`, `BLOCKED`, and `PIPELINE_COMPLETE` are checked at the top of the main loop and cause the orchestrator to exit cleanly. `STOPPED` is a clean halt triggered by the stop sentinel file. The orchestrator does **not** attempt recovery from these states — only a manual reset or operator command resumes the pipeline.

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
    timeout_seconds=2700,                # infrastructure backstop (gateway-dead failsafe)
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
3. On a successful abort, calls `verify_session_stopped` to confirm the agent stopped streaming. If it has not (gateway acknowledged abort but stamp still advancing), the helper emits the `[ABORT][VERIFY_FAILED]` print and the `abort_verify_failed` pipeline event (so the activity feed shows the situation in red) and then **soft-continues** — the orchestrator launches the next attempt anyway. Rationale: 90%+ of long runs eventually resolve on retry, whereas a forced `HALTED_SILENT` state always requires human intervention. The `"timeout"` reason — the 45-minute infrastructure backstop firing — now also routes through this same helper; previously it bypassed abort+verify and let attempt N+1 launch on top of a still-streaming attempt N (the original CORE-E6 cascade). The retry-start abort block (`orchestrator.py:3916`) follows the same soft-continue contract.

**Activity stamp bootstrap**: After `cleanup_output_files()` removes stale output and before the webhook is invoked, call `_init_activity_stamp_or_halt(agent)`. The return value is checked — a False result (workspace dir unwritable) transitions the orchestrator to `HALTED_SILENT` rather than silently disabling stall detection. The plugin refreshes this stamp on `model_call_started`, `model_call_ended`, and `after_tool_call`.

**Plugin build & sessionKey shape (gotchas that bit us live).** The `autodev-pipeline-signals` plugin source lives in `autodev/plugin/`. OpenClaw ≥ 2026.5.x **refuses to load plugins from TypeScript source** — it requires a compiled `dist/index.js`. `install.sh` runs `npm install && npm run build` (esbuild bundle, see `autodev/plugin/package.json`) before `openclaw plugins install`; the resulting `dist/` and `node_modules/` are gitignored. Verify the plugin actually loaded by grepping the gateway journal for `http server listening (N plugins: autodev-pipeline-signals, …)` — if the name is missing, the gateway is running blind and the activity stamp will never refresh. Separately: the gateway delivers `hookCtx.sessionKey` with the OpenClaw `agent:{role}:` prefix (e.g. `agent:executor:pipeline:phase-4:ui-e1:executor-attempt-1`), so `isPipelineSession` in `autodev/plugin/src/utils.ts` matches both `pipeline:…` and `agent:*:pipeline:…`. Removing either branch causes the stamp to silently stop refreshing in production while unit tests (which use the bare `pipeline:…` form) keep passing.

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
| `phase_complete`           | Canonical metrics row written (post-merge)                        | `{executor_attempts, blame_fires}`                                                                   | pre-existing |
| **`poll_start`**           | Before each `poll_for_sentinel()` invocation (3 sites)             | `{startup_grace, stall_threshold, infra_backstop, session_key, attempt}`                              | 6.1.a |
| **`poll_outcome`**         | After `poll_for_sentinel()` returns (3 sites)                      | `{reason, stamp_mtime, duration_s, session_key, attempt}`                                            | 6.1.a |
| **`attempt_end`**          | Companion to `[ATTEMPT_END]` dense print (3 sites)                  | `{reason, duration_s, attempt, session_key, retry_class}` (Stage H added `retry_class`)               | 6.3 + Stage H |
| **`abort_attempted`**      | After every `abort_agent_session()` call (retry-start + inline)    | `{session_key, result, agent_role, reason, source}`                                                   | 6.1.b |
| **`abort_verify_failed`**  | When `verify_session_stopped()` returns `False` (no longer halts — soft-continue) | `{session_key, stamp_path, agent_role, reason}`                                                       | 6.1.b |
| **`reviewer_verdict`**     | On every reviewer-gate consumption                                 | `{verdict, pass_number, next_agent}`                                                                  | 6.1.c |
| **`stamp_init_failed`**    | When `_init_activity_stamp_or_halt` returns False                  | `{agent_role, stamp_path, reason}`                                                                    | 6.1.d |

The bold entries are Section 6 additions; existing UI consumers handle them transparently because the JSONL schema is additive.

### Phase-state outcome fields (Section 6.4)

`phase_state.json` now also persists the latest poll/abort outcome so a restarted orchestrator and the dashboard can render "what happened last" without scraping logs. Written by `_record_phase_outcome(**fields)` (defined near `write_phase_state_atomic`):

| Field                  | Values                                                                |
|------------------------|-----------------------------------------------------------------------|
| `last_poll_reason`     | `succeeded` / `stalled` / `no_first_activity` / `stopped` / `timeout` |
| `last_abort_result`    | `ok` / `FAILED` / `verify_failed`                                     |
| `last_attempt_summary` | Dense one-line string mirroring the `[ATTEMPT_END]` log line          |

### In-poll heartbeat (Section 6.2)

`poll_for_sentinel()` accepts `heartbeat_interval_seconds` (60 s in the three orchestrator call sites). During a long wait it prints one line per interval:

```
[POLL][HEARTBEAT] elapsed=120s stamp_age=12s checked_in=True
```

This distinguishes "alive, agent making progress" from "alive, agent stopped" from "orchestrator hung" — the three states that were indistinguishable in the pre-Section-6 logs.

### Metrics history file (Section 6.0)

`metrics.jsonl` write history is preserved in an orchestrator-private append-only file at `$AUTODEV_PIPELINE_ROOT/metrics_history/<project_name>.jsonl`, written by `_write_canonical_metrics_row()`. The agent cannot reach this directory; even if the executor overwrites the project's `metrics.jsonl` to a single row, the orchestrator rebuilds the full history on the next phase completion. On first deploy the file is bootstrapped from the live `metrics.jsonl` so existing history is preserved across the upgrade.

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

Skill files live in `autodev/skill-library/{discipline}/{agent_role}/SKILL.md`. There are 27 files: 9 disciplines × 3 roles.

**The 9 disciplines:** `api-service`, `auth-security`, `cli-tooling`, `core-logic`, `data-persistence`, `infra-config`, `integration-wiring`, `testing-quality`, `ui-frontend`.

**Base skills (always injected, P1 Stage A).** Two of the nine disciplines — `integration-wiring` and `testing-quality` — are written to the agent workspace on **every phase regardless of the phase prefix**. They are the universal-rule layer (integration-wiring's "read the entrypoint before wiring" rule and testing-quality's TDD discipline). The phase-prefix discipline is layered on top when the prefix maps. See `SkillManager.BASE_DISCIPLINES`.

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

After P1 Stage A, the directory holds **two or three** subdirectories per phase: the two base skills always, plus the phase-prefix discipline when one maps. Example for a `CORE-E2` executor phase:

```
~/.openclaw/workspace-executor/skills/integration-wiring-executor/SKILL.md
~/.openclaw/workspace-executor/skills/testing-quality-executor/SKILL.md
~/.openclaw/workspace-executor/skills/core-logic-executor/SKILL.md
```

`_clean_workspace_skills()` runs **exactly once at the start of every `inject_skill()` call** (`shutil.rmtree` + `os.makedirs`). All base + prefix skills are written into the freshly prepared directory; no per-skill cleanup. This ensures no stale skill from a previous phase can survive into the next phase. OpenClaw's `loadWorkspaceSkillEntries` walks the entire `skills/` tree at session start and surfaces every `SKILL.md` it finds — that's why writing multiple subdirectories per phase works without any OpenClaw configuration change.

### The `~/.openclaw/skills/` (global tier) is intentionally untouched

`skill_manager.py` only writes to `workspace-{agent}/skills/`. The global tier at `~/.openclaw/skills/` would load all 27 skills simultaneously, which is not the intent. Per-phase injection via workspace-level skills is the design.

### Conditions under which injection is silently skipped

The kill switches suppress **everything** (base + prefix); the per-skill skip cases suppress only the *prefix* skill (the base skills still inject in the same call).

**Kill switches — no skills injected at all (workspace ends up empty):**
1. `pipeline.skills.enabled` is `false` in `openclaw.json`
2. `pipeline.skills.{agent_role}_skills_enabled` is `false`
3. `_clean_workspace_skills()` failure (workspace directory unwritable)
4. PyYAML not installed (mapping disabled; base skills also need no mapping but the missing-PyYAML log warns at construction — base skills do still inject in this case)

**Prefix-skip cases — base skills inject; only the prefix is skipped:**
5. `phase_raw_id` is empty
6. Subsystem prefix has no entry in `skill_mapping.yaml`
7. `autodev/skill-library/{discipline}/{role}/SKILL.md` does not exist on disk (prefix or base — a missing base-skill source skips only that one base discipline; the others and the prefix still inject)
8. `OSError` during file copy (per-skill, not per-call)

In all cases a `[SKILL]` log line is emitted to stdout with `Status=loaded base=true|false`, `Status=none_mapped`, `Status=disabled`, or `Status=none_found`. Operators can grep for `base=true|false` to distinguish base-skill outcomes from prefix-skill outcomes.

`phase_state.skill_injected` (consumed by metrics row, UI label, snapshot endpoint) intentionally records only the **variable phase-prefix discipline** — base skills are filtered out by `_record_injected_skill()` because surfacing constant values would be reporting noise.

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
  "status": "IDLE",
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
| Planner sentinel backstop | 3600 seconds | `poll_for_sentinel(timeout_seconds=3600)` — infrastructure-failure backstop; normal completion comes from `agent_end` |
| Executor sentinel backstop | 7200 seconds | `poll_for_sentinel(timeout_seconds=7200)` — infrastructure-failure backstop; normal completion comes from `agent_end` |
| Reviewer sentinel backstop | 3600 seconds | `poll_for_sentinel(timeout_seconds=3600)` — infrastructure-failure backstop; caps at 3 failed polls then escalates |
| Heartbeat cron interval | 30 minutes | `cron/jobs.json`, `heartbeat_cron.py` |
| Pipeline lock file | `pipeline.lock` | `fcntl.flock`, advisory, exclusive |
| SSE heartbeat | 15 seconds | `/api/events/stream` keep-alive |
| Event ring buffer size | 50 entries | In-memory, not persisted across server restart |
| Escalation reset cap | 3 resets | `escalation_resets` counter in `phase_state.json`; UI disables command buttons at ≥ 3 |
| Reviewer visual-contract retry cap | 2 retries | `reviewer_visual_retries` counter in `phase_state.json`; `VISUAL_UNVERIFIED` orchestrator handler escalates beyond this. Independent of `reviewer_retries` so a contract-shape failure does not burn a code-quality retry slot |
| Reviewer behavioural-contract retry cap | 2 retries | `reviewer_behavioral_retries` counter in `phase_state.json`; `BEHAVIORAL_UNVERIFIED` orchestrator handler (added P0 Stage F) escalates beyond this. Independent of `reviewer_retries` and `reviewer_visual_retries` — three orthogonal budgets covering main-quality, visual-shape, and behavioural-shape failure modes |
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
| `AUTODEV_STALL_TIMEOUT_PLANNER` | default `300` (seconds) | **Post-first-hook** silence: max silence on `planner_activity.stamp` mtime *after* the plugin has touched it at least once, before `poll_for_sentinel` returns `PollResult(False, "stalled")`. Catches mid-turn model deaths. Independent of startup grace below. |
| `AUTODEV_STALL_TIMEOUT_EXECUTOR` | default `300` | Same for executor poll. Was `1800` before the two-knob split; the longer value (which forced 30 min to also cover slow boots) caused the CORE-E6 mid-turn-silence-undetected pattern. |
| `AUTODEV_STALL_TIMEOUT_REVIEWER` | default `300` | Same for reviewer poll. |
| `AUTODEV_STARTUP_GRACE_PLANNER` | default `600` (seconds) | **Pre-first-hook** wait: how long `poll_for_sentinel` tolerates a non-advancing stamp before declaring `PollResult(False, "no_first_activity")`. Catches OpenClaw session-creation hangs and provider-auth failures distinct from mid-turn stalls. |
| `AUTODEV_STARTUP_GRACE_EXECUTOR` | default `600` | Same for executor poll. |
| `AUTODEV_STARTUP_GRACE_REVIEWER` | default `600` | Same for reviewer poll. |
| `AUTODEV_IDEAS_IDLE_THRESHOLD` | default `120` (seconds) | Ideas chat `_poll_sentinel_with_idle_detect`: max silence on `prd_creator_activity.stamp` mtime (`ui/server.py`; plugin touches stamp on model/tool events) |
| `AUTODEV_IDEAS_STARTUP_GRACE` | default `30` (seconds) | Ideas chat: wait for first stamp before treating missing session as `no_session` |

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

### `QUEUE_HALTED` pipeline status

`QUEUE_HALTED` is one of the 8 valid `pipeline_status` values (see State Machine Rules). It is set by the orchestrator when all remaining queue entries are blocked, in dependency hold, or fail preflight. The `pipeline_state.json` also contains `queue_halted_reason: "all_blocked" | "all_dependency_hold" | "mixed"`. The dashboard pill label is **Queue stalled**; the header **Queue: halted** chip is separate (see `QUEUE_HALTED` paragraph in State Machine Rules above).

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
