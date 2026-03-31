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
│   ├── config.json                 # Runtime config overrides (not committed if sensitive)
│   ├── requirements.txt            # fastapi, uvicorn, python-multipart, aiohttp
│   └── autodev-ui.service          # systemd unit file
├── tests/                          # UI server tests (~50 pytest files)
├── install.sh                      # Deployment script (10 steps, see SETUP.md)
├── SETUP.md                        # Human-facing setup guide
└── .env                            # Local path config (gitignored, written by install.sh)
```

### Architectural note: intentional single-file design

`ui/server.py` is deliberately a single large file (3,562 lines). The UI was built TDD across 23 phases; keeping all FastAPI routes in one file avoids cross-module import complexity in a single-process server. Do not split it into sub-modules without a deliberate refactoring decision.

`autodev/pipeline/orchestrator.py` is similarly monolithic by design: the pipeline state machine, all agent invocations, git operations, blame attribution, and escalation logic live together to make the control flow auditable in one place. Extracting pieces to helper modules requires understanding all the shared state.

---

## The Two Path Constants

Every pipeline file resolves two constants at module load time. Use these — never hardcode paths.

### `AUTODEV_ROOT`

```python
AUTODEV_ROOT = os.environ.get("AUTODEV_ROOT", os.path.expanduser("~/.openclaw"))
```

Points to the OpenClaw installation directory. **This is not the repo directory.** It is where OpenClaw lives: workspace directories, `openclaw.json`, `pipeline.lock`, `pipeline_state.json`, `pipeline-project` symlink.

Used for: `pipeline.lock`, `pipeline_state.json`, `pipeline-project` symlink, `workspace-{agent}/`, `openclaw.json`, `cron/jobs.json`.

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
ORCHESTRATOR_POLL_TIMEOUT = 120
```

---

## Config Dual-Source Rule (`ui/server.py`)

The UI server's configuration is a two-layer merge:

1. **`DEFAULTS` dict** (lines 362–375 of `ui/server.py`) — hardcoded fallbacks including all `~/.openclaw/…` path defaults.
2. **`ui/config.json`** — optional runtime overrides adjacent to `server.py`. If this file exists, any key it contains overwrites the corresponding DEFAULTS value.

`load_config()` applies this merge and expands `~` in all path values. Every endpoint that reads a file path calls `load_config()` (or receives the result) rather than referencing DEFAULTS directly.

The `autodev_repo_path` key in DEFAULTS reads from the `AUTODEV_REPO_PATH` environment variable first, then falls back to `~/.openclaw` (which is wrong after migration but preserved for backward compatibility). The correct value is the repo root. `install.sh` writes `.env` with the correct value; source `.env` before starting the server or set it in `ui/config.json`.

```json
{
  "autodev_repo_path": "/home/pi/projects/autodev-ui"
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

`QUEUE_HALTED` is written when the project queue is active but all remaining entries are blocked, in dependency hold, or fail preflight. The `pipeline_state.json` will additionally contain `queue_halted_reason: "all_blocked" | "all_dependency_hold" | "mixed"`. The Pipeline Monitor surfaces this state with an amber "Queue: halted" pill. The orchestrator exits cleanly; the queue screen shows which projects are blocked and why.

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

### Use `poll_for_sentinel_with_idle_detect()`, not `poll_for_sentinel()`

The primary function is `poll_for_sentinel_with_idle_detect()` in `sentinel_poller.py` (line 75). The simpler `poll_for_sentinel()` does not handle idle detection and is only used in minimal contexts.

### Critical parameters

```python
poll_for_sentinel_with_idle_detect(
    sentinel_path=...,
    timeout=600,                         # 600s hard timeout
    idle_threshold=120,                  # 120s idle = no recent file activity
    watch_dirs=[SYMLINK_TARGET],         # project directory, not just the JSONL file
    min_sentinel_mtime=time.time(),      # captured BEFORE cleanup_output_files()
)
```

**`watch_dirs`**: Pass `[SYMLINK_TARGET]` (the project directory). Idle detection resets on **any file write** in this directory, not just JSONL writes. This is required because MiniMax batches JSONL writes — an agent can be active for minutes with no JSONL output. Without `watch_dirs`, the poller declares idle too early and burns retry budget. Do not simplify this to JSONL-only detection.

**`min_sentinel_mtime`**: Capture `time.time()` **before** calling `cleanup_output_files()`. This timestamp is compared against the mtime of any `.done` file found. If the `.done` file is older than this timestamp, it is treated as orphaned output from a prior session and discarded. Without this guard, an orphaned session writing its `.done` file after the current attempt starts will burn the retry. The pattern in orchestrator.py is:

```python
_attempt_start_time = time.time()          # BEFORE cleanup
cleanup_output_files(SYMLINK_TARGET, "executor")
self.skill_manager.inject_skill(...)
self.transition_state("WAITING_FOR_SENTINEL", ...)
# ... invoke webhook ...
poll_for_sentinel_with_idle_detect(
    ..., min_sentinel_mtime=_attempt_start_time
)
```

**`idle_threshold=120`**: Do not raise this to work around JSONL silence. If the agent is truly active but writing no JSONL, fix `watch_dirs`. 120s is calibrated for active execution with file-write monitoring.

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
AUTODEV_ROOT/workspace-{agent_role}/skills/{discipline}-{agent_role}/SKILL.md
```

Example: `~/.openclaw/workspace-executor/skills/core-logic-executor/SKILL.md`

`_clean_workspace_skills()` runs **before** every injection (including no-op cases). It does `shutil.rmtree` followed by `os.makedirs`. This ensures no stale skill from a previous phase can survive into the next phase. OpenClaw loads `workspace/skills/{name}/SKILL.md` at session start.

### The `~/.openclaw/skills/` (global tier) is intentionally untouched

`skill_manager.py` only writes to `workspace-{agent}/skills/`. The global tier at `~/.openclaw/skills/` would load all 27 skills simultaneously, which is not the intent. Per-phase injection via workspace-level skills is the design.

### Conditions under which injection is silently skipped

`inject_skill()` always cleans the workspace skills directory first (removing prior skill), then returns early (injecting nothing) if any of these are true:
1. `pipeline.skills.enabled` is `false` in `openclaw.json`
2. `pipeline.skills.{agent_role}_skills_enabled` is `false`
3. `phase_raw_id` is empty
4. Subsystem prefix has no entry in `skill_mapping.yaml`
5. `autodev/skill-library/{discipline}/{role}/SKILL.md` does not exist on disk
6. `OSError` during file copy
7. PyYAML not installed

In all cases a `[SKILL]` log line is emitted to stdout with `Status=none_mapped`, `Status=disabled`, or `Status=none_found` before returning.

---

## Agent LLM Configuration and Known Failure Modes

### How models are resolved

**Planner and reviewer** use `_get_agent_model(agent_id)`, which reads `agents.list[].model.primary` from the live `openclaw.json`. Model changes in `openclaw.json` take effect on the next invocation (config is re-read each time).

**Executor** is hardcoded at line 1317 of `orchestrator.py`:

```python
model = "openrouter/minimax/minimax-m2.7"
```

This overrides `openclaw.json` for all executor attempts. The current configuration uses OpenRouter MiniMax for all executor runs (no local model or cloud Sonnet fallback — the previous three-attempt-tier architecture was replaced with a single cloud model). Do not remove this hardcode without a deliberate decision and update to the model-selection logic.

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
| Sentinel timeout | 600 seconds | `poll_for_sentinel_with_idle_detect(timeout=600)` |
| Idle threshold | 120 seconds | `idle_threshold=120` — with `watch_dirs` active |
| Heartbeat cron interval | 30 minutes | `cron/jobs.json`, `heartbeat_cron.py` |
| Pipeline lock file | `pipeline.lock` | `fcntl.flock`, advisory, exclusive |
| SSE heartbeat | 15 seconds | `/api/events/stream` keep-alive |
| Event ring buffer size | 50 entries | In-memory, not persisted across server restart |
| Escalation reset cap | 3 resets | `escalation_resets` counter in `phase_state.json`; UI disables command buttons at ≥ 3 |
| Session TTL | 30 days | `session_cleanup.py`; escalation sessions are exempt |
| UI server port | 18790 | `DEFAULTS["port"]`; OpenClaw gateway is on 18789 |
| Webhook endpoint | `http://localhost:18789/hooks/agent` | `DEFAULTS["hooks_url"]`; requires Bearer token |
| `prd-creator` agent ID | `"prd-creator"` | `WEBHOOK_AGENT_ID` in `ui/server.py` — used in all idea-to-PRD webhook calls |

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
./install.sh          # Linux only (fcntl dependency)
./install.sh --force  # Override OS check (UI-only development on macOS)
```

The script writes `.env` with `AUTODEV_ROOT` and `AUTODEV_REPO_PATH`. Source it before running any pipeline code:

```bash
source .env
uvicorn ui.server:app --host 0.0.0.0 --port 18790
```

Agent identity docs (`IDENTITY.md`, `SOUL.md`, etc.) are deployed by `install.sh` step 5 from `autodev/agents/{agent}/` into `~/.openclaw/workspace-{agent}/` using `cp -u` (no overwrite if dest is newer). The source of truth for these files is now this repo, not `~/.openclaw/workspace-*`.

---

## Queue System

`~/.openclaw/pipeline_queue.json` holds the project queue. All writes are atomic (`mkstemp + os.replace`). The queue is managed by `ui/server.py` endpoints (`/api/queue/*`) and by `orchestrator.py` methods (`_read_queue`, `_write_queue`, `_select_next_queue_project`, `_queue_update_active_entry`).

### Orchestrator lightweight preflight (`_queue_preflight`)

The orchestrator's `_queue_preflight()` checks: directory exists, `.git` present, `roadmap*.md` present. This is intentionally lighter than the server's `_run_preflight_checks()`, which also validates symlink, `.gitignore`, agent workspace files, etc. **Known MVP limitation:** a project that passes `_queue_preflight` may still fail mid-pipeline if the full server-side preconditions are not met. The server's `_run_preflight_checks` is used at queue-add time and at `trigger-next` time; the orchestrator's check runs only on auto-advance between queue entries.

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

### 3. `autodev_repo_path` DEFAULTS fallback

`DEFAULTS["autodev_repo_path"]` falls back to `os.path.expanduser("~/.openclaw")` when `AUTODEV_REPO_PATH` is not set. After migration, the correct default is the repo root, not `~/.openclaw`. This will silently misbehave on a fresh install where `.env` has not been sourced. `install.sh` mitigates this by writing `.env`, but the in-code default should be fixed.

---

## Key Reference Documents

All in `autodev/docs/`:

| Document | What it is | When to read it |
|----------|-----------|-----------------|
| `PIPELINE-SPEC.md` | Architecture spec — state machine, gate interfaces, component behaviors, infrastructure topology | Before modifying orchestrator.py or any gate script |
| `PIPELINE-CONSTRAINTS.md` | Known issues, hardware limits, model-specific bugs, mitigations | Before changing model config, sentinel timing, or gate logic |
| `AUTODEV-UI-PRD.md` | Full product requirements for the dashboard | Before adding or modifying any API endpoint or UI behavior |
| `ASSUMPTIONS.md` | Resolved spec ambiguities, divergences from original design | When PIPELINE-SPEC and live code appear to contradict each other |
| `Dev_Roadmap_template-v3-...md` | Canonical roadmap format for target projects | When creating or validating a user project's roadmap |
