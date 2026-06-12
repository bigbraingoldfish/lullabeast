# Lullabeast Setup Guide

Lullabeast is an autonomous development pipeline that runs on top of OpenClaw. It manages a planner → executor → reviewer loop against your project repository, with a web dashboard for monitoring and a PRD ideation system. For dashboard terminology (pipeline and queue states, skills, metrics), see [GLOSSARY.md](GLOSSARY.md).

---

## Prerequisites

**Python 3.9 or later.** The pipeline code uses `fcntl` for advisory file locking. `fcntl` is part of the POSIX standard and is available on Linux and macOS. Native Windows lacks `fcntl` and is not supported; WSL2 runs a real Linux kernel and works without any code changes.

**Linux, macOS, or WSL2.** Lullabeast runs on Linux (including WSL2) and macOS. Native Windows is not supported. `fcntl.flock` is how the orchestrator and heartbeat watchdog coordinate without racing — it is a POSIX advisory lock, not a Linux-only mechanism.

**git.** The executor agent commits completed phases to the project repository. git must be on the PATH.

**Node.js (with npm), 22+ recommended.** `install.sh` builds the `autodev-pipeline-signals` OpenClaw plugin (an esbuild bundle) and provisions the Playwright MCP used for visual review of UI phases. If `npm` is missing the installer continues but warns — and without the signals plugin, agent activity stamps never refresh and stall detection is disabled, so install Node before a real run.

**OpenClaw installed and running.** Lullabeast does not bundle OpenClaw — it calls OpenClaw's webhook API to invoke agents and reads the session files OpenClaw writes. OpenClaw is also the **runtime that owns all model and provider configuration**: you pick each agent's model and supply any provider API keys in `openclaw.json`. Lullabeast is model-agnostic and never reads a provider key directly. Install OpenClaw separately and confirm its gateway is running on `localhost:18789` before running install.sh.

---

## New User Webhook Checklist

Run these checks once after install to avoid common first-run webhook failures.

1. In `~/.openclaw/openclaw.json`, confirm `hooks.token` is set (this is the webhook Bearer secret; it is **not** `gateway.auth.token`).
2. Ensure Lullabeast uses the same secret:
   - `<repo>/.env` → `AUTODEV_HOOKS_TOKEN=...`, and/or
   - `ui/config.json` → `hooks_token`.
3. Start Lullabeast UI with `.env` loaded:

```bash
cd /path/to/autodev-ui
source .env
uvicorn ui.server:app --host 127.0.0.1 --port 18790
```

If using systemd for the UI service, configure an `EnvironmentFile=` entry that points at the repo `.env`.

4. Verify webhook auth with a **POST** (GET checks alone are insufficient):

```bash
curl -sS -o /dev/null -w "HTTP %{http_code}\n" -X POST http://127.0.0.1:18789/hooks/agent \
  -H "Authorization: Bearer <hooks.token>" -H "Content-Type: application/json" \
  -d '{"agentId":"prd-creator","sessionKey":"ideas:install-check:0","wakeMode":"now","message":"ping"}'
```

Expect `HTTP 200`. `HTTP 401` means the Bearer token does not match `hooks.token`.

---

## What Testers Bring

You bring two things: a project directory (a git repository containing a `roadmap.md` AND a `verification.md` in the formats Lullabeast expects) and a running OpenClaw instance configured with the four pipeline agents (planner, executor, reviewer, escalation) and the prd-creator agent.

**Three project-level documents form the contract**:

- **`prd.md`** — *what* the project must do. Source of truth for user intent; the reviewer reads this first.
- **`roadmap.md`** — *how* the work is broken into phases. Every phase must include a `**Behavioral Verification:**` block with three sub-bullets (`User-observable`, `How we'll check`, `If this fails, the user sees`). Preflight refuses to stage a project whose roadmap is missing the block.
- **`verification.md`** — *proof*. Project-level document derived from the PRD that names the project type, entry point, public surface, and acceptance stack. Preflight refuses to stage a project without a valid `verification.md`. The user never edits it directly — if it's wrong, the fix is to edit the PRD and re-run conversion from the Ideas screen.

Lullabeast does not create the project repository or the OpenClaw agents — it orchestrates them. The `install.sh` script deploys the behavioral identity files (IDENTITY.md, SOUL.md, TOOLS.md, AGENTS.md, USER.md) into each agent's workspace directory so OpenClaw loads them at session start. You are responsible for having OpenClaw configured with the correct models for each agent.

---

## Installation

```bash
git clone <this-repo> autodev-ui
cd autodev-ui
./install.sh
```

**Upgrading:** after `git pull`, restart the Lullabeast UI service. The server automatically syncs updated agent workspace guidance into `OPENCLAW_ROOT/workspace-*` on startup (same mtime rules as step 5 of `install.sh`). To manage those files only yourself—for example you maintain custom agent instructions—set `"auto_sync_agent_workspaces": false` in `ui/config.json` and re-run `./install.sh` after each update when you want upstream guidance copied.

`install.sh` works through **14** steps in order. You will see colored output for each step. Early steps exit on failure when prerequisites are missing; later steps often warn and continue, collecting issues for the final summary.

What the script does (summary):

1. OS check (Linux, macOS, and WSL2 supported; native Windows is rejected).
2. Python 3.9+ and pip availability.
3. `pip install -r ui/requirements.txt` (interactive confirm unless `--non-interactive`).
4. OpenClaw detection: resolves `OPENCLAW_ROOT`, **requires** `openclaw.json`, creates **`$AUTODEV_REPO_PATH/.autodev/`**, updates **`ui/config.json`** paths from `config.example.json` when needed.
5. **Creates** missing `workspace-{agent}/` directories under OpenClaw and deploys agent identity files (skipping any destination file that is already newer).
6. **Refreshes** stale `gate_scripts` paths inside `exec-approvals.json` when possible (atomic rewrite).
7. Updates `cron/jobs.json` heartbeat script path when applicable, and migrates **user crontab** lines that still reference legacy `heartbeat_cron.py` / `session_cleanup.py` under `OPENCLAW_ROOT` to the repo copies (only if such lines already exist).
8. **Hooks preflight** — audits `hooks.enabled`, `hooks.token`, `hooks.allowRequestSessionKey`, and `hooks.allowedSessionKeyPrefixes` (`pipeline:`, `ideas:`). Optionally patches them atomically (preserves an existing `hooks.token`); if `hooks.token` is still empty, can generate one and append **`AUTODEV_HOOKS_TOKEN`** to `.env` when that key is not already set. Then warns if `tools.profile` is not `coding` or `full` (optional prompt to set `coding`), and registers **planner, executor, reviewer, escalation, prd-creator, and roadmap-converter** in `agents.list` / `hooks.allowedAgentIds`.
9. Confirms bundled PRD→roadmap instructions at `autodev/prompts/prd-to-roadmap-conversion.txt`.
10. **Merges** `.env` non-destructively. Writes only the canonical names (`OPENCLAW_ROOT`, `AUTODEV_PIPELINE_ROOT`, `AUTODEV_REPO_PATH`) plus any keys added in step 8. Legacy names `AUTODEV_ROOT` and `AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME` are ignored at runtime.
11. Installs the **`autodev-pipeline-signals`** OpenClaw plugin and sets `plugins.entries.autodev-pipeline-signals.hooks.allowConversationAccess=true` when the `openclaw` CLI is available.
12. Installs **Playwright MCP** and Chromium — **required for UI/INT visual review** (executors screenshot UI/INT phases and the reviewer reads them; without it every UI/INT phase is rejected at the reviewer gate with `ERR_VISUAL_UNVERIFIED`). Opt out with `--skip-playwright` (or by declining the prompt) only for runs that will not touch UI/INT phases.
13. Writes the setup-complete marker (`~/.autodev_setup_complete`).
14. Prints summary.

If install.sh exits cleanly with no warnings, the system is ready. If it exits with warnings, read each warning — most require a one-line manual fix.

### Installing on macOS

Lullabeast runs on macOS without any code changes. The pipeline uses `fcntl.flock` for advisory locking, which is a POSIX mechanism available on Darwin.

**Prerequisites on macOS:**

```bash
# Python 3.9+ via Homebrew (recommended)
brew install python@3.11
# or via pyenv
pyenv install 3.11 && pyenv global 3.11

# git (if not already present)
brew install git
```

**Run install.sh as normal:**

```bash
git clone <this-repo> autodev-ui
cd autodev-ui
./install.sh
```

The OS check prints `OS: macOS (Darwin)` and proceeds without warnings.

**Register as a LaunchAgent (background service):**

```bash
# 1. Edit WorkingDirectory and ProgramArguments in ui/com.autodev.ui.plist
#    (set your actual checkout path and python3 path)
nano ui/com.autodev.ui.plist

# 2. Install the plist
cp ui/com.autodev.ui.plist ~/Library/LaunchAgents/

# 3. Load it into the current login session
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.autodev.ui.plist

# 4. Enable it to start automatically at login
launchctl enable gui/$(id -u)/com.autodev.ui

# Tail logs
tail -f /tmp/autodev-ui.log /tmp/autodev-ui.err

# Unload (stop and prevent autostart)
launchctl bootout gui/$(id -u)/com.autodev.ui
```

### Installing on WSL2

WSL2 runs a real Linux kernel — no code changes are needed. The flow is identical to native Linux.

**Keep the repo under your Linux home directory**, not under `/mnt/c/…`. Windows NTFS mounts have case-insensitive semantics, symlink restrictions, and much slower IO for file-watch operations.

```bash
# Good
~/projects/autodev-ui

# Avoid
/mnt/c/Users/You/projects/autodev-ui
```

**Run install.sh as normal:**

```bash
git clone <this-repo> autodev-ui
cd autodev-ui
./install.sh
```

The OS check detects WSL2 via `/proc/version` and prints `OS: Linux (WSL2)`.

**Enabling systemd (optional, for the systemd unit file):**

WSL2 supports systemd when enabled in `/etc/wsl.conf`. If not already enabled:

```
# /etc/wsl.conf — add these lines, then restart the WSL instance
[boot]
systemd=true
```

After restarting, install the unit as on native Linux:

```bash
sudo cp ui/autodev-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now autodev-ui
```

If you prefer not to enable systemd, run `uvicorn` directly in a `tmux`/`screen` session instead.

**Reaching the UI from Windows:** once Lullabeast is running inside WSL2, open `http://localhost:18790` in a Windows browser. WSL2 automatically forwards loopback ports to the Windows host.

### Environment variables (canonical names)

Lullabeast exposes two root paths, each with a single canonical name. Legacy
aliases have been removed; set the canonical names below.

| Concept                          | Env                     | UI JSON                 |
| -------------------------------- | ----------------------- | ----------------------- |
| OpenClaw install root            | `OPENCLAW_ROOT`         | `openclaw_root`         |
| Lullabeast pipeline state directory | `AUTODEV_PIPELINE_ROOT` | `autodev_pipeline_root` |

Resolution order at every read site: env var → UI JSON key → built-in default.
Empty strings are treated as "unset".

Legacy names (`AUTODEV_ROOT`,
`AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME`, and the UI `use_legacy_openclaw_runtime`
key) have been removed and are ignored if set.
To collapse the pipeline directory onto the OpenClaw directory, set
`AUTODEV_PIPELINE_ROOT=$OPENCLAW_ROOT` explicitly. See
[`docs/RUNTIME-MIGRATION.md`](docs/RUNTIME-MIGRATION.md) for history.

**Optional — in-session stall timeouts (orchestrator).** Before each planner/executor/reviewer webhook, the orchestrator seeds `{agent}_activity.stamp` in the pipeline artifacts directory. The `autodev-pipeline-signals` plugin refreshes that stamp on Tier A hooks (`model_call_started`, `model_call_ended`, `after_tool_call`) and OpenClaw's live agent event stream. The orchestrator treats silence longer than these thresholds (seconds) as a failed sentinel poll and uses the normal retry path. Seeding the stamp means a missing first hook is still caught. Defaults are conservative; override only if you measure false positives or need faster recovery:

| Env | Default (seconds) | Role |
| --- | ------------------ | ---- |
| `AUTODEV_STALL_TIMEOUT_PLANNER` | 300 | Planner poll (post-first-activity silence) |
| `AUTODEV_STALL_TIMEOUT_EXECUTOR` | 300 | Executor poll (post-first-activity silence) |
| `AUTODEV_STALL_TIMEOUT_REVIEWER` | 300 | Reviewer poll (post-first-activity silence) |

The companion `AUTODEV_STARTUP_GRACE_{PLANNER,EXECUTOR,REVIEWER}` knobs (pre-first-activity wait, default **600 s**) are documented in `.env.example`; raise those for slow cold OpenClaw boots and the stall timeouts above for mid-turn silence.

`install.sh` appends the same three variables to **`.env` as commented placeholders** (once per file; a marker line prevents duplicates). **`.env.example`** contains the same block for new copies. Uncomment a line and set an integer to override.

Verify the plugin registration with:

```bash
openclaw plugins inspect autodev-pipeline-signals --json
```

The output should show `status: "loaded"`, `hookCount: 5`, and typed hooks for `agent_end`, `before_agent_finalize`, `model_call_started`, `model_call_ended`, and `after_tool_call`. During a live pipeline run, the active `{agent}_activity.stamp` mtime should advance with the matching session JSONL mtime.

**Optional — Ideas chat poll (UI server).** With the same plugin, Project Ideas uses `prd_creator_activity.stamp` for idle detection. Override via environment (wins over `ui/config.json`) or the config key `ideas_idle_threshold`:

| Env | Default (seconds) | Role |
| --- | ------------------ | ---- |
| `AUTODEV_IDEAS_IDLE_THRESHOLD` | 300 | Max silence on stamp mtime **after first activity** before the chat turn is declared a definitive **stalled** timeout. 300 s (not 120) because a thorough PRD-draft model call runs with the stamp silent for its whole duration (118 s measured live) |

The Ideas chat send has **no startup-grace knob**: it waits for a definitive timeout signal — a `stalled` (above) or the `poll_timeout` backstop (below) — rather than fast-failing if the agent is slow to produce its first activity stamp. The dashboard only reverts your typed message back into the composer when one of those definitive timeouts fires (married with the failure notice), never on a premature timer.

The companion `poll_timeout` (full-turn infra backstop, `ui/config.json` `poll_timeout`, default **900 s**) bounds the total turn — a thorough PRD turn chains several model calls and can exceed the old 180 s ceiling.

`install.sh` also appends these two as commented placeholders to `.env` (separate marker; idempotent). **`.env.example`** includes the same block.

---

## Silent failure modes (four cases)

These failures produce no obvious error at startup (or are easy to misread after switching projects). Each one causes a specific symptom.

### 1. Orchestrator webhook server not running

**What it looks like.** The pipeline UI shows status as `RUNNING` but no agents are ever invoked. Phases never advance. No error appears in the UI.

**What's happening.** The orchestrator sends HTTP POST requests to `http://localhost:18789/hooks/agent` to wake each agent. If the OpenClaw gateway is not running, these requests fail silently (the orchestrator logs the failure but the UI does not surface it).

**How to verify.** From the server:

```bash
curl -s http://localhost:18789/v1/models | head -20
```

A healthy gateway returns a JSON models list. A connection refused means the gateway is down. Start it with `openclaw gateway start` (or your configured start command), then run the POST check from **New User Webhook Checklist** to validate webhook auth.

### 2. `autodev_repo_path` misconfigured

**What it looks like.** Clicking "Launch" in the setup UI returns an error like `orchestrator.py not found at /path/to/.openclaw/orchestrator.py`. Or the orchestrator launches but immediately fails with `No module named sentinel_poller`.

**What's happening.** The UI server's `_spawn_orchestrator` function constructs the path to `orchestrator.py` using the `autodev_repo_path` value from `ui/config.json`. If this value is absent or still points to the old `~/.openclaw` location, the wrong directory is searched.

**How `install.sh` handles it.** The script writes `.env` with `AUTODEV_REPO_PATH` set to the repo directory. The `DEFAULTS` dict in `ui/server.py` reads this environment variable as its fallback. If you start the server with `dotenv` or export the variable from `.env` before starting uvicorn, the value is correct automatically.

**How to verify.** Check that `$AUTODEV_REPO_PATH/autodev/pipeline/orchestrator.py` exists:

```bash
source .env
ls "$AUTODEV_REPO_PATH/autodev/pipeline/orchestrator.py"
```

If `ui/config.json` exists and contains an `autodev_repo_path` key, that value takes precedence over the environment variable. Make sure it points to the repo root, not to `~/.openclaw`. The repository ships **`ui/config.example.json`** only; copy it to **`ui/config.json`** (gitignored) or let **`install.sh`** create `ui/config.json` on first run. For the OpenClaw webhook Bearer token, prefer **`AUTODEV_HOOKS_TOKEN`** in the environment so the secret is not committed in JSON.

### 3. Conversion prompt file not found

**What it looks like.** The `/api/ideas/{id}/convert` endpoint returns an error. The rest of the ideas system (creating sessions, sending messages, readiness assessment) works normally.

**What's happening.** Converting a PRD draft to a roadmap needs the conversion-instructions prompt. The server resolves it in this order: the `conversion_prompt_path` key in `ui/config.json` (if set), then the repo-bundled default at `<repo>/autodev/prompts/prd-to-roadmap-conversion.txt`, then a built-in inline fallback. The bundled file ships with the repo, so a fresh checkout converts out of the box — this only breaks if `conversion_prompt_path` is overridden to a path that does not exist, or the bundled file was deleted.

**How to fix.** Leave `conversion_prompt_path` empty in `ui/config.json` to use the bundled prompt, or point it at a readable file. `install.sh` step 9 verifies the bundled prompt is present and warns if it is missing.

### 4. `pipeline-project` symlink out of sync with `pipeline_state.json`

**What it looks like.** After copying `pipeline_state.json` from another machine, restoring an old state file, or pointing tests at the wrong project, the **Pipeline Monitor** and queue can disagree about which git repository is active. Until recently this could look like a silent “wrong project” run; the UI now reconciles on resume when safe.

**What's happening.** Under `AUTODEV_PIPELINE_ROOT` (see environment table above), the **`pipeline-project`** entry is a symlink to the active project repository. `pipeline_state.json` records the canonical **`project_path`**. The orchestrator and agents work through that symlink.

**What the UI does.** `POST /api/resume-orchestrator` compares the symlink’s real path to `project_path` in `pipeline_state.json`. If they differ, it **repoints** the symlink to match state (returns JSON **`reconciled`: true** when it did). **`HTTP 422`** if the symlink location cannot be updated safely (for example a real directory where the link should be). **`HTTP 409`** if the pipeline lock shows an orchestrator already running. **`HTTP 503`** if spawning the orchestrator fails; if repointing had just succeeded, the JSON body can still include **`reconciled`: true** so you can fix the underlying spawn error and retry.

**How to verify manually.** With `.env` loaded, compare `readlink -f` / `realpath` on `$AUTODEV_PIPELINE_ROOT/pipeline-project` to the `project_path` field inside `$AUTODEV_PIPELINE_ROOT/pipeline_state.json` (paths depend on your config — see `pipeline_state_path` / `autodev_pipeline_root` in `ui/config.json` if overridden).

---

## Security and network exposure

The dashboard and **`/api/*`** require a **single shared access token**: `AUTODEV_UI_TOKEN`, generated into `.env` by `install.sh` (step 10). At startup the server prints the access URL — open `http://127.0.0.1:18790/?token=<AUTODEV_UI_TOKEN>` once and the browser is authorized via an HttpOnly cookie (30 days); scripts and `curl` send the same value as `Authorization: Bearer <token>` instead. The `?token=` query is honored only on `/`, never on `/api/*`, so the secret stays out of request logs. `GET /health` and `/static/*` are exempt. This is single-user, local-tool auth — one token, no accounts or roles — so the endpoints still execute code on the host for whoever holds it; treat Lullabeast as **operator tooling for a trusted machine**, not a multi-tenant service.

**Token sources, in precedence order:** the `AUTODEV_UI_TOKEN` environment variable (sourced from `.env`), then `ui_token` in `ui/config.json`. Service deployments note: the bundled systemd unit does not load `.env`, so set `ui_token` in `ui/config.json` (or add an `EnvironmentFile=` line yourself). To rotate the token, change it and restart the server. **With no token configured at all**, the server runs in the legacy open mode: loopback requests are served unauthenticated and non-loopback requests are refused (403) — the startup log warns loudly.

- **Default (recommended):** bind to loopback only so only local users and SSH tunnels can connect:

  ```bash
  source .env
  uvicorn ui.server:app --host 127.0.0.1 --port 18790
  ```

- **LAN access:** `--host 0.0.0.0` makes every route reachable from your network (each request still requires the token). Use only on a trusted LAN, behind a firewall, or put a reverse proxy with TLS and its own authentication in front. Do not expose the raw port to the public internet — the shared token is not a substitute for TLS + real access control.

---

## Starting Lullabeast

```bash
source .env
uvicorn ui.server:app --host 127.0.0.1 --port 18790
```

For access from other machines on a **trusted** LAN only, you may use `--host 0.0.0.0` (see **Security and network exposure** above).

The startup log prints your access URL — open it to authorize the browser:

```
[AUTH] Dashboard access URL: http://127.0.0.1:18790/?token=<AUTODEV_UI_TOKEN>
```

To verify the server is up, check the (unauthenticated) health endpoint, then a real API route with the token:

```bash
curl http://localhost:18790/health
curl -H "Authorization: Bearer $AUTODEV_UI_TOKEN" http://localhost:18790/api/state
```

`/health` returns `{"ok": true}`. A healthy `/api/state` response contains a JSON object with `pipeline_status`, `current_agent`, and `current_phase_raw_id` fields; a **401** means the Bearer value does not match the configured `AUTODEV_UI_TOKEN`. If the response is an error or the server refuses the connection, check the uvicorn output for import errors — a missing Python dependency or an incorrect `AUTODEV_REPO_PATH` will surface there.

**First load.** When `pipeline_status` is idle or unknown and no queue row shows a busy live pipeline, the dashboard opens **Project Ideas** by default. Use **Setup & Preflight** to select the repository path, run preflight, and launch. **Pipeline Monitor** is where you watch an active or resumed run.

While Project Ideas is waiting on an assistant reply after you send a chat message, **Generate Roadmap** and **Regenerate Roadmap** stay disabled until that reply finishes, so roadmap conversion uses the PRD returned with that response (not a stale snapshot).

You can also edit a PRD section directly without asking the agent: each section has an **Edit** button (disabled while a reply is in flight) that opens the section body in a textarea. Saving rewrites that section of `prd_draft.md` immediately and records a breadcrumb the agent sees at the start of its next turn (`[SYSTEM EVENTS]` block), so it treats the file on disk as authoritative instead of reverting your change. Manual edits show the same **Changed** badge, diff view, and one-click **Revert** as agent edits.

Before opening Project Ideas for the first time, run the POST `/hooks/agent` check in **New User Webhook Checklist** so token mismatches are caught early.

To run as a background service, see `ui/autodev-ui.service` (Linux/WSL2 systemd unit) or `ui/com.autodev.ui.plist` (macOS LaunchAgent). The install script prints OS-specific next steps after setup completes.

---

## Known Compatible OpenClaw Version

Tested against OpenClaw 2026.5.18 — earlier versions may have schema differences in `pipeline_state.json`. See openclaw.json requirements below.

The fields Lullabeast reads from `pipeline_state.json` are: `pipeline_status`, `current_agent`, `current_phase`, `current_phase_raw_id`, `planner_retries`, `executor_retries`, `reviewer_retries`, `last_action_timestamp`, and `project_path`. Values of **`current_phase_raw_id`** (for example `INT-E1`) are the same phase identifiers used in the project’s **`roadmap.md`**. If your OpenClaw version writes different field names, the UI status endpoint will return partial data.

---

## `openclaw.json` Requirements

Lullabeast reads the following keys from `~/.openclaw/openclaw.json`. The **orchestrator and UI** treat this file as read-only. **`install.sh` step 8** may update it atomically when you confirm the prompts: normalize the **`hooks`** block for webhook calls (`enabled`, `token`, `allowRequestSessionKey`, `allowedSessionKeyPrefixes`), optionally set `tools.profile` to `coding`, and add any missing pipeline agent entries plus `hooks.allowedAgentIds` for those IDs. Other keys are preserved.

### `agents.list` and pipeline agents

Webhook routing uses `agents.list[]`. Some OpenClaw exports include `agents.defaults` but omit `agents.list`. The installer creates `agents.list` when missing, then appends entries for **planner**, **executor**, **reviewer**, **escalation**, **prd-creator**, and **roadmap-converter** (skipping IDs already present). New coding agents omit per-agent `tools` so `tools.profile` applies; **escalation** gets an explicit read/write-only `tools` block when first added. **`roadmap-converter`** copies `tools` from **prd-creator** when that entry defines them.

### `tools.profile` vs per-agent tools

OpenClaw applies a **global tool profile** (`tools.profile`: `minimal` | `coding` | `messaging` | `full`) as the baseline allowlist, then per-agent `tools` can further restrict or extend depending on version and UI presets. That is why the gateway can show **Coding** for planner/executor/reviewer even when those entries do not list every tool explicitly. For Lullabeast’s pipeline, **`coding` or `full`** is appropriate; see [OpenClaw — Tools and Plugins](https://docs.openclaw.ai/tools).

**`hooks.token` vs `gateway.auth.token`** — These are different secrets. **`hooks.token`** is the **Bearer** secret for **`POST /hooks/agent`**: the orchestrator and Lullabeast UI send it in the `Authorization` header when invoking agents. If it is wrong or missing, invocations return **401** and the pipeline stalls. **`gateway.auth.token`** (or similar gateway Control UI / API auth in your OpenClaw version) protects **browser or REST access to the gateway itself** — it does **not** substitute for `hooks.token`. The installer can generate `hooks.token` and suggest storing the same value as **`AUTODEV_HOOKS_TOKEN`** (or `hooks_token` in `ui/config.json`) so the UI matches the gateway. The orchestrator also uses **`gateway.auth.token`** (with **`gateway.port`**) for a best-effort WebSocket **`sessions.abort`** on the **previous** executor session before starting executor attempt N+1, so a stale run does not keep streaming after a retry.

Recommended **`hooks`** shape for Lullabeast (installer converges toward this without clobbering unrelated keys):

```json
{
  "hooks": {
    "enabled": true,
    "token": "your-webhook-bearer-secret",
    "allowRequestSessionKey": true,
    "allowedSessionKeyPrefixes": ["pipeline:", "ideas:"]
  }
}
```

Session keys such as `pipeline:phase-1:...` and idea flows under `ideas:` must be allowed when the gateway enforces prefix rules.

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

**`agents.defaults.heartbeat.every`** — Set this to `"0m"` to disable the native OpenClaw heartbeat. If left at a non-zero interval (e.g. `"30m"`), OpenClaw's heartbeat will pull agents to the foreground on a schedule, interrupting active pipeline runs and causing model-swap interruptions. Lullabeast's own heartbeat watchdog (`heartbeat_cron.py`) provides crash recovery independently.

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

## Cost metrics: configuring OpenClaw so Lullabeast can report run cost

Lullabeast does **not** compute model cost. It reads `usage.cost.total` directly from each OpenClaw session JSONL row (`~/.openclaw/agents/<role>/sessions/<id>.jsonl`) and sums those values into `metrics.jsonl` and the **Pipeline Complete** panel. When OpenClaw writes zero for `cost.total`, Lullabeast's UI correctly hides the cost card and the **Cost** column — there is nothing to display. If you want cost reporting, **OpenClaw must populate that field**.

There is no single switch that works for every provider/model. The steps below are the rough order of operations that produces non-zero `cost.total` in most setups. Treat it as a checklist, not a script.

### Step 1 — Enable pricing in `~/.openclaw/openclaw.json`

OpenClaw computes `cost.total` per assistant turn only when pricing is enabled. The `models` block at the gateway level must contain:

```json
{
  "models": {
    "pricing": {
      "enabled": true
    }
  }
}
```

If `pricing` is missing or `enabled: false`, every `cost.total` in every session JSONL is `0` regardless of token volume.

### Step 2 — Verify your provider exposes pricing to OpenClaw

OpenClaw needs a price (per-million input/output/cache tokens) for **every model your pipeline agents use**. Three common sources, in rough priority order:

1. **Provider-reported pricing.** Providers that expose token cost in their API response (e.g. some OpenRouter routes that include a `cost` field) feed OpenClaw directly. Nothing further to configure.
2. **Built-in OpenClaw price tables.** OpenClaw ships rates for first-party Anthropic models (`claude-opus-*`, `claude-sonnet-*`, `claude-haiku-*`) and a handful of common third-party models. These work out of the box once `pricing.enabled` is `true`.
3. **Explicit `pricing` block on the model entry.** If your model is not in OpenClaw's table and your provider does not report cost, add a `pricing` object to the model definition under `models.{model-id}.pricing`. Consult your OpenClaw version's docs for the exact field names (typical shape is `inputPerMillion`, `outputPerMillion`, `cacheReadPerMillion`, `cacheWritePerMillion`). Without this, OpenClaw has no way to convert tokens into dollars.

**Local providers** (llama-server, Ollama, vLLM) generally have no real cost. You can leave them without pricing — `cost.total` will be `0`, Lullabeast will hide the cost UI for runs that used only local models, and no report is wrong.

### Step 3 — Restart the OpenClaw gateway after config changes

Pricing is read at gateway start. **Existing agent sessions are baked at session-creation time** — they will not retroactively gain cost data when you flip `pricing.enabled`. To force a clean slate:

1. Stop the OpenClaw gateway.
2. (Optional but recommended) Edit `~/.openclaw/agents/<role>/sessions/sessions.json` and remove entries for the pipeline agents, or delete their `.jsonl` files.
3. Restart the gateway.

New sessions created from this point will write `cost.total` per assistant message.

### Step 4 — Confirm cost data is flowing

After running at least one pipeline phase, inspect a recent session JSONL:

```bash
grep -o '"cost":{[^}]*}' ~/.openclaw/agents/executor/sessions/*.jsonl | head -3
```

A healthy result looks like:

```
"cost":{"input":0.0012,"output":0.0048,"cacheRead":0.0001,"cacheWrite":0,"total":0.0061}
```

If every `total` is still `0`:

- Re-check `models.pricing.enabled` in `openclaw.json`.
- Confirm the model your agent actually used (look at the `model` field in the session JSONL — note that fallback can swap the model silently) has pricing defined either by OpenClaw or by an explicit `pricing` block.
- Check `~/.openclaw/logs/` for OpenClaw warnings about missing rate tables.

Once `cost.total` is non-zero on disk, Lullabeast surfaces it automatically — no Lullabeast restart needed; the Pipeline Complete panel reads `metrics.jsonl` on each poll.

### What Lullabeast does with the data

- **Pipeline Complete panel** (left side, post-completion): shows total cost plus a planner/executor/reviewer breakdown when `total_cost > 0`. The COST column in the per-phase table appears when any phase has cost data.
- **Roadmap panel** phase breakdown: shows **Cost: $X.XX** in the expanded run-metrics block for any phase with `cost_total > 0`. Phases that ran on local models (zero cost) simply omit the row.
- **No backfill.** Phases that ran before pricing was configured stay at zero forever — re-running a phase will populate cost only for that new run.

---

## Re-approving Gate Scripts

OpenClaw maintains an `exec-approvals.json` file at `~/.openclaw/exec-approvals.json`. This file records which shell scripts and Python files each agent is permitted to execute. Gate scripts (the Python files in `autodev/pipeline/gate_scripts/`) must be pre-approved, or agent sessions will refuse to run them.

**Why paths changed.** Before this migration, gate scripts lived at `~/.openclaw/gate_scripts/`. They now live at `<repo>/autodev/pipeline/gate_scripts/`. If you previously approved gate scripts from the old location, `exec-approvals.json` still contains the old absolute paths. The orchestrator will attempt to execute scripts at the new paths, which OpenClaw will not recognise as approved.

**How to detect this.** `install.sh` step 6 greps `exec-approvals.json` for gate_scripts entries that do not match your `AUTODEV_REPO_PATH`. If stale entries are found, it prints them and warns you to re-approve.

**How to re-approve.** Open the OpenClaw UI, navigate to the exec-approvals section (Settings → Execution Approvals or equivalent in your version), and approve each gate script at its new path:

- `<repo>/autodev/pipeline/gate_scripts/planner_gate.py`
- `<repo>/autodev/pipeline/gate_scripts/executor_gate.py`
- `<repo>/autodev/pipeline/gate_scripts/reviewer_gate.py`
- `<repo>/autodev/pipeline/gate_scripts/phase_resolver.py`
- `<repo>/autodev/pipeline/gate_scripts/phase_init.py`
- `<repo>/autodev/pipeline/gate_scripts/repo_init_check.py`

The simplest way to trigger the approval prompt is to start a pipeline run — OpenClaw will pause and ask for approval the first time each script is encountered.

---

## Pipeline Monitor: git checkout recovery

When `pipeline_state.json` surfaces a **Git operation failed** message, the Pipeline Monitor shows **Recover Git**. That opens a dialog to confirm the **Branch to return to** (prefilled from context). The server endpoint **`POST /api/pipeline/git-recover`** runs **`git stash push --include-untracked`**, then **`git checkout`** on the resolved branch name, then updates pipeline state so the run can continue. It does **not** run **`git reset`**. **`GET /api/state`** includes **`git_recover_suggested_branch`**: the same branch the UI prefills, derived from optional **`base_branch`** in `ui/config.json` and otherwise from repository detection (`main`, `master`, `develop`, `origin/HEAD`, `init.defaultBranch`, in that style). Override the field in the dialog only if checkout failed because the wrong branch was targeted.

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
