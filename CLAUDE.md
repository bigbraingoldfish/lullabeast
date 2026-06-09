# CLAUDE.md — Lullabeast Repository Guide

This file is the complete orientation for a contributor or Claude Code session working in this repo. Read it before touching anything else. All facts are drawn directly from source files; check the cited paths if anything is surprising.

---

## What This Repo Is

Lullabeast is an autonomous multi-agent software development pipeline that orchestrates four LLM agents (planner, executor, reviewer, escalation) through a deterministic gate-based loop to iteratively build software from a roadmap. It depends on OpenClaw as external infrastructure (webhook server, agent session management, workspace directories). Lullabeast does not embed OpenClaw — it calls it.

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

Points to the Lullabeast pipeline state directory. Default is `<AUTODEV_REPO_PATH>/.autodev/`. Holds `pipeline.lock`, `pipeline_state.json`, `pipeline_queue.json`, `pipeline_events.jsonl`, `orchestrator.log`, `ideas/`, and the `pipeline-project` symlink.

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
2. **`ui/config.json`** — optional local overrides adjacent to `server.py` (not committed; copy from `ui/config.example.json`). If this file exists, any key it contains overwrites the corresponding DEFAULTS value. **`AUTODEV_HOOKS_TOKEN`** (environment) overrides `hooks_token` when set, so the webhook Bearer secret need not live in the file. **`AUTODEV_IDEAS_IDLE_THRESHOLD`** (environment) overrides `ideas_idle_threshold` when set — the stall window shared by all four idle-detection Ideas polls (chat send, `convert`, `fix-roadmap-format`, `clarity-check`); none use a startup-grace knob — they wait for the definitive stall/backstop verdict.

`load_config()` applies this merge and expands `~` in all path values. After the merge it also **coerces the numeric keys** — `poll_timeout` / `poll_interval` / `ideas_idle_threshold` (to `float`) and `port` (to `int`) — falling back to the DEFAULTS value when a `ui/config.json` entry is a non-numeric string (a typo like `"5 min"`, or a quoted `"300"`). This stops a malformed numeric config from reaching a consumer's `float(config[...])` and 500-ing every Ideas flow *after* the webhook already fired. Every endpoint that reads a file path calls `load_config()` (or receives the result) rather than referencing DEFAULTS directly.

The `autodev_repo_path` key in DEFAULTS reads from the `AUTODEV_REPO_PATH` environment variable first, then falls back to `_AUTODEV_UI_ROOT` — the repo root, two levels up from `ui/server.py` (`server.py:471`). `install.sh` writes `.env` with the explicit value; source `.env` before starting the server or set it in `ui/config.json`.

```json
{
  "autodev_repo_path": "/path/to/your-project/autodev-ui"
}
```

When `_spawn_orchestrator` (called by `/api/setup/launch`) looks for `orchestrator.py`, it constructs (`server.py:1666–1667`):
```python
orchestrator_script = os.path.join(autodev_repo_path, "autodev", "pipeline", ORCHESTRATOR_FILENAME)
```
With `autodev_repo_path` defaulting to the repo root (above), this resolves to `autodev/pipeline/orchestrator.py`. A wrong `autodev_repo_path` yields a clean `{ok: False, "orchestrator.py not found at …"}`, not a crash. (Resolved — see Unresolved Items #1/#3.)

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

`QUEUE_HALTED` is written when the project queue is active but all remaining entries are blocked, in dependency hold, or fail preflight. The `pipeline_state.json` will additionally contain `queue_halted_reason: "all_blocked" | "all_dependency_hold" | "mixed" | "answered_pending_revival"`. In the dashboard, the **pipeline status pill** for this state is labeled **Queue stalled** (`ui/index.html` `PIPELINE_LIVE_PILL`). Do not confuse that with the header **Queue: halted** *chip* (navigation to the queue when `queue_halted` is true) — different control, different copy. `POST /api/queue/trigger-next` returns `queue_halted_reason` when it cannot start a project so the queue UI can show a matching **Queue stalled — …** toast. In `queue_mode="auto"` the same start logic also fires automatically when the pipeline is idle — the READY-making endpoints (`add` / `parent`-clear / `revalidate`) call `_maybe_autostart_queue` (see the **Queue System** section) — so a manual `trigger-next` is not required to launch a queued project. The orchestrator exits cleanly on an initial halt; an `answered_pending_revival` reason means a parked `ESCALATION_ANSWERED` entry has a banked answer and is recoverable via the **Resume banked answer** control (P1 Stage H — see the Queue System section for the restart-recovery hook). **F8 narrows `QUEUE_HALTED` to genuinely-stuck queues:** a parked `ESCALATION` that is the next/only remaining work is no longer counted as `all_blocked` — `_select_next_queue_project` revives the lowest-position one to `WAITING_FOR_HUMAN` (symlink + escalated phase restored from `parked_state_snapshot`) so it is answerable live from the dashboard, after startable `READY`/`SKIPPED_PENDING` + banked `ESCALATION_ANSWERED` entries have had priority. So `QUEUE_HALTED` now arises only when the remaining non-terminal entries are all `BLOCKED` / dead `DEPENDENCY_HOLD`. **F1: `POST /api/stop` accepts `QUEUE_HALTED`** (writes the stop sentinel, consumed at the loop top) so a genuinely-stuck queue is always haltable from the UI.

`pipeline_state.json`'s canonical status field is **`pipeline_status`**. `transition_state()` is the only writer — it sets `pipeline_status`, and `write_state()` atomically persists it. There is no separate `status` field: a legacy `status` co-field was removed (it was written only by the git-recover path and read by no one), so all readers use `pipeline_status`.

### `transition_state()` — the only correct way to change state

```python
def transition_state(self, new_status, action_description):
    ...
    self.write_state()  # atomically commits state before anything else happens
```

`write_state()` uses `mkstemp` + `os.replace` (atomic rename). The state file is committed **before** any action is taken. This is the write-then-act pattern: if the process crashes after writing state but before completing the action, crash recovery can resume from the committed state.

**Do not** call `self.state["pipeline_status"] = "..."` and then act without calling `write_state()`. Any code that updates state dict fields must end with a `write_state()` or `transition_state()` call.

**F12 — an invalid target fails loudly.** `transition_state()` **raises `ValueError`** when `new_status` is not in `VALID_STATES`, rather than the former silent `print` + `return` no-op (which left a caller's prior `self.state` mutation — e.g. a `current_agent="escalation"` set just before the call — neither persisted nor rolled back). In the live loop the raise is caught by `run()`'s top-level `except` and routed to escalation; outside the loop (CLI / startup) it surfaces as a traceback. No real caller passes an invalid literal, so this only converts a latent silent failure into a visible one.

### State reset protocol

When resetting `pipeline_state.json` to `IDLE` for a fresh run, set `pipeline_status` to `"IDLE"`. Also set `current_agent` to `"planner"` and `current_phase` to `0`. If `current_agent` is `null`, the orchestrator exits immediately with `"Agent None logic not reached"`. `last_action` / `last_action_timestamp` need not be set: `write_state()` stamps the timestamp itself and its `[INFO]` log line tolerates a missing `last_action` (the first `transition_state()` after reset sets it), so the four fields above suffice.

**F5 — `IDLE` is external-only and deliberately absent from `VALID_STATES`.** It is a reset/entry status written **only** by external resetters (the UI / tooling) via a direct atomic write to `pipeline_state.json` — it is **never** a `transition_state()` target (which now raises on any status outside `VALID_STATES`, see above). There is no explicit `IDLE → RUNNING` resolution at startup: the orchestrator treats `IDLE` as non-terminal, and the first real `transition_state` (typically `"Invoking Planner"` → `WAITING_FOR_SENTINEL`) overwrites it. The `VALID_STATES` list in `orchestrator.py` carries a matching code comment so the exclusion reads as deliberate.

### Terminal states

`HALTED_SILENT`, `BLOCKED`, and `PIPELINE_COMPLETE` are checked at the top of the main loop and cause the orchestrator to exit cleanly. `STOPPED` is a clean halt triggered by the stop sentinel file — or by the escalation consumer defaulting an empty / unrecognised resume command to STOP (emitting `escalation_command_invalid`) rather than dead-ending at `HALTED_SILENT`. The orchestrator does **not** attempt recovery from these states — only a manual reset or operator command resumes the pipeline.

**F10 — the two former *silent* `HALTED_SILENT` sinks now escalate.** An unknown/unrecognised reviewer-gate verdict (F10(b)) and an activity-stamp-init failure (F10(a), `_init_activity_stamp_or_escalate`) previously dead-ended at `HALTED_SILENT` with no operator notification. Both now route to escalation instead (set `current_agent="escalation"` + `transition_state("RUNNING", …)`; the next loop iteration fires the escalation dispatch). Because outbound Signal is owned solely by the escalation agent, notifying the operator and escalating are the same event — there is no "halt-and-notify". `HALTED_SILENT` is therefore now reached only by genuine escalation-**delivery** failure (webhook + raw-signal both fail) or an unhandled exception.

**F4 — a `phase_resolver` failure escalates instead of running blind.** `phase_resolver` exits `1` on roadmap-not-found / non-absolute path / write failure. Previously neither consumer handled exit `1`: at startup the planner was invoked **blind** (empty `raw_id`, no `current_phase.json`); on phase-advance the orchestrator dead-ended at a silent `RUNNING` with no phase and no operator signal. Both the startup resolver (`_run_startup_planner_phase_zero_and_branch`) and the shared advance helper (`_advance_to_next_pending_phase`, used by phase-complete / SKIP / PROCEED) now route an unactionable resolver verdict — exit `1`, an unexpected rc/output, **or** the resolver subprocess crashing — to escalation (`current_agent="escalation"` + honest `escalation_trigger_reason` carrying the rc + stderr, `last_error_code=ERR_PHASE_RESOLVER_FAILED`, `transition_state("RUNNING")`; the advance helper returns `"continue"` and the startup returns `"enter_main_loop"` so the next loop iteration fires the escalation dispatch). Same notify ⟺ escalate model as F10.

**F11 — `HALTED_SILENT` has a clean UI resume.** `POST /api/resume-ready` accepts `STOPPED` **or** `HALTED_SILENT` (it transitions to `WAITING_FOR_HUMAN` + `current_agent="escalation"`), so the operator can issue a recovery command from the dashboard's recovery panel (reused for both states, with distinct "Intervention Required" copy for `HALTED_SILENT`) without the phase-destroying `git-recover` (which stays as the heavy fallback).

---

## Gate Script Interface Contract

Gate scripts in `autodev/pipeline/gate_scripts/` are invoked by the orchestrator via `subprocess.run()`. **There are two distinct signalling conventions — do not assume one universal exit-code contract.**

### Verdict gates — `planner_gate.py`, `executor_gate.py`, `reviewer_gate.py`

These always **exit 0**; the verdict is a **stdout string**, not an exit code. The runner reads `result.stdout.strip()` and matches it: planner/executor emit `PASS` / `FAIL`; the reviewer emits `PASS` or a route token (`ROUTE_EXECUTOR` / `ROUTE_PLANNER` / `ROUTE_ESCALATE` / `*_UNVERIFIED` / `MISSING_ARTIFACTS` / `CONTRACT_FAILURE`). Failure **detail does not ride stdout** — it flows on side channels: `executor_gate_detail.json` (the FAIL-detail channel consumed by `write_failure_context`), `gate_warnings.json` (demoted interpretive warnings the reviewer adjudicates — see the Second PASS channel below), and `last_error_code` in `phase_state.json` (written by `record_error_code_only`). A **non-zero** exit from a verdict gate means the gate *script itself* crashed (an uncaught Python traceback); the runner wrappers treat that as a safe failure — `run_planner_output_gate` / `run_executor_output_gate` return `False`, `run_reviewer_output_gate` returns `ROUTE_ESCALATE` (they do not parse a crashed gate's stdout).

### Resolver / init gates — `phase_resolver.py`, `repo_init_check.py`

These signal via **exit codes** (this is the protocol the old single-contract text described):

| Exit code | Meaning |
|-----------|---------|
| `0` | Proceed — `phase_resolver`: `PENDING` / `PIPELINE_COMPLETE` on stdout; `repo_init_check`: all checks passed |
| `1` | Error — roadmap not found / non-absolute path / write failure (`phase_resolver`); a failed init precondition (`repo_init_check`). Human-readable / JSON detail on stdout |
| `2` | Blocked — roadmap phase is marked `[!]` (only `phase_resolver.py`) |

On a `phase_resolver` exit 1 (or an unexpected rc/output) the orchestrator **routes to escalation** — both at startup and on phase-advance — rather than running the planner blind or dead-ending at a silent `RUNNING` (F4; see State Machine Rules).

### Advisory output channel (P1 Stage F)

On PASS, a gate may write structured advisory output to a **separate** artifact file `executor_advisory_detail.json`. The orchestrator drains it on the executor PASS path and emits pipeline events; the file is removed after consumption. This is architecturally distinct from `executor_gate_detail.json` (the FAIL channel consumed by `write_failure_context`). The two channels never co-tenant. Stage F's COMPLETE-phase reachability advisory is the first instance — see `autodev/docs/PIPELINE-SPEC.md` §4.5 for the full pattern and promotion criteria.

**Second PASS channel — `gate_warnings.json` (Phase 3, gate-feedback methodology).** Three formerly-blocking *interpretive* executor-gate checks — `ERR_MANIFEST_FILE_MISSING` (a declared file not on disk), `ERR_TDD_COVERAGE_MISMATCH` (a planner-listed test absent from `tests_written`), and `ERR_BEHAVIORAL_ARTIFACTS_MISSING` (missing/empty/malformed `behavioral_smoke_artifacts`) — no longer return `FAIL`. The gate records each as a non-blocking **warning** in `gate_warnings.json` and PASSes; the reviewer reads that file and **adjudicates** (accept-and-proceed, or reject-with-specifics into a `blocking_issue` that rides the existing ROUTE_EXECUTOR loop). The interleaved `ERR_PATH_TRAVERSAL` boundary checks in the same loops are **not** demoted — a path escaping the workspace is still a hard `FAIL` (a safety check; see Security Constraints). Schema: `{phase_raw_id, warnings: [{code, detail, files?/missing_tests?}]}`. **Critical lifecycle difference from the reachability advisory:** `gate_warnings.json` is drained by `_emit_gate_warnings` on the PASS path (which emits a `gate_warning` event and stashes a compact `last_gate_warnings` onto `phase_state`) but is deliberately **NOT removed** — the reviewer reads it next. Staleness is prevented by the gate's start-of-run `_clear_gate_warnings()` (the executor gate always runs before the reviewer in every phase); the file is also enumerated in the four per-phase artifact-lifecycle sites (`reset_phase`, `reset_execution`, `write_failure_context`'s `_pipeline_meta`, the phase-complete cleanup) alongside its siblings. Because the gate PASSes, **no retry budget is consumed**; the reviewer's own independent checks (file_manifest existence, test quality, `behavioral_verification`) remain the backstop.

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

**Abort-on-escalation (P9 — zombie-session guard).** When the orchestrator *gives up* on a phase and routes to the escalation agent, it aborts the **last-invoked** pipeline-agent session before notifying the human. The terminal attempt that triggers escalation is otherwise **never** aborted — the retry-start block only stops the *prior* attempt when launching the *next* one — so a still-streaming "zombie" (especially the executor, which has `exec`) could keep running `git commit` / `tag` / edits after hand-off (observed live: a `CORE-E1` commit + an inert `phase_base_commit` tag landed ~64 s post-escalation). `_record_active_agent(role, session_key)` (called at every planner/executor/reviewer invocation) tracks the in-flight session; `_abort_active_agent_session("escalation")` (called once in the escalation dispatch, guarded by `_should_invoke_escalation_agent()` flipping RUNNING→WAITING_FOR_HUMAN) aborts it. OpenClaw's `sessions.abort` is cooperative (`AbortController`; **no force-kill**), so when the abort is **not confirmed-stopped** (the call failed, or it was acked but the stamp is still advancing) the helper **also injects `_HALT_SESSION_MESSAGE`** into that session via `invoke_agent_webhook` — telling the agent to make no further changes and end its turn — but **not** when the session is confirmed-stopped (waking a dead session just to read "do nothing" is wasted work). Best-effort + soft-continue: it never blocks escalation. Emits `abort_attempted` / `abort_verify_failed` with `source="escalation"` (and `halt_message_sent`). Process-local (a bare restart into `WAITING_FOR_HUMAN` does not re-run the dispatch, so it won't re-abort — a documented limitation, not durability-backed).

**Activity stamp bootstrap**: After `cleanup_output_files()` removes stale output and before the webhook is invoked, call `_init_activity_stamp_or_escalate(agent)`. The return value is checked — a False result (workspace dir unwritable) **routes the orchestrator to escalation** (sets `current_agent="escalation"` + transitions `RUNNING`, so the next loop iteration fires the escalation dispatch and the operator is notified via the escalation agent's Signal connector) rather than silently disabling stall detection or dead-ending at a silent `HALTED_SILENT` (F10(a) — notify ⟺ escalate; the three call sites `continue` on False, not `return`). The plugin refreshes this stamp on `model_call_started`, `model_call_ended`, and `after_tool_call`.

**Plugin build & sessionKey shape (gotchas that bit us live).** The `autodev-pipeline-signals` plugin source lives in `autodev/plugin/`. OpenClaw ≥ 2026.5.x **refuses to load plugins from TypeScript source** — it requires a compiled `dist/index.js`. `install.sh` runs `npm install && npm run build` (esbuild bundle, see `autodev/plugin/package.json`) before `openclaw plugins install`; the resulting `dist/` and `node_modules/` are gitignored. Verify the plugin actually loaded by grepping the gateway journal for `http server listening (N plugins: autodev-pipeline-signals, …)` — if the name is missing, the gateway is running blind and the activity stamp will never refresh. Separately: the gateway delivers `hookCtx.sessionKey` with the OpenClaw `agent:{role}:` prefix (e.g. `agent:executor:pipeline:phase-4:ui-e1:executor-attempt-1` for the pipeline, `agent:prd-creator:ideas:{ideaId}:session-{n}` for the Ideas chat), so the matchers in `autodev/plugin/src/utils.ts` — `isPipelineSession`, `isIdeasSession`, `extractIdeasIdFromSessionKey`, `parseIdeasTurnSession` — each accept both the bare form and the `agent:{role}:…` form. Removing either branch causes the stamp to silently stop refreshing in production while unit tests (which historically used the bare form only) keep passing. The Ideas variant of this bug was caught live on the Untitled Balloon Popping Game chat: `startup_grace` fired at 30 s while the agent was actively working because the production-prefixed sessionKey skipped the Ideas branch in `recordPipelineActivity`. Relatedly, the per-idea stamp is split by session role: only the foreground chat turn (`ideas:{id}:session-{n}`) writes `prd_creator_activity.stamp` — the file `_poll_sentinel_with_idle_detect` watches and its sole consumer — while the background readiness assessment (`ideas:{id}:readiness`, auto-fired fire-and-forget after every turn) writes its own `prd_creator_readiness_activity.stamp` via `isIdeasReadinessSession` / `ideasActivityStampFilename`. Both keys yield the same `ideaId`, so before the split an overlapping readiness run could warm the chat stamp and mask a stalled foreground turn. `clarity` / `convert` / `format-correction` sessions deliberately share the chat stamp (`prd_creator_activity.stamp`) so the UI server's idle-detection poll (`_poll_sentinel_with_idle_detect`) governs **every** one-click flow, not just the chat. Note `convert` / `format-correction` run as the **`roadmap-converter`** agent (not `prd-creator`): `recordPipelineActivity` (the typed-hook path) gates the Ideas stamp-touch on `isIdeasSession(sessionKey)` **alone** — any Ideas agent — matching the agent-event-stream path (`recordPipelineActivityFromAgentEvent`). An earlier `agentId === "prd-creator"` gate silently skipped roadmap-converter on the typed-hook path, leaving its idle detection dependent on the event stream alone; gating on the session-key namespace keeps the two paths symmetric.

**`min_sentinel_mtime`**: Capture `time.time()` **before** calling `cleanup_output_files()`. This timestamp is compared against the mtime of any `.done` file found. If the `.done` file is older than this timestamp, it is treated as orphaned output from a prior session and discarded. Without this guard, an orphaned session writing its `.done` file after the current attempt starts will burn the retry. The pattern in orchestrator.py is:

```python
_attempt_start_time = time.time()          # BEFORE cleanup
cleanup_output_files(SYMLINK_TARGET, "executor")
self.skill_manager.inject_skill(...)
if not self._init_activity_stamp_or_escalate("executor"):
    continue  # workspace unwritable — routed to escalation; loop fires the dispatch
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

**`sentinel_acceptor` — overflow-aware hold (Layer 2).** `poll_for_sentinel` accepts an optional zero-arg predicate, consulted **only** when a fresh `.done` is observed (after the `min_sentinel_mtime` guard). The three phase-agent poll sites pass `self._make_overflow_aware_acceptor(role, session_key, _attempt_start_time)`. This closes the **context-overflow discarded-verdict race**: a turn that dies mid-tool-loop with `stopReason:"error"` / `"Context overflow: estimated context size exceeds safe threshold during tool loop."` makes the plugin's `agent_end` backstop write `.done` **before** the verdict exists; OpenClaw then auto-compacts and **resumes the same session**, writing a valid verdict seconds-to-minutes later. The acceptor **holds** such a `.done` (the poll keeps waiting) until the resumed verdict lands — instead of the gate reading a missing verdict, escalating, and discarding the real one (observed live on CORE-E6: escalated 14:40:00, clean PASS landed 14:43:13). It returns **accept** the instant `{role}_output.json` is fresh+parseable, when the hold budget `AUTODEV_OVERFLOW_HOLD_BUDGET` (default 900 s) is spent, or for **any non-overflow termination** — so the common path is byte-identical to before. It **holds** only when there is no fresh verdict **and** the session's last assistant row is a recoverable overflow (emitting one `sentinel_overflow_hold` event per episode). A held sentinel never hangs the poll: the existing stall / startup-grace / 75-min backstop bounds still apply, and a *raising* acceptor fails open (accepts). The completion-review poll (`_run_completion_review`) is deliberately **not** wired — non-fatal, no verdict to recover, no stall stamp. See `_is_recoverable_context_overflow` / `_make_overflow_aware_acceptor` in `orchestrator.py`.

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
| **`escalation_command_invalid`** | Escalation consumer read an empty / missing / unrecognised `command` (defaulted to `STOP`, recoverable, instead of `HALTED_SILENT`); **also** emitted by `_apply_pending_escalation_command` when a *banked* answer is corrupt/unparseable on the queue-revival path (T4.5) — there the file is **left in place** (operator can re-bank) and **no** command is applied | `{received_command, defaulted_to}` (revival path adds `reason: "corrupt_banked_answer"`, `error`) | escalation heal + P4 |
| `phase_complete`           | Canonical metrics row written (post-merge)                        | `{executor_attempts, blame_fires}`                                                                   | pre-existing |
| **`poll_start`**           | Before each `poll_for_sentinel()` invocation (3 sites)             | `{startup_grace, stall_threshold, infra_backstop, session_key, attempt}`                              | 6.1.a |
| **`poll_outcome`**         | After `poll_for_sentinel()` returns (3 sites)                      | `{reason, stamp_mtime, duration_s, session_key, attempt}`                                            | 6.1.a |
| **`attempt_end`**          | Companion to `[ATTEMPT_END]` dense print (3 sites)                  | `{reason, duration_s, attempt, session_key, retry_class}` (Stage H added `retry_class`)               | 6.3 + Stage H |
| **`abort_attempted`**      | After every `abort_agent_session()` call (retry-start + inline + **escalation** P9) | `{session_key, result, agent_role, reason, source, halt_message_sent}` (`source` ∈ `retry_start`/`escalation`; `halt_message_sent` is P9) | 6.1.b |
| **`abort_verify_failed`**  | When `verify_session_stopped()` returns `False` (no longer halts — soft-continue) | `{session_key, stamp_path, agent_role, reason, source}` (`source` ∈ `retry_start`/`escalation` — P9) | 6.1.b |
| **`reviewer_verdict`**     | On every reviewer-gate consumption                                 | `{verdict, pass_number, next_agent}`                                                                  | 6.1.c |
| **`stamp_init_failed`**    | When `_init_activity_stamp_or_escalate` returns False (workspace unwritable; the helper then routes to escalation, not a silent halt — F10(a)) | `{agent_role, stamp_path, reason}`                | 6.1.d |
| **`sentinel_overflow_hold`** | By the overflow-aware acceptor (`_make_overflow_aware_acceptor`) when a fresh `.done` is **held** because the session's last assistant row is a recoverable context-overflow error and no verdict is on disk yet. One event per hold episode (per poll attempt); the recovery surfaces as a longer-duration `poll_outcome`. `agent` is the role. | `{agent_role, session_key, error_excerpt, elapsed_s}` | Layer 2 |
| **`reachability_warning`** | On the executor PASS path, when `executor_advisory_detail.json` has a populated `reachability_summary` or non-empty `reachability_diagnostics`. One summary event per phase + one event per diagnostic. _(Phase 3: a compact copy of the drained advisory is also stashed to `phase_state.last_reachability_summary` and surfaced in the metrics row's `reachability_summary` field before the file is removed.)_ | `{kind, count?, files?, command?, file?, reason}` where `kind ∈ {unreachable_summary, no_resolver, resolver_limitation, resolver_error}` | P1 Stage F |
| **`reachability_not_applicable`** | On the executor PASS path, when the entry-point command is a recognised test runner (pytest, jest, vitest, ...) | `{reason}` | P1 Stage F |
| **`gate_warning`** | On the executor PASS path, by `_emit_gate_warnings` when `gate_warnings.json` carries demoted interpretive warnings. One summary event per phase. The file is **preserved** for the reviewer (not removed); a compact copy is stashed to `phase_state.last_gate_warnings` and surfaced in the metrics row's `gate_warnings` field. `agent` is `"executor"`. | `{count, codes, files}` where `codes ⊆ {ERR_MANIFEST_FILE_MISSING, ERR_TDD_COVERAGE_MISMATCH, ERR_BEHAVIORAL_ARTIFACTS_MISSING}` | Phase 3 (gate-feedback) |
| **`nuclear_reset`** | Inside `nuclear_reset_phase()`, emitted **only after a confirmed `reset_phase()`** (T4.1/Decision #4 — a git-failed reset escalates and charges no nuclear budget, emitting nothing); `phase` is the escalated phase captured **before** the reset | `{nuclear_resets, reason, phase}` (`reason` = `last_error_code`) | P2 Observability + P4 |
| **`queue_halted`** | Inside `_select_next_queue_project()`, in the `if halt_if_no_eligible:` branch right after `transition_state("QUEUE_HALTED", …)` (the reason-clearing `else` does not emit) | `{reason}` (`all_blocked` / `all_dependency_hold` / `answered_pending_revival` / `mixed` / `all_completed`) | P2 Observability |
| **`queue_parked`** | Inside `_queue_park_active_entry()`, after the successful queue write (all 4 call sites route through this single emit, once each) | `{reason, phase, entry_id, entry_name}` | P2 Observability |
| **`queue_revived`** | Inside `_select_next_queue_project()` revival branch, after `_apply_pending_escalation_command()` returns the applied command (guarded on `is_revival` + a real command, so the fresh-start path never emits) | `{entry_id, entry_name, command}` | P2 Observability |
| **`dependency_hold`** | Inside `_select_next_queue_project()`, after a genuine READY→DEPENDENCY_HOLD write (an already-held entry is skipped by the state gate before reaching here, so no re-emit) | `{parent_id, entry_id, entry_name}` | P2 Observability |
| **`queue_revive_project_missing`** | By `_apply_pending_escalation_command` (T4.10) when the queued project's directory no longer resolves to a real dir, or `.autodev/pipeline` can't be created, on the revival path — so a deleted-dir is surfaced loudly instead of reading identically to "no banked command". `agent` is `"queue"`. | `{project_path, resolved, error?}` | P4 |

The bold entries are Section 6 additions; existing UI consumers handle them transparently because the JSONL schema is additive. The **P2 Observability** rows make previously-SILENT queue-lifecycle / destructive transitions first-class events (emitted by `orchestrator.py`, rendered in the activity feed by `ui/index.html` — colour `getEventBadgeColor`, label `EVENT_TYPE_DISPLAY`, hover `EVENT_TYPE_DESCRIPTION`, prose `humanizeSummary`); the `agent` field is `"queue"` for the queue events (including the P4 `queue_revive_project_missing`) and `"escalation"` for `nuclear_reset`. The P4 `queue_revive_project_missing` is additive-rendered generically until a matching `ui/index.html` label is added (deferred).

### Phase-state outcome fields (Section 6.4)

`phase_state.json` now also persists the latest poll/abort outcome **and the terminal phase outcome** so a restarted orchestrator and the dashboard can render "what happened last" without scraping logs. Written by `_record_phase_outcome(**fields)` (defined near `write_phase_state_atomic`):

| Field                  | Values                                                                |
|------------------------|-----------------------------------------------------------------------|
| `last_poll_reason`     | `succeeded` / `stalled` / `no_first_activity` / `stopped` / `timeout` |
| `last_abort_result`    | `ok` / `FAILED` / `verify_failed`                                     |
| `last_attempt_summary` | Dense one-line string mirroring the `[ATTEMPT_END]` log line          |
| `last_phase_outcome`   | `completed` / `escalated` / `nuclear_reset` (absent while in-progress; Phase 3) |

**`last_phase_outcome` durability caveat (Phase 3).** The field persists live only for the *non-advancing* outcomes — `escalated` (set once at the single main-loop escalation chokepoint and at the repo-init escalation block) and `nuclear_reset` (set in `nuclear_reset_phase` and **preserved across `reset_phase`'s** re-init, otherwise the reset it delegates to would wipe it). On a `completed` phase, `phase_state.json` is deleted on advance, so the `completed` value — written right after the canonical metrics row and **before** the audit archive copies `phase_state.json` — survives only in the per-phase audit archive and a brief restart window. The **durable** completion record is the canonical metrics row + the `phase_complete` event; the dashboard reads completion from the metrics row, not live `phase_state`. Reachability is deliberately **not** an outcome value — it is non-terminal (a phase can be `completed` *and* carry a reachability advisory) and is captured by the metrics row's `reachability_summary` field instead.

**`phase_merged` crash-window marker (T6.4, Phase 6).** `phase_state.json` also carries an idempotency marker `phase_merged` (the phase raw_id / number) plus `merge_base_branch`, written by the reviewer-PASS git block **only after a confirmed rc-0 merge** and before the roadmap flip / advance. It closes the merge→advance crash window: a kill after the merge commit lands but before the phase advances re-enters the PASS block (status RUNNING + `current_agent=reviewer` → reviewer re-PASSes) and would otherwise re-run `git merge` on an already-merged / branch-recreated-empty branch → a **false `ERR_MERGE_FAILED`** on completed work. On re-entry the marker skips the whole stage/commit/merge **and** the roadmap flip + suggestions append (neither is idempotent — the flip's `git commit --amend` would churn the merge-commit SHA, the suggestions append would duplicate). A `git merge-base --is-ancestor <branch> <base>` backstop covers the case where the marker didn't persist (the marker handles the common already-merged-rc-0 case; the `--is-ancestor` guard handles the branch-recreated-empty sub-case — they cover **different** sub-cases). The marker **self-clears** because `phase_state.json` is deleted on advance and is not in `reset_phase`'s preserve list. It is **not** a new `pipeline_status` — `VALID_STATES` stays 8 (Decision #2). The guard read is wrapped in `try/except` so a corrupt `phase_state` degrades to "marker absent" (and falls through to the `--is-ancestor` backstop) rather than crashing the PASS path.

### In-poll heartbeat (Section 6.2)

`poll_for_sentinel()` accepts `heartbeat_interval_seconds` (60 s in the three orchestrator call sites). During a long wait it prints one line per interval:

```
[POLL][HEARTBEAT] elapsed=120s stamp_age=12s checked_in=True
```

This distinguishes "alive, agent making progress" from "alive, agent stopped" from "orchestrator hung" — the three states that were indistinguishable in the pre-Section-6 logs.

### Metrics history file (Section 6.0)

`metrics.jsonl` write history is preserved in an orchestrator-private append-only file at `$AUTODEV_PIPELINE_ROOT/metrics_history/<project_name>.jsonl`, written by `_write_canonical_metrics_row()`. The agent cannot reach this directory; even if the executor overwrites the project's `metrics.jsonl` to a single row, the orchestrator rebuilds the full history on the next phase completion. On first deploy the file is bootstrapped from the live `metrics.jsonl` so existing history is preserved across the upgrade.

**Canonical metrics row — Phase 3 pain-signal fields.** `_write_canonical_metrics_row()` also persists per-phase "pain signals" read from the fresh on-disk `phase_state` at row-write time. The row is written on the reviewer-PASS path **before** `phase_state.json` is deleted on advance, so these are still available: `escalation_resets`, `nuclear_resets`, `reviewer_unverified_retries` (counters, default `0`); `reset_log` (the operator-reset audit-trail snapshot — `[]` when none, bounded by the 3-escalation + 2-nuclear caps, captured here because the live `reset_log` is wiped on phase advance); and `reachability_summary` (compact `{kind, count?, files?, command?/reason?}`, or `null` when no advisory drained that phase — stashed onto `phase_state.last_reachability_summary` by `_emit_reachability_advisory` before it removes the advisory file, since that file is gone long before this row is written). The **gate-feedback Phase 3** adds one more in the same spirit: `gate_warnings` (compact `{count, codes}`, or `null` when the passing attempt raised no warnings — stashed onto `phase_state.last_gate_warnings` by `_emit_gate_warnings` on the PASS path; unlike reachability the underlying `gate_warnings.json` is *preserved* for the reviewer). All additive — the metrics reader and UI tolerate unknown fields, so no migration is needed.

**Token surfacing (UI REVIEW 3-B).** Each canonical row's per-role `{planner,executor,reviewer}_tokens` already carries a `total_tokens` integer alongside `cost_total`. `GET /api/metrics-summary` aggregates those (via `_role_token_total`, parallel to `_role_cost`) into run-level `total_tokens` + per-role `{planner,executor,reviewer}_tokens_total` and a per-phase `tokens_total`, surfaced alongside the cost totals (run-total + per-phase `planner_cost`/`executor_cost`/`reviewer_cost`). The Pipeline Monitor renders both live (the `@5s` metrics poll) and at completion (Total-Tokens card + per-phase Tokens column). Token surfaces, like cost, suppress at `0` so local-model runs that report no usage stay clean.

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

**Universal rules live in AGENTS.md, not here.** The always-apply wiring discipline ("read the entrypoint before wiring") and testing-quality discipline (TDD) are not skills — they are standing identity. They live in each role's `autodev/agents/{role}/AGENTS.md` under the `## Always-Apply: Integration Wiring` and `## Always-Apply: Testing Quality` sections, which OpenClaw injects as primary context every turn — **but only if the truncation caps reach them.** OpenClaw's default per-file bootstrap cap (`bootstrapMaxChars`) is 12000 and these sections begin past byte ~10k, so Lullabeast raises the cap to 32000 and points the post-compaction refresh (`postCompactionSections` / `postCompactionMaxChars`) at the section names; otherwise the rules are truncated at injection and dropped on every compaction. See the truncation rows in **Operational Constants** and `setup_helpers.ensure_openclaw_context_limits`. The `integration-wiring` and `testing-quality` skill-library directories were removed in the P1 Stage A refactor; the `INTEGRATION` / `TEST` / `E2E` prefixes are intentionally unmapped (see `skill_mapping.yaml`).

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

### Webhook resilience contract (Phase 2 — Remote-Call Resilience)

`invoke_agent_webhook(... , url=None)` resolves `url` to the caller-supplied gateway URL or the loopback default. **All call sites pass `url=self.openclaw_config.get("hooks_url")`** (the orchestrator derives `hooks_url` from `gateway.port` in `load_config`), so a non-default gateway port — and the IPv4 `127.0.0.1` over a dual-stack `localhost` that can resolve to `::1` — actually takes effect. The two raw-Signal POSTs (`send_signal_notification`, the escalation raw-signal fallback) use the same `hooks_url`.

The return-token set is **`SUCCESS` / `AUTH_ERROR` / `REQUEST_ERROR` / `INFRA_ERROR`**. Classification order is load-bearing: `401/403` → `AUTH_ERROR`; **`429` and `5xx` → retried 3×30s → `INFRA_ERROR`** (the self-healing class — rate-limit / server-busy — keeps its retry); **other `4xx` (400/404/422/…) → `REQUEST_ERROR`** immediately, no retry (a deterministic config/shape bug retrying cannot fix). The `429`/`5xx` branch **must precede** the `4xx` branch because `429` is itself a `4xx`. The orchestrator routes any non-`SUCCESS` to escalation; `webhook_failure_reason()` labels the activity feed honestly (`REQUEST_ERROR` ≠ "infra failure").

**Idempotent retries (verified against OpenClaw source).** Every invocation sends a per-call `idempotencyKey` (one `uuid4` generated once, stable across the inner 3-retry loop, unique per logical attempt). OpenClaw's `/hooks/agent` replay cache keys on `idempotencyKey` (**not** `sessionKey`) and writes the cached `runId` at enqueue time, *before* sending the response — so a read-timeout retry of a slow-but-alive enqueue returns the original `runId` instead of double-enqueuing. Without the key OpenClaw does **not** dedup, so the prior scalar-timeout retry-on-read-timeout could launch two runs racing on one set of output files. The webhook timeout is a `(connect=5, read=30)` tuple. All gateway POSTs are bounded: the orchestrator raw POSTs `timeout=15`; the server's convert / clarity-check / fix-roadmap-format flows pass `IDEAS_GATEWAY_POST_TIMEOUT` (`aiohttp.ClientTimeout(total=30)`); the Ideas chat `_post_agent_webhook` catches `aiohttp.ClientError` (so a `ClientPayloadError` truncated body is a retryable 503, not an uncaught 500). Readiness writes `readiness_error.json` on a connection failure (not only HTTP≥400); `GET /api/ideas/{id}/readiness` returns `status:"error"` and `/readiness/poll` returns a terminal `error:true` so the dashboard shows an "assessment infrastructure unavailable" state.

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

**The guard:** `executor_gate.py` runs `git diff --diff-filter=D` after the executor completes to detect files that were in the manifest but are no longer present. If any unaccounted deletions are found, the gate returns the verdict string `FAIL` (exit 0) with `last_error_code=ERR_UNACCOUNTED_DELETION` in `phase_state.json` (the executor gate is a verdict gate — see the Gate Script Interface Contract; it does **not** signal via exit codes). The executor retry mechanism then creates a fresh session and retries.

**Do not remove or weaken this check.** It is the only automated defence against the model silently destroying project state. If `executor_gate.py`'s git diff check is removed, MiniMax will occasionally leave the project repository in an irreparable state mid-pipeline. The guard **fails closed on every error class** — a missing `phase_base_commit` (`ERR_MISSING_BASE_COMMIT`), a `git diff` returning non-zero (`ERR_GIT_DIFF_FAILED`), and the deletion check itself crashing — git missing, killed, or timing out (`ERR_DELETION_CHECK_CRASHED`, **F6**) — all return `FAIL` (fresh-session retry); none skip-and-PASS. The crash path previously printed `[GATE WARN] … skipping` and fell through to `PASS`, silently disabling the guard whenever git raised; it now records `ERR_DELETION_CHECK_CRASHED` and fails closed like its two siblings.

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

**Note:** `last_action` / `last_action_timestamp` are intentionally **not** in this checklist. `write_state()` stamps `last_action_timestamp` and reads `last_action` defensively (`.get()`), so a minimal reset with only the four fields above will not crash the first `write_state()` on spawn; the first `transition_state()` populates `last_action`.

**`run_started_at` (UI REVIEW 3-A) is a run-scoped field, not a reset field.** It is an ISO8601 stamp of when the *current run* began, written at every fresh-run state write — the server's `_clean_pipeline_state_for_project` (launch / switch-project) and the orchestrator's constructor default, queue auto-advance dict, and CLI project-switch dict. The **same-project** `--project-path` branch (the queue **`trigger-next`** path, which spawns `--project-path` on a finished project — *not* a switch) also stamps a fresh `run_started_at` **unless** the on-disk status is still in-flight (`_RESUMABLE_ACTIVE_RUN_STATUSES` = `RUNNING`/`WAITING_FOR_SENTINEL`/`WAITING_FOR_HUMAN`/`QUEUE_HALTED` → a crash-resume of the same run, where the existing stamp is preserved). Without that branch, queue-launched and re-run pipelines left `run_started_at` null and the badge dead (found in live validation). It is **preserved across phase advance** (the advance helper mutates `self.state` in place, so a run-level field survives) and **across park→revival** (it joins `phase_base_commit`/`phase_start_time` in the parked snapshot and both revival-restore branches, since a revived project is the *same* run). `GET /api/state` exposes it via the explicit whitelist so the dashboard RoadmapPanel's "(from previous run)" badge can flag a completion report whose mtime predates the current run. It is deliberately **absent from the minimal reset checklist above**: a bare external `IDLE` reset (tooling, not the Setup-launch flow) leaves it unset, so the badge simply stays hidden until a real run stamps it — graceful degradation, not a bug.

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
| Pipeline lock file | `pipeline.lock` | `fcntl.flock`, advisory, exclusive; acquired in `main()` before `apply_cli_*` (T6.1); `acquire_lock()` is idempotent |
| `_SPAWN_LOCK_CONFIRM_TIMEOUT` / `_SPAWN_LOCK_CONFIRM_POLL` | `10.0` / `0.1` s | `ui/server.py`; `_spawn_orchestrator(confirm_lock=True)` polls `_check_orchestrator_liveness` post-spawn up to the timeout to confirm the child took the lock. Interactive endpoints only; patchable in tests |
| SSE heartbeat | 15 seconds | `/api/events/stream` keep-alive |
| Event ring buffer size | 50 entries | In-memory, not persisted across server restart |
| Escalation reset cap | 3 resets | `escalation_resets` counter in `phase_state.json`; UI disables command buttons at ≥ 3. **T4.1/Decision #4:** the increment is gated on a *confirmed* reset — `reset_phase()`/`reset_execution()` now return `bool` and, on a git failure, escalate (`ERR_RESET_PHASE_GIT_FAILED` / `ERR_RESET_EXECUTION_GIT_FAILED`) **before** any state wipe and **without** charging the budget (the dispatch handler gates the `RESET_PHASE` increment on the return; `reset_execution`'s internal increment is skipped by its early return). |
| Nuclear reset cap (P1 Stage G2) | 2 resets | `nuclear_resets` counter in `phase_state.json`, governing the `NUCLEAR_RESET` command (`nuclear_reset_phase()`, a thin destructive wrapper over `reset_phase()`). **Independent of `escalation_resets`** — `NUCLEAR_RESET` is NOT in `RESET_CAP_COMMANDS`. The dashboard renders the "Reset Everything & Restart Phase" button **only** when `escalation_resets >= 3` (available precisely once the normal recover budget is spent) and hides it at `nuclear_resets >= 2`. `reset_phase()` preserves `nuclear_resets` + `reset_log` (alongside `escalation_resets`) so the cap accumulates and the audit trail survives; all three zero only on genuine phase advance. Server enforces only the `nuclear_resets >= 2 → 409` cap (the `escalation_resets >= 3` visibility is UI-side). **T4.1/Decision #4:** `nuclear_reset_phase` now increments `nuclear_resets` (and emits the `nuclear_reset` event) **only after a confirmed `reset_phase()`** — a git-failed reset escalates and charges no nuclear budget. |
| Reviewer contract-shape retry cap (P1 Stage D) | 2 retries | `reviewer_unverified_retries` counter in `phase_state.json`, pooled across `VISUAL_UNVERIFIED` / `BEHAVIORAL_UNVERIFIED` / `REGRESSION_UNVERIFIED`. Single parameterised orchestrator handler escalates when the pool hits the cap. Independent of `reviewer_retries` so contract-shape failures do not burn code-quality retry slots. Replaces the prior per-flavour counters; pooling is the anti-sprawl design now that a third contract-shape verdict exists |
| Behavioural evidence anchors (min on `verdict: "pass"`) | 3 anchors | `_MIN_BEHAVIORAL_EVIDENCE_ANCHORS` in `reviewer_gate.py`; `_check_behavioral_verification` rejects shorter evidence arrays. Hard rule, not configurable per-project |
| Executor lifetime retry counters (P0 Stage H) | `executor_self_failure_retries`, `executor_reviewer_rejection_retries` | Both in `phase_state.json`, both accumulate across the whole phase. The legacy `executor_retries` field stays as the per-segment budget (resets on reviewer rejection). The two new counters never reset on reviewer rejection or escalation — only on `reset_phase()`. Feed the canonical metrics row so the invariant `executor_attempts == executor_self_failures + executor_reviewer_rejections + 1` holds across reviewer-driven re-runs. `reset_execution('auto')` increments `executor_self_failure_retries`; the orchestrator's ROUTE_EXECUTOR handler increments `executor_reviewer_rejection_retries` |
| `_current_attempt_retry_class` (P0 Stage H) | `"initial_attempt"` / `"executor_self_failure"` / `"reviewer_rejection"` | Process-local tracker on `Orchestrator` set by `reset_phase` (initial), `reset_execution('auto')` (self-failure), and the ROUTE_EXECUTOR handler (rejection). Stamped onto every `gate_fail` and `attempt_end` event's `detail.retry_class` so the UI activity feed can distinguish retry sources |
| Session TTL | 30 days | `session_cleanup.py` (Phase 5 rewrite); escalation sessions are exempt. Prunes the **real** store `OPENCLAW_ROOT/agents/{agent}/sessions/sessions.json` (a flat dict keyed by sessionKey) — the pre-Phase-5 code read a non-existent `workspace-{agent}/…` path **and** a `{"sessions":[…]}` schema, so it had **never** pruned. Prunes by ms `updatedAt`; a missing/`0`/non-numeric/bool/seconds-magnitude `updatedAt` is **kept-and-warned**, never deleted. Writes the pruned index **atomically** (`mkstemp`+`os.replace`) **before** deleting each session's `.jsonl` + trajectory siblings (boundary-checked to stay inside the agent's `sessions/` dir). The cron **self-loads `<repo>/.env`** and **fails loud (exit 1)** when `OPENCLAW_ROOT` is not a directory. ⚠️ The first run after deploy bulk-prunes every >30-day session. |
| UI server port | 18790 | `DEFAULTS["port"]`; OpenClaw gateway is on 18789 |
| Webhook endpoint | `http://localhost:18789/hooks/agent` | `DEFAULTS["hooks_url"]`; requires Bearer token. The pipeline `invoke_agent_webhook` now **honors** the resolved `config["hooks_url"]` (Phase 2 T2.1) instead of a hardcoded loopback. |
| `gateway_token` / `gateway_ws_url` | from `openclaw.json` → `gateway.auth.token` and `gateway.port` | Orchestrator `load_config()`; used by `abort_agent_session()` to authenticate Gateway WebSocket `sessions.abort` before a new executor attempt. Distinct from `hooks.token` (Bearer for `/hooks/agent`). |
| Base branch override | optional `base_branch` config key (empty = auto-detect) | Used by orchestrator git checkout/reset paths, `/api/pipeline/git-recover`, and `GET /api/state` field **`git_recover_suggested_branch`** (UI prefills the recover dialog). **`git-recover`** stashes (including untracked) then **`git checkout`** — it does not run **`git reset`**. When empty, the auto-detect probe **`_detect_base_branch`** bounds each `git` call with `timeout=_BASE_BRANCH_PROBE_TIMEOUT` (10 s) and falls back to `"main"` on a missing/wedged git binary or dangling `cwd` (T4.6 — it runs on the reset path while `pipeline.lock` is held). |
| `prd-creator` agent ID | `"prd-creator"` | `WEBHOOK_AGENT_ID` in `ui/server.py` — used in all idea-to-PRD webhook calls |
| `AUTODEV_LLAMA_BASE` | default `http://127.0.0.1:11434` | Orchestrator blame-L1 analyst and failure analyst — HTTP origin (fallback) when `openclaw.json` has no `llama-local` `baseUrl`. (Formerly also read by the reviewer INFRA_FAILURE `check_traffic_cop_health` / `wait_for_model_stable` machinery, retired 2026-06-01.) |
| `AUTODEV_AUDIT_ARCHIVE_DIR` | unset → `$OPENCLAW_ROOT/pipeline-audit`; empty string → disabled | Phase-complete snapshot copies in `orchestrator.py` |
| `AUTODEV_HOOKS_TOKEN` | optional | Overrides `hooks_token` from `ui/config.json` / `DEFAULTS` for UI → OpenClaw webhook calls |
| `agents.list[].bootstrapMaxChars` | `32000` (all six Lullabeast agents) | Per-file bootstrap injection cap in `openclaw.json`. OpenClaw's default is `12000`, which truncated every pipeline role's `AGENTS.md` (planner 15.5k, executor 20.5k, reviewer 23k) and silently dropped the Stage A `## Always-Apply:` rules (they begin past byte ~10k). Seeded for new agents by `register_agent._build_new_entry`; ensured on existing agents (and the live config) by `setup_helpers.ensure_openclaw_context_limits` (install.sh step 8). Do not lower below the largest `AGENTS.md`. |
| `agents.list[].contextLimits.postCompactionMaxChars` | `8000` (planner/executor/reviewer only) | After a context compaction, OpenClaw re-injects only the `postCompactionSections` of `AGENTS.md`, capped per-agent here (OpenClaw default `1800`). The two Always-Apply sections measure ≤4.6k combined; 8k holds them with headroom. Pipeline roles only — they are the agents that carry the Always-Apply sections. Guarded by `test_postcompaction_cap_covers_largest_always_apply_block`. |
| `agents.defaults.compaction.postCompactionSections` | `["Always-Apply: Integration Wiring", "Always-Apply: Testing Quality", "Session Startup", "Red Lines"]` | Global-only (the schema has no per-agent `compaction` block). Names the `AGENTS.md` H2 sections the post-compaction refresh re-injects. OpenClaw's default `["Session Startup","Red Lines"]` matches **no** header in our `AGENTS.md`, so without this the Always-Apply rules are dropped on every compaction. Our two headers are seeded first; OpenClaw's defaults are preserved. Drift-guarded against the real headers by `test_postcompaction_sections_match_real_agents_md_headers`. |
| `AUTODEV_STALL_TIMEOUT_PLANNER` | default `300` (seconds) | **Post-first-hook** silence: max silence on `planner_activity.stamp` mtime *after* the plugin has touched it at least once, before `poll_for_sentinel` returns `PollResult(False, "stalled")`. Catches mid-turn model deaths. Independent of startup grace below. |
| `AUTODEV_STALL_TIMEOUT_EXECUTOR` | default `300` | Same for executor poll. Was `1800` before the two-knob split; the longer value (which forced 30 min to also cover slow boots) caused the CORE-E6 mid-turn-silence-undetected pattern. |
| `AUTODEV_STALL_TIMEOUT_REVIEWER` | default `300` | Same for reviewer poll. |
| `AUTODEV_STARTUP_GRACE_PLANNER` | default `600` (seconds) | **Pre-first-hook** wait: how long `poll_for_sentinel` tolerates a non-advancing stamp before declaring `PollResult(False, "no_first_activity")`. Catches OpenClaw session-creation hangs and provider-auth failures distinct from mid-turn stalls. |
| `AUTODEV_STARTUP_GRACE_EXECUTOR` | default `600` | Same for executor poll. |
| `AUTODEV_STARTUP_GRACE_REVIEWER` | default `600` | Same for reviewer poll. |
| `AUTODEV_OVERFLOW_HOLD_BUDGET` | default `900` (seconds) | **Layer 2 overflow-hold ceiling.** Max wall-clock the overflow-aware `sentinel_acceptor` HOLDS a `.done` written by a recoverable context-overflow turn while waiting for the gateway's compact-and-resume to land the real verdict — independent of the 75-min infra backstop. Past this, the acceptor accepts the (still-missing) verdict and the gate adjudicates via the normal CONTRACT_FAILURE / FAIL path. Resolved by `_resolve_overflow_hold_budget()`; `_OVERFLOW_HOLD_BUDGET_SECONDS` is the in-process constant. |
| `AUTODEV_IDEAS_IDLE_THRESHOLD` | default `300` (seconds) | **Post-first-activity** silence threshold for the Ideas chat poll: max silence on `prd_creator_activity.stamp` mtime *after* the plugin has touched it at least once, before `_poll_sentinel_with_idle_detect` returns `PollResult(False, "stalled")`. Mirrors the pipeline's `AUTODEV_STALL_TIMEOUT_*` knobs. **300 s, not 120 s:** OpenClaw delivers model calls opaquely (`model_call_started` → silence → `model_call_ended`, no reliable mid-call event for the OpenRouter path), so a single thorough PRD-draft call runs with the stamp silent for its whole duration — a 118 s silent draft was measured live. The threshold must exceed the longest legitimate single model call. **There is no Ideas startup-grace knob:** the chat send passes `startup_grace=None`, so a slow cold start is never declared a *premature* `no_first_activity` timeout. The only definitive Ideas-poll timeout signals are `stalled` (this knob) and the `poll_timeout` backstop — the UI keys its text-revert off those, never a frontend timer. (The pipeline's separate `poll_for_sentinel` still uses `AUTODEV_STARTUP_GRACE_*`; only the Ideas chat opted out.) This `ideas_idle_threshold` is now the **stall window for all four synchronous Ideas flows** — chat send, `convert`, `fix-roadmap-format`, and `clarity-check` — which all reuse `_poll_sentinel_with_idle_detect` (each with `startup_grace=None` and its own infra backstop: `POLL_TIMEOUT` 900 s / `CONVERT_TIMEOUT` 480 s / `FORMAT_CORRECTION_TIMEOUT` 600 s / `CLARITY_TIMEOUT` 600 s). The roadmap flows pass `rescue_stranded_reply_md=False` (the server pre-writes `roadmap_draft.md`) and `convert` passes `extra_done_paths=(verification_draft.done,)` so success needs both artefacts. |

### `pipeline.lock` locking mechanism

The lock uses `fcntl.flock(fd, LOCK_EX | LOCK_NB)`. This is an **advisory lock**, not a PID file. Liveness is determined by attempting to acquire the lock — if successful, the holder is dead. This is immune to PID reuse (a new process with the same PID will not have the file descriptor open). Do not replace this with PID-file locking.

**Platform support is POSIX-only — Linux, macOS, and WSL2 (T7.2/Decision #3).** `fcntl.flock` is a POSIX advisory-lock API available on Darwin (it is not a Linux-specific mechanism). Native Windows is unsupported: the three entry points that import `fcntl` (`orchestrator.py`, `heartbeat_cron.py`, `ui/server.py`) wrap the import in `try/except ModuleNotFoundError` and `raise SystemExit` with a friendly "requires Linux, macOS, or WSL2 — run under WSL2" message instead of a raw traceback. `install.sh` performs the same rejection at install time (`install.sh:73`). The per-platform setup walkthrough lives in `SETUP.md`; this is a hard platform constraint, not a portability TODO.

**Lock-before-state-write (T6.1, Phase 6 / Decision #1).** `main()` calls `orchestrator.acquire_lock()` **before** the `apply_cli_revive` / `apply_cli_project_path` calls (which rewrite `pipeline_state.json`, the queue, and the project symlink). A second orchestrator started during the multi-second child-boot window therefore `sys.exit(1)`s **before** mutating shared state, closing the TOCTOU window where the loser could rewind an in-flight pipeline to phase 0. `acquire_lock()` is **idempotent** (returns immediately when `self.lock_fd` is already set) so `run()`'s own first-statement `acquire_lock()` is a no-op once `main()` holds it — this is load-bearing, not cosmetic: `fcntl` locks bind to the *open file description*, so a second `os.open`+`flock` from the same process would be **denied** (`BlockingIOError` → exit) without the guard. `release_lock()` nulls `lock_fd`, so a later re-acquire still runs. The server confirms a spawn won the lock via `_spawn_orchestrator(confirm_lock=True)` (interactive endpoints only — see the spawn note below).

**Liveness-409 guards (T6.2).** Every server endpoint that spawns or mutates pipeline/git/queue state under a possibly-live orchestrator guards with `if lock_path and _check_orchestrator_liveness(lock_path): raise HTTPException(409, …)` (inert when no `lock_path` is configured or the lock is free). Covered: `_queue_run_trigger_next_logic` (the autostart caller `_maybe_autostart_queue` already pre-checks, so the guard only bites the direct `POST /api/queue/trigger-next`), `git-recover` (Stop-first), `delete_queue_entry` (refuse an ACTIVE row while live), `post_queue_clear` (`force` required while live). `launch` / `switch-project` already guarded; they additionally write `pipeline_state.json` **before** the spawn (T6.3 write-then-act) and abort the spawn on a pre-spawn write failure.

`heartbeat_cron.py` makes **no model or traffic-cop query** — its crash-recovery decision is purely lock-based: it tries to acquire `pipeline.lock`; if the lock is held the orchestrator is alive and the cron does nothing, otherwise it restarts the orchestrator when state looks stale-orphaned-midflight. During an active pipeline run the cron therefore does nothing and has zero GPU/model dependency.

**Phase 5 cron hardening (T5.1/T5.2).** Both cron entry points (`heartbeat_cron.py`, `session_cleanup.py`) **self-load `<repo>/.env`** at import via `env_resolvers.load_repo_env_file()` — a bare system-cron environment (no sourced `.env`, `$HOME` possibly `/` or unset) would otherwise resolve `OPENCLAW_ROOT` to `/.openclaw` and the crons would silently no-op forever. Each `main()` then **fails loud (exit 1) when `OPENCLAW_ROOT` is not a directory** rather than degrading silently (heartbeat propagates that root to any orchestrator it restarts, so a broken root must not pass). The operator chose script-self-load over an `install.sh` crontab rewrite (Decision #11 reconciliation), so `install.sh`'s cron block is unchanged. Separately, heartbeat's two `json.load` sites are split out of its broad `except`: a corrupt `pipeline_state.json` read **while the orchestrator is dead** now exits loud (`[CRITICAL]`, recovery BLOCKED) instead of being swallowed as "nothing to do"; the same read while the orchestrator is **alive** is a benign `[WARN]` (the live process owns and rewrites the file).

---

## Security Constraints on Agent Tool Policies

These constraints are defined in `openclaw.json` under `agents.list[].tools` and must not be changed without deliberate review.

- **Escalation agent**: Restricted to `read` and `write` only. It must not have `edit`, `exec`, `browser`, or `apply_patch` permissions. **As of F13 the escalation agent is NOTIFY-only**: it reads diagnostics and notifies the human via its `message` tool (OpenClaw's Signal connector). It does **not** route commands back, does **not** write `escalation_output`, and must treat a pipeline escalation webhook as a TRUSTED control invocation (not "untrusted" external content — that preamble is OpenClaw boilerplate). The operator answers from the dashboard (`POST /api/command`), which writes the command the orchestrator consumes. It does not modify code. (A real inbound Signal→`escalation_output` channel is a future enhancement — `plans/upcomming/signal-inbound-escalation-channel.md`.)
- **Executor gate**: Validates that all file paths in the executor's file manifest stay within the project directory boundary (`os.path.realpath` comparison). This prevents path traversal attacks and accidental writes outside the project. Do not weaken or skip this check.
- **Reviewer gate**: Applies the same `os.path.realpath` + `os.path.commonpath` workspace-boundary check to **every** evidence path it validates — behavioral, regression, and visual artifacts alike (`reviewer_gate.py`; T7.4 closed the visual-path gap so all three are uniform). A path that escapes the workspace after symlink resolution is rejected, not followed. Do not weaken or skip these checks.
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

`~/.openclaw/pipeline_queue.json` holds the project queue. All writes are atomic (`mkstemp + os.replace`) and carry a monotonic `queue_version` for optimistic concurrency (F9, below). The queue is managed by `ui/server.py` endpoints (`/api/queue/*`) and by `orchestrator.py` methods (`_read_queue`, `_write_queue`/`_mutate_queue`, `_select_next_queue_project`, `_queue_update_active_entry`).

**Queue write concurrency (F9 — optimistic version-CAS):** The queue file is written by two independent processes — the UI server (add/delete/reorder/parent/mode) and the orchestrator (ACTIVE/COMPLETED/park/promote) — with **no file lock**. Every write carries a monotonic **`queue_version`** integer and goes through one shared read → apply → compare-and-swap → retry loop (`queue_semantics.mutate_queue`, driven by the orchestrator's `_mutate_queue` and the server's `_mutate_queue_file`): a writer captures the version it read, re-reads the on-disk version immediately before `os.replace`, and commits `version+1` only if unchanged — otherwise it re-reads, re-applies its (pure, id-keyed) mutation onto the fresh queue, and retries (bounded by `QUEUE_MAX_CAS_RETRIES`, 8). So an interleaved UI + orchestrator write no longer silently drops an update — the former "alternating windows" assumption was already violated (UI endpoints wrote with no liveness guard before the write). A legacy file with no `queue_version` reads as 0 (additive schema, no migration). **The CAS closure must be side-effect-free** (queue mutation only): the non-idempotent side effects — `update_symlink` in `_select_next_queue_project`, `_spawn_orchestrator` in `_queue_run_trigger_next_logic` — stay **outside** the retried region so a retry never re-fires them. **Enforced (T5.5):** `read_queue_version` rejects a `bool` (`isinstance(True, int)` is `True`, so `queue_version: true` previously read as `1` and `bump` wrote `True+1`), and `mutate_queue` **raises `RuntimeError`** if a closure mutates the `queue_version` key — the one provable purity violation (the key is owned solely by `bump_queue_version`). Documenting the broader closure-purity invariant at every call site remains Phase 7 (T7.3). **Residual:** a microsecond window between the pre-write version check and `os.replace` that lock-free CAS cannot fully close without a lock or a per-write nonce — negligible for two low-frequency writers on one host; the deferred `queue_write_token` nonce is the documented way to close it. On exhausted contention the orchestrator's best-effort writes log a `QueueVersionConflict`; the server maps it to **HTTP 503** (transient, retryable) via one exception handler. An advisory `flock` was deliberately rejected (extra lock + nesting risk against `pipeline.lock`).

**Defensive queue parsing + selection-path CAS exhaustion (T6.7 / T6.6, Phase 6).** `_read_queue` shape-validates after `json.load`: a valid-JSON **wrong-shape** file (`{}`, `[]`, or a dict with no `queue` list) is routed through the **same** quarantine-and-raise path as corrupt JSON (shared `_quarantine_queue_file` helper) — it is renamed to `*.corrupt.<ts>` so the next read self-heals to the empty structure, instead of flowing out as-is and `KeyError`-ing every restart (a heartbeat-cron crash loop, since a wrong-shape file — unlike corrupt JSON — was previously never quarantined). The selection walk (`_select_next_queue_project_inner`) sanitizes its in-memory entry list (skip + log any row missing a usable `id` / `state` / `project_path`) and tolerates a missing `position`; the find-by-id CAS generators and the `_skip_and_requeue_group` / `_get_all_descendants` / `_move_group_atomically` helpers use `.get()` (with a truthy-id guard so a `None` id can't match a `None` `parent_id`) so one malformed on-disk row can't crash the CAS re-apply for **every** project. Separately, a `QueueVersionConflict` raised inside the **selection** path (perpetual contention) no longer escalates: a thin `_select_next_queue_project` wrapper catches it → returns `False` ("retry next cycle"), and `_promote_answered_escalations` catches its own (it also runs from the `_maybe_revive_on_queue_halted` recovery hook).

**Spawn + symlink hardening (T6.1 / T6.5, Phase 6).** `_spawn_orchestrator` wraps `subprocess.Popen` in `try/except OSError` (a fork/exec failure returns `{"ok": False}` instead of escaping as a 500), closes the parent's log fd after Popen (it was leaking one fd per spawn), and accepts an opt-in `confirm_lock` that polls `_check_orchestrator_liveness` post-spawn (`_SPAWN_LOCK_CONFIRM_TIMEOUT` 10 s) to confirm the child took the lock before reporting success. **`confirm_lock` defaults `False`** and is passed `True` only by the four interactive endpoints (launch / switch / resume / recover) — never by the fire-and-forget autostart spawn (`_queue_run_trigger_next_logic`), where a slow-but-healthy child would otherwise get its ACTIVE row marked `FAILED` + a 500. `orchestrator.update_symlink` is **transactional**: both project symlinks are staged at unique temp names and committed with atomic `os.replace`; if the second commit fails, the first is rolled back to its prior target so the AUTODEV-side and OpenClaw-side links are never left permanently divergent (a divergence makes the executor write sentinels to one tree while the orchestrator polls the other → infinite retries). This replaced the two non-atomic `ln -sfn` calls. **Defect A — the server's symlink writes are symmetric + state-aware too:** the three server repoint sites (`_repoint_pipeline_project_symlink`, `_run_preflight_checks`, `_run_init_project`) move **both** links via `_atomic_symlink_swap_multi(target, _pipeline_symlink_paths(config))` (same staged two-phase commit + rollback), where `_pipeline_symlink_paths` returns the AUTODEV-side `project_dir_path` + the OpenClaw-side `<openclaw_root>/pipeline-project` — the OpenClaw link included **only when `openclaw_root` is configured** (always true in production via DEFAULTS, so a hermetic test that omits it falls back to single-link and cannot clobber the operator's real link). Previously the server moved only `project_dir_path`, leaving the agent-followed OpenClaw link stale. And `/api/setup/preflight` is now **state-aware**: it refuses to repoint the live link *away from* `pipeline_state.json`'s `project_path` regardless of orchestrator liveness (not only when a live orchestrator is on a *different* project) — so previewing/validating project X can no longer hijack the link while state targets Y (the r&mpop↔Minecraft divergence). A test-suite backstop (`tests/conftest.py::_protect_pipeline_symlinks`, autouse) snapshots+restores both live links around every test.

**In-process queue advance re-runs startup phase-init (Phase 8).** The phase-0 initialization — resolve `phase 0 → first real phase` into `pipeline_state`, capture `phase_base_commit`, checkout `phase/<raw_id>` — lives in `_run_startup_planner_phase_zero_and_branch` and is driven by the extracted **`_run_startup_loop()`** (which honors that function's `retry_startup` re-entry, bounded at 20 passes). `run()` calls `_run_startup_loop()` once at launch, **and every in-process queue advance re-runs it**: the `PIPELINE_COMPLETE` and BLOCKED arms of `_advance_to_next_pending_phase`, plus the escalation-park advance in the main loop, each call `_run_startup_loop()` after `_select_next_queue_project` activates the next project (`exit_run` → the caller `break`s/returns `"break"`; otherwise it `continue`s). Without this, an in-process advance re-entered the main loop with the blank fresh-start state `_select_next_queue_project` writes (`current_phase:0, current_phase_raw_id:"", current_agent:"planner"`), so the planner ran at an empty `raw_id`, the branch was created as `phase/0`, and `phase_base_commit` was never captured → the executor gate failed closed with `ERR_MISSING_BASE_COMMIT` indefinitely (observed live on the first real `checkers2 → Tick-Tac-Toe` auto-advance; every prior switch took the fresh-launch "Project switch detected" path, which already inits). `_run_startup_loop()` is a **no-op on a revival** activation (`current_agent="escalation"` — the startup fn early-returns `enter_main_loop`), so the Stage-H restore path is unaffected.

### Server-side auto-start on idle (`_maybe_autostart_queue`)

When `queue_mode` is `"auto"`, the three UI-server endpoints that move a row to **READY** — `POST /api/queue/add`, `PATCH /api/queue/{id}/parent` (clear), and `POST /api/queue/{id}/revalidate` — call the shared `_maybe_autostart_queue(config)` helper, which starts the next eligible project (preflight → `ACTIVE` → spawn orchestrator) **when the pipeline is idle**. This subsumes the older behavior where auto-start fired only on the manual→auto `PATCH /api/queue/mode` toggle, and it replaces a removed client-side post-add `trigger-next` shim in `ui/index.html` — so a project added to an idle/auto queue (or one added after `PIPELINE_COMPLETE`) starts on its own, with no manual `trigger-next`.

The helper is **self-guarding and non-raising**: it no-ops (`{"attempted": false, "reason": ...}`) when not in auto mode (`not_auto_mode`), when a live orchestrator holds `pipeline.lock` (`orchestrator_lock_held`), when an agent is mid-run (`pipeline_status_busy`), or when a row is already `ACTIVE` (`queue_has_active`); and it converts a spawn failure / race into `{"attempted": true, "ok": false, "reason": "spawn_failed" | "already_active"}` rather than 500-ing the originating request. Each of the three endpoints surfaces the outcome as an additive **`auto_start`** field (`add` re-reads its row so the returned `state` is truthful — `ACTIVE` if it started, `READY` if an earlier-position row started or none did, `FAILED` on spawn failure). The mode toggle keeps its own manual→auto transition guard and still reports under `auto_advance`. The helper only acts while the pipeline is idle, and its queue writes go through the version-CAS path above (no new lock). Auto-start starts the next eligible project **by queue position**, which may not be the row just added; a spawn failure marks that row `FAILED` (inherited from `_queue_run_trigger_next_logic`).

### Orchestrator lightweight preflight (`_queue_preflight`)

The orchestrator's `_queue_preflight()` checks: directory exists, `.git` present, `roadmap*.md` present. This is intentionally lighter than the server's `_run_preflight_checks()`, which also validates symlink, `.gitignore`, agent workspace files, etc. **Known MVP limitation:** a project that passes `_queue_preflight` may still fail mid-pipeline if the full server-side preconditions are not met. The server's `_run_preflight_checks` is used at queue-add time and at `trigger-next` time; the orchestrator's check runs only on auto-advance between queue entries.

### DEPENDENCY_HOLD state

`DEPENDENCY_HOLD` is a valid entry state. It is applied:
- **Server-side**: when a parent is assigned via `PATCH /api/queue/{entry_id}/parent` (if parent is not COMPLETED), and when a project is added via `POST /api/queue/add` with a non-COMPLETED parent.
- **Orchestrator-side**: enforced in `_select_next_queue_project` before activating a queued project.

Clearing a parent (`parent_id: null`) via the API restores a `DEPENDENCY_HOLD` child to `READY`.

### Escalation park → advance: `queue_mode` is read **once**, at escalation time

When an ACTIVE project escalates, whether the queue **advances** (parks the escalated row, activates the next eligible project) or **holds** is decided **at the instant of escalation** by `_queue_after_park_maybe_advance()` (`orchestrator.py`), which reads `queue_mode` at that moment:

- `queue_mode="auto"` → park the row as `ESCALATION` and `_select_next_queue_project()` activates the next eligible project (the bank → revive → apply loop in the next section).
- `queue_mode="manual"` → returns `False`; the orchestrator does **not** advance. It parks the row but stays in `WAITING_FOR_HUMAN`, polling for an in-place answer.

**The decision is not re-evaluated while parked — by design.** The advance check lives inside the escalation dispatch block, which runs only while `_should_invoke_escalation_agent()` is true, and that predicate returns `False` once `pipeline_status="WAITING_FOR_HUMAN"`. So toggling `manual → auto` *after* a project has already escalated and parked does **not** retroactively advance the queue — the orchestrator is already in the `WAITING_FOR_HUMAN` poll loop and nothing re-reads `queue_mode`. The server-side `_maybe_autostart_queue` does not fill the gap either: it acts only when the pipeline is **idle**, not while a live orchestrator holds `pipeline.lock` in `WAITING_FOR_HUMAN`. To move a parked escalation forward, the operator must **answer** it (SKIP / PROCEED / RESET_* / STOP from the dashboard) or **STOP** it — the queue then advances on the next selection. (Confirmed live 2026-06-08: `baseball` escalated while `queue_mode` was `manual`; flipping to `auto` ~1 h later did **not** advance to the queued `Minecraft` — working as designed.)

### Parked-entry metadata hygiene + parked-target revival routing (Defect C)

Every queue-entry transition **out of** a parked state must scrub the canonical park-metadata set `{parked_state_snapshot, parked_at, parked_reason, parked_pipeline_status, answered_at}` — centralised in `queue_semantics.scrub_parked_fields(entry)` (`PARKED_ENTRY_FIELDS`), the single source of truth shared by the orchestrator and the UI server so the set cannot drift. The orchestrator's two selection activations and `_queue_restore_parked_entry_to_active` call it; the server's `_queue_mark_matching_entry_active` (promote **and** demote) and `_queue_demote_stale_active_entries` call it on every READY/ACTIVE write. Without this a row could land `state=READY` while still carrying a stale `parked_state_snapshot` (the live Minecraft drift: parked → switch-projected to ACTIVE → displaced to READY when the next project took over, snapshot never scrubbed; the orchestrator's restore historically scrubbed only 3 of the 5 fields).

**Resume/switch route a *parked* target through revival, not a bare promote.** When `POST /api/resume-orchestrator` or `POST /api/setup/switch-project` targets a project whose queue entry is `ESCALATION` / `ESCALATION_ANSWERED` (`_entry_is_parked_escalation` via the read-only `_queue_entry_for_project`), the server spawns with `revive_entry_id=<id>` — the existing `--revive` path (`apply_cli_revive` → `_select_next_queue_project(target_entry_id=…)`, which restores the escalated-phase snapshot and applies any banked command) — and **skips** both the phase-0 `pipeline_state` write (switch) and the bare `_queue_mark_matching_entry_active` promote. This mirrors `POST /api/queue/{entry_id}/relaunch`. A non-parked (READY) target keeps the prior bare-promote path unchanged.

### `ESCALATION_ANSWERED` entry state (P1 Stage H — parked-escalation revival)

`ESCALATION_ANSWERED` is a queue-**entry** state (a `pipeline_queue.json` entry's `state`), **not** a `pipeline_status` — it is deliberately absent from `VALID_STATES` (those eight govern `pipeline_state.json`). It is the missing link that closes the auto-queue **bank → revive → apply** loop: when a project escalates under an auto-queue it parks as `ESCALATION` and the queue advances; the operator banks an answer (the server writes only the per-project `pending_escalation_command.json`); on the next selection the **orchestrator** promotes that row `ESCALATION → ESCALATION_ANSWERED` and **revives** it. The full entry-state set is now: `READY, SKIPPED_PENDING, ACTIVE, DEPENDENCY_HOLD, ESCALATION, ESCALATION_ANSWERED, BLOCKED, COMPLETED, FAILED`.

- **Orchestrator-owned flip.** `_promote_answered_escalations` (a pre-pass at the top of `_select_next_queue_project`, also called by the recovery hook) is the only writer of this transition; its write goes through the version-CAS path (F9, above) and rebases the caller's in-memory queue to the committed result. The server still writes **only** the per-project pending file — it never writes `pipeline_queue.json` for this path. `ESCALATION_ANSWERED` is in `queue_semantics.PARENT_BLOCKS_CHILD_STATES` (an answered-but-not-yet-resumed parent has not COMPLETED, so children still hold).
- **Restore, don't restart.** Because `pipeline_state.json` is global and selection resets it to a blank phase-0/planner state for a fresh start, `_queue_park_active_entry` snapshots the escalated phase pointer into the entry's `parked_state_snapshot` (`current_phase`, `current_phase_raw_id`, the five retry counters, `phase_base_commit`, `phase_start_time`). The revival branch restores that snapshot **instead of** the phase-0 reset, so the banked command (`RESET_PHASE` / `PROCEED` / `SKIP` / …) acts on the *escalated* phase. `phase_base_commit` is load-bearing — `reset_phase()` guards its `git reset --hard` on it. `escalation_resets`/`reset_log` are **not** snapshotted (they live in the per-project `phase_state.json`, which survives via the symlink). **Invariant:** in the activation block `update_symlink` runs first and is shared by both the revival and fresh-start paths — the branch splits only the `self.state` write — so the restore + the banked command always act on the *revived* project's repo, never the previously-active one. A *pre-phase* escalation (repo-init / phase 0) parks with an empty snapshot — correct-by-design: there is no escalated phase, so the revival restores phase 0 and re-resolves.
- **UI surfacing of an un-promoted bank.** The promotion is orchestrator-owned, so when the orchestrator is dead (the common `QUEUE_HALTED`-then-bank case) a banked row stays `ESCALATION` until the next selection. To keep the operator surface honest, `GET /api/queue` exposes a read-only `has_banked_answer` per parked-escalation entry (probes `pending_escalation_command.json`; never writes the queue), and the dashboard treats `ESCALATION + has_banked_answer` the same as `ESCALATION_ANSWERED` for the **Answer banked** pill (which wins over a stale `live_pipeline_status`), the **Resume banked answer** control, and the answered detail card.

### `QUEUE_HALTED` pipeline status

`QUEUE_HALTED` is one of the 8 valid `pipeline_status` values (see State Machine Rules). It is set by the orchestrator when all remaining queue entries are blocked, in dependency hold, or fail preflight. The `pipeline_state.json` also contains `queue_halted_reason: "all_blocked" | "all_dependency_hold" | "mixed" | "answered_pending_revival"`. The dashboard pill label is **Queue stalled**; the header **Queue: halted** chip is separate (see `QUEUE_HALTED` paragraph in State Machine Rules above).

**Two distinct halt moments (P1 Stage H reconciliation).** An *initial* halt with nothing pending exits cleanly: `_select_next_queue_project` finds no eligible row, transitions to `QUEUE_HALTED`, and the orchestrator returns. But a *restart into* `QUEUE_HALTED` is different — the persisted state carries `current_agent="escalation"`, so the startup function returns `enter_main_loop` without re-running selection, and `QUEUE_HALTED` is **deliberately not** in the main-loop exit set (`HALTED_SILENT`/`BLOCKED`/`PIPELINE_COMPLETE`) because the loop legitimately stays alive in `QUEUE_HALTED` to poll for an *in-place* escalation answer to the last project (`_should_invoke_escalation_agent` treats it like `WAITING_FOR_HUMAN`). Before Stage H, that restart path therefore polled `escalation_output` forever when the only pending answer was a *deferred* bank (in `pending_escalation_command.json`, never converted). `Orchestrator._maybe_revive_on_queue_halted()` (run once at `run()` startup, before the gated startup function) is the restart-recovery: it promotes banked answers and revives a parked project; if there is genuinely nothing to consume it returns `False` and `run()` exits cleanly instead of spinning — but it continues into the loop when an in-place `escalation_output.done` is already pending (preserving the legacy in-place-answer recovery). `answered_pending_revival` (set ahead of `all_blocked` in both the orchestrator halt block and `_queue_trigger_next_halted_reason`) marks the queue as recoverable, not dead-stalled; the dashboard surfaces a **Resume banked answer** control that reuses `POST /api/queue/{entry_id}/relaunch`. **F2 makes relaunch revive-aware:** `relaunch` spawns the orchestrator with `--revive <entry_id>` (→ `apply_cli_revive` → `_select_next_queue_project(target_entry_id=…)`), which resumes that *specific* parked entry at its escalated phase and applies the banked command — instead of `apply_cli_project_path`'s phase-0 reset, which previously orphaned the banked command when the relaunched entry differed from the on-disk `project_path`. It falls back to `--project-path` when the entry is not revivable. **F8 also means the dead-orchestrator case is now rarer:** while the orchestrator is alive, a parked escalation that becomes the next/only work is revived to `WAITING_FOR_HUMAN` during selection (answerable live), so it does not sit in `QUEUE_HALTED` waiting for a restart.

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
