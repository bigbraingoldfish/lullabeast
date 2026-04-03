# AutoDev Setup Guide

AutoDev is an autonomous development pipeline that runs on top of OpenClaw. It manages a planner → executor → reviewer loop against your project repository, with a web dashboard for monitoring and a PRD ideation system.

---

## Prerequisites

**Python 3.9 or later.** The pipeline code uses `fcntl` for advisory file locking, which is part of the Python standard library on Linux only. Running AutoDev on macOS or Windows is not supported — the orchestrator will fail to acquire the pipeline lock. If you need to run the UI server alone for development, `install.sh --force` will skip the OS check.

**Linux.** This is a hard dependency, not a soft one. `fcntl.flock` is how the orchestrator and heartbeat watchdog coordinate without racing. There is no cross-platform fallback.

**git.** The executor agent commits completed phases to the project repository. git must be on the PATH.

**OpenClaw installed and running.** AutoDev does not bundle OpenClaw — it calls OpenClaw's webhook API to invoke agents and reads the session files OpenClaw writes. Install OpenClaw separately and confirm its gateway is running on `localhost:18789` before running install.sh.

---

## What Testers Bring

You bring two things: a project directory (a git repository containing a `roadmap.md` in the format AutoDev expects) and a running OpenClaw instance configured with the four pipeline agents (planner, executor, reviewer, escalation) and the prd-creator agent.

AutoDev does not create the project repository or the OpenClaw agents — it orchestrates them. The `install.sh` script deploys the behavioral identity files (IDENTITY.md, SOUL.md, TOOLS.md, AGENTS.md, USER.md) into each agent's workspace directory so OpenClaw loads them at session start. You are responsible for having OpenClaw configured with the correct models for each agent.

---

## Installation

```bash
git clone <this-repo> autodev-ui
cd autodev-ui
./install.sh
```

`install.sh` works through thirteen steps in order. You will see colored output for each step. Steps 1–4 exit on failure; steps 5–9 emit warnings and continue, collecting all issues for the final summary.

What the script does (summary):

1. Checks Python 3.9+, pip, git, and Linux.
2. Detects `AUTODEV_ROOT` (OpenClaw home, usually `~/.openclaw`) and `AUTODEV_REPO_PATH` (this repo).
3. **Requires** an existing `openclaw.json` under `AUTODEV_ROOT` (OpenClaw must be installed first; AutoDev does not create this file).
4. Creates **`$AUTODEV_REPO_PATH/.autodev/`** (repo-local pipeline runtime: state, lock, queue, ideas, `pipeline-project` symlink target). See [docs/RUNTIME-MIGRATION.md](docs/RUNTIME-MIGRATION.md).
5. Runs `pip install -r ui/requirements.txt` (interactive confirm unless non-interactive).
6. **Creates** missing `workspace-{agent}/` directories under OpenClaw and copies agent identity files with `cp -u`.
7. **Refreshes** stale `gate_scripts` paths inside `exec-approvals.json` when possible (atomic rewrite).
8. OpenClaw version check (warning-only if below recommended).
9. Updates `cron/jobs.json` heartbeat script path when applicable.
10. Warns if `tools.profile` is not `coding` or `full` (optional prompt to set `coding`), then ensures **planner, executor, reviewer, escalation, prd-creator, and roadmap-converter** exist in `agents.list` and `hooks.allowedAgentIds` (creates `agents.list` if the file uses `agents.defaults` only).
11. Confirms bundled PRD→roadmap instructions at `autodev/prompts/prd-to-roadmap-conversion.txt`.
12. **Merges** `.env` non-destructively (`AUTODEV_ROOT`, `AUTODEV_REPO_PATH`, `AUTODEV_RUNTIME_ROOT`).
13. Writes setup-complete marker and prints summary.

If install.sh exits cleanly with no warnings, the system is ready. If it exits with warnings, read each warning — most require a one-line manual fix.

---

## The Three Silent Failure Modes

These failures produce no obvious error at startup. Each one causes a specific symptom that is easy to misread.

### 1. Orchestrator webhook server not running

**What it looks like.** The pipeline UI shows status as `RUNNING` but no agents are ever invoked. Phases never advance. No error appears in the UI.

**What's happening.** The orchestrator sends HTTP POST requests to `http://localhost:18789/hooks/agent` to wake each agent. If the OpenClaw gateway is not running, these requests fail silently (the orchestrator logs the failure but the UI does not surface it).

**How to verify.** From the server:

```bash
curl -s http://localhost:18789/v1/models | head -20
```

A healthy gateway returns a JSON models list. A connection refused means the gateway is down. Start it with `openclaw gateway start` (or your configured start command) and confirm `openclaw.json` has the correct `hooks.token` value.

### 2. `autodev_repo_path` misconfigured

**What it looks like.** Clicking "Launch" in the setup UI returns an error like `orchestrator.py not found at /home/pi/.openclaw/orchestrator.py`. Or the orchestrator launches but immediately fails with `No module named sentinel_poller`.

**What's happening.** The UI server's `_spawn_orchestrator` function constructs the path to `orchestrator.py` using the `autodev_repo_path` value from `ui/config.json`. If this value is absent or still points to the old `~/.openclaw` location, the wrong directory is searched.

**How `install.sh` handles it.** The script writes `.env` with `AUTODEV_REPO_PATH` set to the repo directory. The `DEFAULTS` dict in `ui/server.py` reads this environment variable as its fallback. If you start the server with `dotenv` or export the variable from `.env` before starting uvicorn, the value is correct automatically.

**How to verify.** Check that `$AUTODEV_REPO_PATH/autodev/pipeline/orchestrator.py` exists:

```bash
source .env
ls "$AUTODEV_REPO_PATH/autodev/pipeline/orchestrator.py"
```

If `ui/config.json` exists and contains an `autodev_repo_path` key, that value takes precedence over the environment variable. Make sure it points to the repo root, not to `~/.openclaw`. The repository ships **`ui/config.example.json`** only; copy it to **`ui/config.json`** (gitignored) or let **`install.sh`** create `ui/config.json` on first run. For the OpenClaw webhook Bearer token, prefer **`AUTODEV_HOOKS_TOKEN`** in the environment so the secret is not committed in JSON.

### 3. Missing conversion prompt file

**What it looks like.** The `/api/ideas/{id}/convert` endpoint returns HTTP 500. The rest of the ideas system (creating sessions, sending messages, readiness assessment) works normally.

**What's happening.** When converting a PRD draft to a roadmap, the server reads a prompt template from `~/.openclaw/deployment-package/Updates/PRD to Roadmap*.txt`. If this file does not exist, the endpoint raises an unhandled exception.

**Where to put it.** The file must be in `~/.openclaw/deployment-package/Updates/` and its filename must match `PRD to Roadmap*.txt`. The exact filename does not matter beyond the prefix — the server takes the first match. `install.sh` step 6 checks for this file and warns if it is missing.

---

## Security and network exposure

The FastAPI app exposes **`/api/*` without authentication**. Anyone who can reach the bound TCP port can invoke setup, queue, launch/stop, and file-oriented endpoints — treat this as **operator tooling for a trusted machine**, not a multi-tenant service.

- **Default (recommended):** bind to loopback only so only local users and SSH tunnels can connect:

  ```bash
  source .env
  uvicorn ui.server:app --host 127.0.0.1 --port 18790
  ```

- **LAN access:** `--host 0.0.0.0` makes every route reachable from your network. Use only on a trusted LAN, behind a firewall, or put a reverse proxy with TLS and authentication in front. Do not expose the raw port to the public internet.

---

## Starting AutoDev

```bash
source .env
uvicorn ui.server:app --host 127.0.0.1 --port 18790
```

For access from other machines on a **trusted** LAN only, you may use `--host 0.0.0.0` (see **Security and network exposure** above).

The UI is then available at `http://<host>:18790`.

To verify it is running correctly, check the health endpoint:

```bash
curl http://localhost:18790/api/state
```

A healthy response contains a JSON object with `pipeline_status`, `current_agent`, and `current_phase_raw_id` fields. If the response is an error or the server refuses the connection, check the uvicorn output for import errors — a missing Python dependency or an incorrect `AUTODEV_REPO_PATH` will surface there.

To run as a background service, see `ui/autodev-ui.service` for a systemd unit file.

---

## Known Compatible OpenClaw Version

Tested against OpenClaw [VERSION] — earlier versions may have schema differences in `pipeline_state.json`. See openclaw.json requirements below.

The fields AutoDev reads from `pipeline_state.json` are: `pipeline_status`, `status`, `current_agent`, `current_phase`, `current_phase_raw_id`, `planner_retries`, `executor_retries`, `reviewer_retries`, `last_action_timestamp`, and `project_path`. If your OpenClaw version writes different field names, the UI status endpoint will return partial data.

---

## `openclaw.json` Requirements

AutoDev reads the following keys from `~/.openclaw/openclaw.json`. The **orchestrator and UI** treat this file as read-only. **`install.sh` step 9** may update it in two narrow ways: set `tools.profile` to `coding` if you confirm at the prompt, and add any missing pipeline agent entries plus `hooks.allowedAgentIds` entries for those IDs (atomic rewrite; other keys preserved).

### `agents.list` and pipeline agents

Webhook routing uses `agents.list[]`. Some OpenClaw exports include `agents.defaults` but omit `agents.list`. The installer creates `agents.list` when missing, then appends entries for **planner**, **executor**, **reviewer**, **escalation**, **prd-creator**, and **roadmap-converter** (skipping IDs already present). New coding agents omit per-agent `tools` so `tools.profile` applies; **escalation** gets an explicit read/write-only `tools` block when first added. **`roadmap-converter`** copies `tools` from **prd-creator** when that entry defines them.

### `tools.profile` vs per-agent tools

OpenClaw applies a **global tool profile** (`tools.profile`: `minimal` | `coding` | `messaging` | `full`) as the baseline allowlist, then per-agent `tools` can further restrict or extend depending on version and UI presets. That is why the gateway can show **Coding** for planner/executor/reviewer even when those entries do not list every tool explicitly. For AutoDev’s pipeline, **`coding` or `full`** is appropriate; see [OpenClaw — Tools and Plugins](https://docs.openclaw.ai/tools).

**`hooks.token`** — The bearer token used to authenticate webhook calls to the OpenClaw gateway. The orchestrator reads this on startup and sends it in the `Authorization` header of every `/hooks/agent` request. If the token is wrong or missing, all agent invocations will return 401 and the pipeline will stall.

```json
{
  "hooks": {
    "token": "your-token-here"
  }
}
```

**`pipeline.skills`** — Controls whether skill injection is active. The orchestrator reads these flags before each agent invocation.

```json
{
  "pipeline": {
    "skills": {
      "enabled": true,
      "planner_skills_enabled": true,
      "executor_skills_enabled": true,
      "reviewer_skills_enabled": true
    }
  }
}
```

Setting `enabled` to `false` disables skill injection for all agents. Setting an individual agent flag to `false` disables injection for that agent only. Missing flags default to `true`.

**`agents.defaults.heartbeat.every`** — Set this to `"0m"` to disable the native OpenClaw heartbeat. If left at a non-zero interval (e.g. `"30m"`), OpenClaw's heartbeat will pull agents to the foreground on a schedule, interrupting active pipeline runs and causing model-swap interruptions. AutoDev's own heartbeat watchdog (`heartbeat_cron.py`) provides crash recovery independently.

```json
{
  "agents": {
    "defaults": {
      "heartbeat": {
        "every": "0m"
      }
    }
  }
}
```

---

## Re-approving Gate Scripts

OpenClaw maintains an `exec-approvals.json` file at `~/.openclaw/exec-approvals.json`. This file records which shell scripts and Python files each agent is permitted to execute. Gate scripts (the Python files in `autodev/pipeline/gate_scripts/`) must be pre-approved, or agent sessions will refuse to run them.

**Why paths changed.** Before this migration, gate scripts lived at `~/.openclaw/gate_scripts/`. They now live at `<repo>/autodev/pipeline/gate_scripts/`. If you previously approved gate scripts from the old location, `exec-approvals.json` still contains the old absolute paths. The orchestrator will attempt to execute scripts at the new paths, which OpenClaw will not recognise as approved.

**How to detect this.** `install.sh` step 7 greps `exec-approvals.json` for gate_scripts entries that do not match your `AUTODEV_REPO_PATH`. If stale entries are found, it prints them and warns you to re-approve.

**How to re-approve.** Open the OpenClaw UI, navigate to the exec-approvals section (Settings → Execution Approvals or equivalent in your version), and approve each gate script at its new path:

- `<repo>/autodev/pipeline/gate_scripts/planner_gate.py`
- `<repo>/autodev/pipeline/gate_scripts/executor_gate.py`
- `<repo>/autodev/pipeline/gate_scripts/reviewer_gate.py`
- `<repo>/autodev/pipeline/gate_scripts/phase_resolver.py`
- `<repo>/autodev/pipeline/gate_scripts/phase_init.py`
- `<repo>/autodev/pipeline/gate_scripts/repo_init_check.py`

The simplest way to trigger the approval prompt is to start a pipeline run — OpenClaw will pause and ask for approval the first time each script is encountered.

---

## Running Tests

```bash
pytest autodev/tests/ -q
```

The pipeline tests in `autodev/tests/` test orchestration logic (sentinel polling, skill injection, idle detection, heartbeat) and do not require a running OpenClaw instance or a real project directory — they use in-process mocks and temporary directories.

The `tests/` directory at the repo root contains UI server tests. Run them with:

```bash
pytest tests/ -q
```

Some tests (`test_skill_mode_a_symlink_and_validation.py`, `test_skill_mode_b_symlink_and_validation.py`) require the skill-library directory to be reachable at the path `AUTODEV_REPO_PATH/autodev/skill-library/`. If you run tests without `.env` loaded, set the environment variable explicitly:

```bash
AUTODEV_REPO_PATH=$(pwd) pytest tests/ -q
```
