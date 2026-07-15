# Lullabeast Reference

Lullabeast is an autonomous development pipeline that runs on top of OpenClaw: a planner → executor → reviewer loop against your project repository, with a web dashboard and a PRD ideation system. For dashboard terminology (pipeline and queue states, skills, metrics), see [GLOSSARY.md](GLOSSARY.md).

**Installing or developing? Use Docker.** Users follow the quickstart in [deploy/README.md](deploy/README.md); contributors use the [development container](deploy/README.md#development-container). There is no supported bare-metal install path: the container runs the installer internally and gates every boot on the doctor. This page is the reference material that applies no matter how Lullabeast is running: the project contract, dashboard orientation, failure playbooks, OpenClaw configuration, cost metrics, and local models.

---

## The project contract

Three project-level documents drive every run:

- **`prd.md`** — *what* the project must do. Source of truth for user intent; the reviewer reads this first.
- **`roadmap.md`** — *how* the work is broken into phases. Every phase must include a `**Behavioral Verification:**` block with three sub-bullets (`User-observable`, `How we'll check`, `If this fails, the user sees`). Preflight refuses to stage a project whose roadmap is missing the block.
- **`verification.md`** — *proof*. Project-level document derived from the PRD that names the project type, entry point, public surface, and acceptance stack. Preflight refuses to stage a project without a valid `verification.md`. The user never edits it directly — if it's wrong, the fix is to edit the PRD and re-run conversion from the Ideas screen.

The Ideas screen authors all three for you (idea → PRD chat → roadmap conversion); you can also bring an existing project directory that already carries them.

---

## Dashboard orientation

**First load.** When `pipeline_status` is idle or unknown and no queue row shows a busy live pipeline, the dashboard opens **Project Ideas** by default. Use **Setup & Preflight** to select the repository path, run preflight, and launch. **Pipeline Monitor** is where you watch an active or resumed run.

**Switching the active project.** Lullabeast runs one active project at a time. To point it at a different repository, stop the pipeline first, then click the project path in the **Pipeline Monitor** header to open the switch dialog — it runs preflight against the new repo before starting. Switching re-targets the single active project; to line up several projects, use the queue, which runs them one after another rather than concurrently.

While Project Ideas is waiting on an assistant reply after you send a chat message, **Generate Roadmap** and **Regenerate Roadmap** stay disabled until that reply finishes, so roadmap conversion uses the PRD returned with that response (not a stale snapshot).

You can also edit a PRD section directly without asking the agent: each section has an **Edit** button (disabled while a reply is in flight) that opens the section body in a textarea. Saving rewrites that section of `prd_draft.md` immediately and records a breadcrumb the agent sees at the start of its next turn (`[SYSTEM EVENTS]` block), so it treats the file on disk as authoritative instead of reverting your change. Manual edits show the same **Changed** badge, diff view, and one-click **Revert** as agent edits.

---

## The doctor

`python -m autodev.installer.doctor` checks every documented silent-failure mode in one pass: paths, Python deps, git identity, the PRD conversion prompt, `openclaw.json` health (hooks, agents, context limits, `tools.profile`, heartbeat), the OpenClaw version floor and known-bad releases, gateway reachability, plugin bundle freshness and hook registration, exec-approvals paths, `pipeline-project` symlink agreement, stale locks, Playwright, tokens, and ports. It is strictly read-only, and every red line carries a one-line fix hint.

Flags: `--json` (machine-readable report), `--quiet` (print only problems; warnings stop affecting the exit code), `--live` (also POSTs the webhook ping, which creates a real OpenClaw session, so it is opt-in). Exit codes: 0 all ok, 1 any failure, 2 warnings only. Network and subprocess probes are bounded by `DOCTOR_PROBE_TIMEOUT` seconds (default 5).

The dashboard exposes the same report at `GET /api/doctor` and renders it as the **Health** card on the Settings screen. In the container:

```bash
docker compose exec lullabeast python -m autodev.installer.doctor
```

One check is mode-gated: `template_conformance` diffs the live `openclaw.json` against the golden template at `deploy/openclaw.template.json` and runs only in owned-OpenClaw installs (the container). The decision record behind that template is `deploy/CONFIG-AUDIT.md`.

---

## Silent failure modes (four cases)

**Run the doctor first; it checks all four.** Case 1 is `gateway_up` (plus the opt-in `--live` webhook ping), case 2 is `env_paths`, case 3 is `conversion_prompt`, and case 4 is `symlink_consistency`. The prose below is the reference behind those checks: what each failure looks like, why it happens, and how to verify a fix by hand. (Shell commands assume a shell inside the container: `docker compose exec lullabeast bash`.)

### 1. OpenClaw gateway not running

**What it looks like.** The pipeline UI shows status as `RUNNING` but no agents are ever invoked. Phases never advance. No error appears in the UI.

**What's happening.** The orchestrator sends HTTP POST requests to `http://localhost:18789/hooks/agent` to wake each agent. If the OpenClaw gateway is not running, these requests fail silently (the orchestrator logs the failure but the UI does not surface it).

**How to verify.**

```bash
curl -s http://localhost:18789/v1/models | head -20
```

A healthy gateway returns a JSON models list; connection refused means the gateway is down (in the container, a dead gateway stops the whole container, so check `docker compose logs`). To validate webhook auth end to end, POST with the Bearer secret (GET checks alone are insufficient):

```bash
curl -sS -o /dev/null -w "HTTP %{http_code}\n" -X POST http://127.0.0.1:18789/hooks/agent \
  -H "Authorization: Bearer <hooks.token>" -H "Content-Type: application/json" \
  -d '{"agentId":"prd-creator","sessionKey":"ideas:install-check:0","wakeMode":"now","message":"ping"}'
```

Expect `HTTP 200`. `HTTP 401` means the Bearer token does not match `hooks.token` in `openclaw.json`.

### 2. `autodev_repo_path` misconfigured

**What it looks like.** Clicking "Launch" in the setup UI returns an error like `orchestrator.py not found at ...`. Or the orchestrator launches but immediately fails with `No module named sentinel_poller`.

**What's happening.** The UI server's `_spawn_orchestrator` function constructs the path to `orchestrator.py` from the `autodev_repo_path` config value. The container asserts this key on every boot, so it only breaks when an override points somewhere stale.

**How to verify.** Check that `$AUTODEV_REPO_PATH/autodev/pipeline/orchestrator.py` exists (in the container, `/app/autodev/pipeline/orchestrator.py`). If `ui/config.json` contains an `autodev_repo_path` key, that value takes precedence over the environment; make sure it points at the repo root.

### 3. Conversion prompt file not found

**What it looks like.** The `/api/ideas/{id}/convert` endpoint returns an error. The rest of the ideas system (creating sessions, sending messages, readiness assessment) works normally.

**What's happening.** Converting a PRD draft to a roadmap needs the conversion-instructions prompt. The server resolves it in this order: the `conversion_prompt_path` key in `ui/config.json` (if set), then the repo-bundled default at `<repo>/autodev/prompts/prd-to-roadmap-conversion.txt`, then a built-in inline fallback. The bundled file ships with the repo, so this only breaks if `conversion_prompt_path` is overridden to a path that does not exist, or the bundled file was deleted.

**How to fix.** Leave `conversion_prompt_path` unset to use the bundled prompt, or point it at a readable file.

### 4. `pipeline-project` symlink out of sync with `pipeline_state.json`

**What it looks like.** After copying `pipeline_state.json` from another machine, restoring an old state file, or pointing tests at the wrong project, the **Pipeline Monitor** and queue can disagree about which git repository is active. Until recently this could look like a silent “wrong project” run; the UI now reconciles on resume when safe.

**What's happening.** Under `AUTODEV_PIPELINE_ROOT` (`/data/pipeline-state` in the container), the **`pipeline-project`** entry is a symlink to the active project repository. `pipeline_state.json` records the canonical **`project_path`**. The orchestrator and agents work through that symlink.

**What the UI does.** `POST /api/resume-orchestrator` compares the symlink’s real path to `project_path` in `pipeline_state.json`. If they differ, it **repoints** the symlink to match state (returns JSON **`reconciled`: true** when it did). **`HTTP 422`** if the symlink location cannot be updated safely (for example a real directory where the link should be). **`HTTP 409`** if the pipeline lock shows an orchestrator already running. **`HTTP 503`** if spawning the orchestrator fails; if repointing had just succeeded, the JSON body can still include **`reconciled`: true** so you can fix the underlying spawn error and retry.

**How to verify manually.** Compare `readlink -f` on `$AUTODEV_PIPELINE_ROOT/pipeline-project` to the `project_path` field inside `$AUTODEV_PIPELINE_ROOT/pipeline_state.json`.

---

## Security and network exposure

The dashboard and **`/api/*`** require a **single shared access token**: `AUTODEV_UI_TOKEN` (the container generates and persists it; the boot log prints the tokenized URL). Opening `http://127.0.0.1:<port>/?token=<AUTODEV_UI_TOKEN>` once authorizes the browser via an HttpOnly cookie (30 days); scripts and `curl` send the same value as `Authorization: Bearer <token>` instead. The `?token=` query is honored only on `/`, never on `/api/*`, so the secret stays out of request logs. `GET /health` and `/static/*` are exempt. This is single-user, local-tool auth — one token, no accounts or roles — so the endpoints still execute code for whoever holds it; treat Lullabeast as **operator tooling for a trusted machine**, not a multi-tenant service.

**Token sources, in precedence order:** the `AUTODEV_UI_TOKEN` environment variable, then `ui_token` in `ui/config.json`. To rotate the token, change it and restart. **With no token configured at all**, the server runs in the legacy open mode: loopback requests are served unauthenticated and non-loopback requests are refused (403) — the startup log warns loudly.

Both published ports (dashboard and gateway) bind to the host's loopback only. Exposing either beyond loopback is a conscious edit: use only on a trusted LAN behind a firewall, or put a reverse proxy with TLS and its own authentication in front. Do not expose the raw ports to the public internet — the shared token is not a substitute for TLS + real access control. See [SECURITY.md](SECURITY.md).

---

## Per-project prerequisites (`.env.example`)

A project's prerequisites live in its `verification.md` under a `## Prerequisites` block (the
roadmap-converter authors it from your Project Ideas conversation; you can also hand-edit it). **Names
only, never values:**

```markdown
## Prerequisites

### Tools
- node — Node.js 20+ runtime — needed by all
- unity6 — Unity 6 LTS + Android Build Support — needed by INFRA-1

### Environment
- API_BASE_URL (config) — base URL the app calls — used by all
- OPENAI_API_KEY (secret) — provider key for the app's LLM calls — used by CORE-3
```

**`### Tools` is documentation only — Lullabeast does not check it.** We tried probing declared tools
on PATH and it produced false-positive blocks (you can't reliably `which` `Python 3.10+` or `Unity 6
LTS`, and the converter writes human names, not binary tokens). Rather than block you on a bad signal,
we removed the check. Use `### Tools` as a note-to-self of what the build environment needs. If a tool
is genuinely missing, the pipeline will surface it when a phase fails (later, but honestly) — there is
no up-front gate.

**`### Environment` → a committed `.env.example`.** From the declared env-var names, Preflight writes a
committed **`.env.example`** in the project (each key as a blank `KEY=` line preceded by a `# purpose`
comment — append-only, never overwriting a line you filled). Copy it to your own `.env` and fill in the
values. Lullabeast writes only the blank example; it **never** ingests, transmits, stores, or logs an env
**value** — only names/types/purposes are captured (in `verification.md`). Env vars are **not** a
Preflight gate — they're yours to set.

**Your real `.env` is kept out of git.** Because the pipeline commits the whole project tree each phase
(`git add .`), Preflight also ensures the project `.gitignore` ignores `.env` (and keeps `.env.example`
trackable) — so the secrets you put in `.env` are never committed or pushed. This is automatic; you
don't have to do anything.

**Point Preflight at a single project, not a parent folder.** If the directory you select is not yet a git
repository, Preflight initializes one for you (`git init` + an initial commit) so the pipeline has a HEAD to
build on. To keep that convenience from misfiring, Preflight **refuses to auto-init a directory that already
contains other git repositories** — e.g. selecting a folder that holds many repos fails Preflight with
an actionable message instead of running `git init` + `git add .` across the whole tree. Always aim Lullabeast
at one project's own directory.

**How the build reads your `.env` (DEC-5).** The agent webhook has no env channel — agents inherit the
OpenClaw gateway's environment. So the project's **entry-point/test command must load its own `.env`**
(most frameworks do this via a dotenv loader; otherwise prepend `set -a; . ./.env; set +a` to the
command). This is a contract, not something Lullabeast enforces.

**Paid/external calls are mocked during the build (DEC-6).** The pipeline mocks paid/external APIs by
default and accepts mocked / recorded / local-stub evidence as satisfying behavioral verification — so a
paid-API feature is built and verified **without spending your provider budget**. There is no live-paid
call inside the automated loop. Final live validation against a paid provider is **yours** to run
afterward, with your own key, watching your own billing.

---

## Inbound escalation replies (answer an escalation from your phone)

When a phase escalates, the escalation agent notifies you on your configured channel (Signal, Discord, …). By default you answer from the **dashboard**. You can also enable a parallel **inbound** path so a reply typed on that channel becomes the recovery command — useful when you are away from the dashboard.

**How it works.** The notification includes a short **correlation token** (e.g. `e2.ab12cd`). You reply starting with that token followed by a command — e.g. `e2.ab12cd reset phase`. The `autodev-pipeline-signals` plugin forwards the reply to the UI server's `POST /api/escalation/inbound`, which maps it to a pipeline command and writes it through the **same files the dashboard uses**. The escalation agent never applies the command itself — the UI server does, exactly as for a dashboard answer. The answer is always applied to the project that **escalated** (matched by the token), never to whatever project happens to be active when your reply lands.

**Recognized replies** (case-insensitive; start with the token): `retry`, `proceed` (or `continue`), `stop`, `reset phase`, `reset execution`, `reset reviewer`; `skip` and `nuclear reset` only on an explicit, unambiguous request. An unrecognized, **negated** ("don't stop"), or ambiguous reply gets a clarification request — it never defaults to a command. Reset caps still apply.

**Enable it (opt-in).** The forwarder is dormant until these reach the OpenClaw gateway's environment — in the container, add them to `deploy/.env` and restart:

| Variable | Purpose |
|----------|---------|
| `AUTODEV_ESCALATION_CHANNEL` | The channel the escalation agent is bound to (e.g. `signal`). Replies on this channel are forwarded; **unset = inbound disabled**. |
| `AUTODEV_HOOKS_TOKEN` | The hooks Bearer secret (same value the UI uses). The plugin presents it; the endpoint verifies it. Already set inside the container. |
| `AUTODEV_UI_URL` | The UI server base URL. Defaults to `http://127.0.0.1:18790`. |

If `AUTODEV_ESCALATION_CHANNEL` or `AUTODEV_HOOKS_TOKEN` is unset, the plugin no-ops and behavior is unchanged (replies route to the escalation agent as before). No `openclaw.json` change is required — the forwarder runs on the `inbound_claim` hook, before routing.

**Security.** `POST /api/escalation/inbound` is the only endpoint exempt from the dashboard token; it authenticates with the **hooks** Bearer secret and **fails closed** (no hooks token configured → it refuses). Keep `AUTODEV_HOOKS_TOKEN` secret, and prefer loopback for the UI server.

---

## Known Compatible OpenClaw Version

Requires OpenClaw v2026.5.18 or newer. The `autodev-pipeline-signals` plugin was first built against 2026.5.18 and has run on every release since; earlier versions may have schema differences in `pipeline_state.json`. The container bakes a pinned, verified version; the doctor's `openclaw_version` check knows this floor and the known-bad releases. See openclaw.json requirements below.

The fields Lullabeast reads from `pipeline_state.json` are: `pipeline_status`, `current_agent`, `current_phase`, `current_phase_raw_id`, `planner_retries`, `executor_retries`, `reviewer_retries`, `last_action_timestamp`, and `project_path`. Values of **`current_phase_raw_id`** (for example `INT-E1`) are the same phase identifiers used in the project’s **`roadmap.md`**. If your OpenClaw version writes different field names, the UI status endpoint will return partial data.

---

## `openclaw.json` Requirements

The container renders and reconciles `openclaw.json` from the golden template (`deploy/openclaw.template.json`) on every boot, so none of this needs hand-configuration there. It matters when you manage models and providers by hand in OpenClaw (the setup wizard's "skip model setup" path) or otherwise operate your own OpenClaw tree. The orchestrator and UI treat the file as read-only.

### `agents.list` and pipeline agents

Webhook routing uses `agents.list[]`. Six agents must be registered: **planner**, **executor**, **reviewer**, **escalation**, **prd-creator**, and **roadmap-converter** (also listed in `hooks.allowedAgentIds`). Coding agents omit per-agent `tools` so `tools.profile` applies; **escalation** carries an explicit read/write-only `tools` block.

### `tools.profile` vs per-agent tools

OpenClaw applies a **global tool profile** (`tools.profile`: `minimal` | `coding` | `messaging` | `full`) as the baseline allowlist, then per-agent `tools` can further restrict or extend depending on version and UI presets. That is why the gateway can show **Coding** for planner/executor/reviewer even when those entries do not list every tool explicitly. For Lullabeast’s pipeline, **`coding` or `full`** is appropriate; see [OpenClaw — Tools and Plugins](https://docs.openclaw.ai/tools).

**`hooks.token` vs `gateway.auth.token`** — These are different secrets. **`hooks.token`** is the **Bearer** secret for **`POST /hooks/agent`**: the orchestrator and Lullabeast UI send it in the `Authorization` header when invoking agents. If it is wrong or missing, invocations return **401** and the pipeline stalls. **`gateway.auth.token`** (or similar gateway Control UI / API auth in your OpenClaw version) protects **browser or REST access to the gateway itself** — it does **not** substitute for `hooks.token`. The orchestrator also uses **`gateway.auth.token`** (with **`gateway.port`**) for a best-effort WebSocket **`sessions.steer`** interrupt on a stale agent session before starting the next attempt, so a stale run does not keep streaming after a retry.

Required **`hooks`** shape:

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

---

## Cost metrics: configuring OpenClaw so Lullabeast can report run cost

Lullabeast does **not** compute model cost. It reads `usage.cost.total` directly from each OpenClaw session JSONL row and sums those values into `metrics.jsonl` and the **Pipeline Complete** panel. When OpenClaw writes zero for `cost.total`, Lullabeast's UI correctly hides the cost card and the **Cost** column — there is nothing to display. If you want cost reporting, **OpenClaw must populate that field**.

The container's shipped default models come with complete pricing blocks, so runs on the defaults report real dollars out of the box. The steps below are for models you add yourself. Treat it as a checklist, not a script.

### Step 1 — Enable pricing in `openclaw.json`

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

If `pricing` is missing or `enabled: false`, every `cost.total` in every session JSONL is `0` regardless of token volume. (The container template ships it enabled.)

### Step 2 — Verify your provider exposes pricing to OpenClaw

OpenClaw needs a price (per-million input/output/cache tokens) for **every model your pipeline agents use**. Three common sources, in rough priority order:

1. **Provider-reported pricing.** Providers that expose token cost in their API response (e.g. some OpenRouter routes that include a `cost` field) feed OpenClaw directly. Nothing further to configure.
2. **Built-in OpenClaw price tables.** OpenClaw ships rates for first-party Anthropic models and a handful of common third-party models. These work out of the box once `pricing.enabled` is `true`.
3. **Explicit `pricing` block on the model entry.** If your model is not in OpenClaw's table and your provider does not report cost, add a `pricing` object to the model definition under `models.{model-id}.pricing`. Consult your OpenClaw version's docs for the exact field names. Without this, OpenClaw has no way to convert tokens into dollars.

**Local providers** (llama-server, Ollama, vLLM) generally have no real cost. You can leave them without pricing — `cost.total` will be `0`, Lullabeast will hide the cost UI for runs that used only local models, and no report is wrong.

### Step 3 — Restart the OpenClaw gateway after config changes

Pricing is read at gateway start. **Existing agent sessions are baked at session-creation time** — they will not retroactively gain cost data when you flip `pricing.enabled`. New sessions created after a gateway restart write `cost.total` per assistant message; to force a clean slate, remove the pipeline agents' entries from `agents/<role>/sessions/sessions.json` (and their `.jsonl` files) while the gateway is stopped.

### Step 4 — Confirm cost data is flowing

After running at least one pipeline phase, inspect a recent session JSONL (inside the container the OpenClaw tree is `/data/openclaw`):

```bash
grep -o '"cost":{[^}]*}' /data/openclaw/agents/executor/sessions/*.jsonl | head -3
```

A healthy result looks like:

```
"cost":{"input":0.0012,"output":0.0048,"cacheRead":0.0001,"cacheWrite":0,"total":0.0061}
```

If every `total` is still `0`: re-check `models.pricing.enabled`; confirm the model your agent actually used (the `model` field in the session JSONL — fallback can swap it silently) has pricing defined; check OpenClaw's logs for missing-rate-table warnings.

Once `cost.total` is non-zero on disk, Lullabeast surfaces it automatically — no restart needed; the Pipeline Complete panel reads `metrics.jsonl` on each poll.

### What Lullabeast does with the data

- **Pipeline Complete panel** (left side, post-completion): shows total cost plus a planner/executor/reviewer breakdown when `total_cost > 0`. The COST column in the per-phase table appears when any phase has cost data.
- **Roadmap panel** phase breakdown: shows **Cost: $X.XX** in the expanded run-metrics block for any phase with `cost_total > 0`. Phases that ran on local models (zero cost) simply omit the row.
- **No backfill.** Phases that ran before pricing was configured stay at zero forever — re-running a phase will populate cost only for that new run.

---

## Running with local models (experimental)

OpenClaw owns all model configuration, so Lullabeast doesn't care whether an agent runs on a cloud
or a local model. The container wires a local provider for you: one `LOCAL_MODEL_URL` line in
`deploy/.env` (or the setup wizard's detected-server list on a keyless boot) generates the provider
entry, and roles point at it via the `*_MODEL` variables — see
[deploy/README.md](deploy/README.md) under "Local models on the host". A fully-local configuration
has been run end-to-end (planner/executor/reviewer on Qwen-27B-class models via a local llama.cpp /
llama-swap server), and it works — but this is still an area of active experimentation, not a tuned,
guaranteed setup. Treat the notes below as a starting point, not a spec.

- **`"apiKey": "no-key"` is mandatory on every local provider entry.** Without it OpenClaw inherits
  the cloud auth profile and silently falls back to a cloud model — with no error. The only signal
  is `fallbackNoticeReason: auth` in the agent's `sessions.json`. (The `LOCAL_MODEL_URL` path sets
  it for you; hand-added providers make it your responsibility.)
- **A multi-modal model is needed for the executor and reviewer** (the reviewer does
  screenshot-based visual review on UI phases); it's only recommended for the planner.
- **The reviewer's infra backstop may need raising** (`AUTODEV_INFRA_BACKSTOP_REVIEWER`) — a
  thorough long-context local reviewer pass can take minutes per model call on local hardware.
- **The idea-to-PRD chat (`prd-creator`) does noticeably better on a cloud model** in testing, so a
  cloud PRD agent with a local build loop is a reasonable mix.
- **Output quality is a real trade-off.** Local results have been functional with room for
  improvement; a hybrid setup (some roles local, some cloud) is worth exploring rather than
  assuming all-local or all-cloud is best. Which combination wins is not settled.

Qwen-style models need llama-server flags to suppress inline `<think>` tokens (they corrupt JSON
tool-call parsing) — see the Qwen notes in [CLAUDE.md](CLAUDE.md) under *Agent LLM Configuration*.

---

## Re-approving Gate Scripts

OpenClaw records which scripts each agent may execute in `exec-approvals.json`. The gate scripts (`autodev/pipeline/gate_scripts/*.py`) must be approved or agent sessions will refuse to run them. The simplest way to trigger the approval prompt is to start a pipeline run — OpenClaw pauses and asks (in its own UI, linked from the dashboard's Settings screen) the first time each script is encountered. The doctor's `exec_approvals` check flags entries pointing at stale paths.

---

## Pipeline Monitor: git checkout recovery

When `pipeline_state.json` surfaces a **Git operation failed** message, the Pipeline Monitor shows **Recover Git**. That opens a dialog to confirm the **Branch to return to** (prefilled from context). The server endpoint **`POST /api/pipeline/git-recover`** runs **`git stash push --include-untracked`**, then **`git checkout`** on the resolved branch name, then updates pipeline state so the run can continue. It does **not** run **`git reset`**. **`GET /api/state`** includes **`git_recover_suggested_branch`**: the same branch the UI prefills, derived from optional **`base_branch`** in `ui/config.json` and otherwise from repository detection (`main`, `master`, `develop`, `origin/HEAD`, `init.defaultBranch`, in that style). Override the field in the dialog only if checkout failed because the wrong branch was targeted.
