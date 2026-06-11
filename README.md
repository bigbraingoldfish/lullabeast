<p align="center">
  <img src="ui/static/img/lullabeast_512.png" alt="Lullabeast — a rounded gold creature face" width="140">
</p>

# Lullabeast

**From plain English to shipped MVP.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/[[FILL: org/repo]]/actions/workflows/ci.yml/badge.svg)](https://github.com/[[FILL: org/repo]]/actions/workflows/ci.yml)
[![Runs on OpenClaw](https://img.shields.io/badge/runs%20on-OpenClaw-c9962e.svg)](https://docs.openclaw.ai)
![Status](https://img.shields.io/badge/status-beta-orange.svg)
![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20WSL2-lightgrey.svg)

Lullabeast is an open-source, local-first, autonomous multi-agent development pipeline: describe
what you want to build in plain English, and a team of LLM agents — **planner → executor →
reviewer** — implements it phase by phase against a real git repository, with deterministic gate
scripts checking every step and an escalation path back to you when a run gets stuck. Lullabeast
runs on [OpenClaw](https://docs.openclaw.ai) and **requires** it: Lullabeast is the pipeline and
dashboard, while the agents themselves run inside OpenClaw, which you install and run separately.
It is built on top of OpenClaw — not a fork of it, and not a competitor to it.

> **Status: beta.** Pre-release software, not production-ready. It runs as a **single-user tool
> on a trusted machine**: the dashboard and API are protected by a locally generated access
> token, but there are no user accounts or roles — keep it bound to loopback. Autonomous runs
> can and do fail; the escalation/recovery loop is a first-class part of the design, not an
> apology.

**Who it's for.** Today: developers willing to run a beta — you install two services, point
Lullabeast at a git repo, and supervise runs from a dashboard. The longer arc: the idea-to-PRD and
escalation flows are built so that someone who can *describe* software in plain English — not
necessarily write it — can take a project from idea to working MVP.

---

## Quick start

### Requirements

Read this before running anything — the first item is a separate install:

- **A running [OpenClaw](https://docs.openclaw.ai) gateway** — required; Lullabeast cannot run
  without it. Install it first ([install guide](https://openclaw.dev/install)) and have it
  listening on `localhost:18789`. Tested against **OpenClaw 2026.5.18**; earlier versions may
  have state-schema differences (see [SETUP.md](SETUP.md)).
- **Linux, macOS, or WSL2** — native Windows is unsupported (the pipeline uses POSIX `fcntl` locking).
- **Python 3.9+** and `git` — with a configured git identity: the pipeline makes commits in your
  project repos (the executor's commits, phase merges, init commits), so set it once —
  `git config --global user.name "Your Name"` and `git config --global user.email "you@example.com"`.
  `install.sh` checks this and fails fast with these commands if either is missing.
- **Node.js 22+** with `npm` — builds the OpenClaw signals plugin and the Playwright visual-review MCP.
  The MCP is **required for UI/INT phases**: the reviewer reads executor screenshots, and without it
  every UI/INT phase is rejected at the reviewer gate (`ERR_VISUAL_UNVERIFIED`). `install.sh` installs
  it by default — opt out with `--skip-playwright` only for runs that will not touch UI/INT phases.

### Install & run

```bash
# 1. Install and start OpenClaw first — Lullabeast cannot run without it.
#    https://openclaw.dev/install
curl -s http://localhost:18789/v1/models   # should respond; "connection refused" = gateway not up

# 2. Install Lullabeast.
git clone [[FILL: public repo URL — current origin is https://github.com/bigbraingoldfish/autodev-oc.git]] autodev-ui
cd autodev-ui
./install.sh            # interactive; registers agents with OpenClaw, generates your dashboard access token; safe to re-run

# 3. Run the dashboard — from the repo root; the -m module form is required.
source .env
python -m ui.server
```

> **Launch command:** `python -m ui.server`, run **from the repo root**, is the canonical way to
> start the dashboard (it binds `127.0.0.1` on the configured port, default `18790`). The script
> form `python ui/server.py` fails with `ModuleNotFoundError: No module named 'ui'` because the
> server uses package-absolute imports. `uvicorn ui.server:app --host 127.0.0.1 --port 18790` is
> the equivalent uvicorn invocation if you need CLI control of host/port.

The server prints your access URL at startup — open it
(**`http://127.0.0.1:18790/?token=<AUTODEV_UI_TOKEN>`**). That authorizes your browser via a
cookie (30 days); scripts can send the same token as a `Bearer` header instead. Then verify the
webhook wiring once — use POST, a GET check can miss token mismatches:

```bash
curl -sS -o /dev/null -w "HTTP %{http_code}\n" -X POST http://127.0.0.1:18789/hooks/agent \
  -H "Authorization: Bearer <hooks.token>" -H "Content-Type: application/json" \
  -d '{"agentId":"prd-creator","sessionKey":"ideas:install-check:0","wakeMode":"now","message":"ping"}'
```

`HTTP 200` means you're wired up; `401` means the Bearer token doesn't match `hooks.token` in
`openclaw.json`. The full walkthrough — including macOS LaunchAgent and Linux/WSL2 systemd units —
is in **[SETUP.md](SETUP.md)**.

---

## How it works

```text
  Your idea
  │
  ▼
  Project Ideas   —  chat a raw idea into a structured PRD
  │
  ▼
  Convert         —  the PRD becomes a phased roadmap + verification contract
  │
  ▼
  Build loop      —  Planner ─▶ Executor ─▶ Reviewer ─▶ Gates
  │                  ▲                                      │
  │                  └────────────── retry ◀ ───────────────┘
  ▼
  Escalation      —  on repeated failure, you answer and the run resumes
  │
  ▼
  Working MVP  ✅
```

1. **Ideate.** Chat with the PRD-creator agent until a raw idea is a structured `prd.md`.
2. **Convert.** The PRD becomes a phased `roadmap.md` (each phase with a behavioral-verification
   block) and a `verification.md` acceptance contract.
3. **Build.** The orchestrator runs the gate-checked planner → executor → reviewer loop for each
   phase; failing phases retry in fresh sessions until they pass or the retry budget runs out.
4. **Recover.** When a phase exhausts its budget, the run pauses and escalates to you with the
   failure context — answer (proceed / reset / skip / stop) and the run resumes.
5. **Done.** The dashboard shows a completion summary: per-phase attempts, review verdicts, and
   cost (when your OpenClaw models report it).

Queue several projects and Lullabeast works them in order, honoring dependencies between them.

---

## Architecture

Four pipeline agents and two ideation agents, sequenced by a single orchestrator state machine
that owns the git operations, blame attribution, and recovery logic:

- **Planner** — turns the current roadmap phase into a concrete implementation plan.
- **Executor** — writes the code and tests, then commits to a phase branch.
- **Reviewer** — verifies the result actually behaves as intended, including screenshot-based
  visual review for UI phases.
- **Gate scripts** — deterministic, LLM-free Python checkers between every handoff: file
  manifest, git diff, test results, behavioral evidence, unaccounted deletions. The gates are the
  pipeline's source of truth — no agent advances on its own say-so.
- **Escalation** — invoked only when gates and retries are exhausted; notifies you and pauses.
- **prd-creator / roadmap-converter** — drive the idea → PRD → roadmap front end.

Pipeline state (lock, queue, event log, ideas) lives in `<repo>/.autodev/`; OpenClaw's own config
and agent workspaces live under `~/.openclaw`. `ui/server.py` (all API routes) and
`autodev/pipeline/orchestrator.py` (the whole state machine) are intentionally single-file to keep
control flow auditable — read [CLAUDE.md](CLAUDE.md) before refactoring either. The full spec is
[autodev/docs/PIPELINE-SPEC.md](autodev/docs/PIPELINE-SPEC.md).

---

## How Lullabeast relates to OpenClaw

Lullabeast is built on top of [OpenClaw](https://docs.openclaw.ai) and requires it — a hard
dependency, the way a client needs its server. OpenClaw hosts the agent sessions, brokers every
model call, and owns all model/provider configuration: API keys and model choices live in
`openclaw.json`, never in Lullabeast — which is what keeps Lullabeast itself model-agnostic.
Lullabeast drives OpenClaw from the outside (webhook invocations in, session files out) and ships
a small OpenClaw plugin for activity signals; it contains no OpenClaw code. Not a fork, not a
competitor — a pipeline that needs a capable agent runtime, and uses OpenClaw as that runtime. New
to OpenClaw? Start with its [install guide](https://openclaw.dev/install).

---

## The dashboard

[[FILL: dashboard screenshot or short demo GIF — screenshots/ is currently empty; capture the Pipeline Monitor mid-run]]

- **Project Ideas** — chat an idea into a PRD, then generate the roadmap + verification contract.
- **Setup & Preflight** — point at a project repo, run preflight checks, launch the pipeline.
- **Pipeline Monitor** — watch the live planner → executor → reviewer loop, per-phase metrics, and
  a real-time activity feed; recover from git errors or answer escalations.
- **Queue** — line up multiple projects with dependency ordering; Lullabeast runs them sequentially.

---

## Security

- **The dashboard and `/api/*` require an access token** (`AUTODEV_UI_TOKEN`, generated by
  `install.sh`). Open the tokenized URL printed at startup to authorize your browser; scripts send
  the token as a `Bearer` header. This is single-user, local-tool auth — one shared token, no
  accounts, roles, or audit trail.
- **Stay on loopback anyway.** Bind to **`127.0.0.1`** (the default); the server refuses
  non-loopback requests unless a token is configured. Never expose the raw port to the internet —
  anything beyond a trusted LAN belongs behind a reverse proxy + TLS. See
  [SECURITY.md](SECURITY.md) and [SETUP.md — Security and network exposure](SETUP.md#security-and-network-exposure).
- The pipeline **executes agent-written code on the host** under your user account. Treat
  Lullabeast as operator tooling for a trusted machine, not a multi-tenant service.
- Secrets — the dashboard token (`AUTODEV_UI_TOKEN`) and the webhook Bearer token
  (`AUTODEV_HOOKS_TOKEN`) — live in `.env` (gitignored). Never commit them in `ui/config.json` or
  any tracked file.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| UI says `RUNNING` but no agents ever fire | OpenClaw gateway is down | `curl -s http://localhost:18789/v1/models` — connection refused means start the gateway |
| Webhook returns **401** | `hooks.token` ≠ `AUTODEV_HOOKS_TOKEN` | Sync the Bearer secret (install.sh step 8 does this) |
| Dashboard or `/api/*` returns **401** | browser not authorized / wrong `AUTODEV_UI_TOKEN` | Open the tokenized URL printed at server startup |
| `orchestrator.py not found` on launch | `.env` not sourced | `source .env` before starting uvicorn |
| Every **UI/INT** phase fails at the reviewer | Playwright MCP not installed | Re-run `./install.sh` without `--skip-playwright` |
| Header shows **Queue stalled** | all queued projects blocked / in dependency hold | Clear a parent or resume a banked escalation answer |

A deeper **"Silent failure modes"** walkthrough lives in [SETUP.md](SETUP.md#silent-failure-modes-four-cases).

---

## Tests

Two suites; both must pass before a change merges — CI ([ci.yml](.github/workflows/ci.yml)) runs
them on every push and pull request. Neither needs a live OpenClaw.

```bash
source .env
pytest autodev/tests/ -q     # pipeline: orchestration, sentinel polling, skill injection
pytest tests/ -q             # UI server: FastAPI routes + frontend
```

Dev dependencies are in [`requirements-dev.txt`](requirements-dev.txt).

---

## Known limitations

- **Beta.** State schemas, interfaces, and the install flow still change without deprecation.
- **Single-user by design.** One shared access token — no user accounts, roles, or multi-tenancy;
  agent-written code runs on your host.
- **Hard projects will escalate.** That is the designed behavior, not an edge case.
- **POSIX only.** Linux, macOS, or WSL2; native Windows is unsupported.

---

## Project layout

```
autodev/
  pipeline/         # orchestrator, sentinel poller, gate scripts, skill manager
  skill-library/    # per-discipline, per-role SKILL.md injected per phase
  agents/           # agent identity docs deployed into OpenClaw workspaces
  plugin/           # autodev-pipeline-signals OpenClaw plugin (TS → esbuild bundle)
  config/           # skill mapping, MCP + session setup
  docs/             # PIPELINE-SPEC, PIPELINE-CONSTRAINTS, PRD, assumptions
ui/                 # FastAPI server + single-file React dashboard (no build step)
tests/              # UI server tests
install.sh          # interactive installer
```

---

## Documentation

| Doc | What it covers |
|---|---|
| [SETUP.md](SETUP.md) | Full install, openclaw.json requirements, silent-failure modes, cost metrics |
| [GLOSSARY.md](GLOSSARY.md) | Dashboard terminology (pipeline/queue states, skills, metrics) |
| [CLAUDE.md](CLAUDE.md) | Complete contributor orientation and architecture deep-dive |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev setup, PR conventions, adding skills |
| [SECURITY.md](SECURITY.md) | Security model and vulnerability reporting |
| `autodev/docs/PIPELINE-SPEC.md` | The architecture spec / single source of truth |

---

## License

[MIT](LICENSE) © 2026 Lullabeast contributors.
