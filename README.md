# AutoDev

**Bring an idea. Leave with a working MVP.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20WSL2-lightgrey.svg)
![Status](https://img.shields.io/badge/status-MVP-orange.svg)

AutoDev is an autonomous, multi-agent software-development pipeline. You describe what you
want to build; AutoDev helps you shape that idea into structured product documentation, then
drives a team of LLM agents — **planner → executor → reviewer** — to implement it phase by
phase against a real git repository. When the agents get stuck (and on a hard enough project,
they will), AutoDev **escalates to you** with the context to unblock the run and resume it.

The goal is simple: **come to us with an idea, and by the end we aim to hand you a functional
MVP** — with a clear, honest trail of what was built, what passed review, and where a human
had to step in.

It ships as a single-process **FastAPI dashboard** plus the orchestration pipeline. AutoDev is
**model-agnostic**: it runs on top of [OpenClaw](https://docs.openclaw.ai), which provides the
agent gateway and owns all model/provider configuration.

> **Status — MVP / operator tooling.** AutoDev aims for a working MVP — a prototype you'd refine
> and harden before shipping. It runs as a **single-user tool on a trusted machine**: the API has
> **no authentication**, so bind it to loopback. Autonomous runs can and do fail, which is exactly
> why the escalation/recovery loop is a first-class part of the design.

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

1. **Ideate.** In **Project Ideas**, you chat with a PRD-creator agent that refines a raw idea
   into a structured **`prd.md`** (what to build, and why).
2. **Convert.** AutoDev turns the PRD into a phased **`roadmap.md`** (how the work is broken into
   phases, each with a behavioral-verification block) and a **`verification.md`** contract (the
   project's entry point, public surface, and acceptance stack).
3. **Build.** The orchestrator runs a deterministic, gate-checked loop for each phase:
   - **Planner** turns the phase into a concrete implementation plan.
   - **Executor** writes the code and tests, then commits.
   - **Reviewer** verifies the behavior actually works (including screenshot-based visual review
     for UI phases).
   - Deterministic **gate scripts** validate every agent's output before the pipeline advances —
     no LLM is trusted to grade its own homework.
4. **Recover.** If a phase can't pass after its retry budget, AutoDev **escalates**: the run pauses,
   you get the failure context, you answer (proceed / reset the phase / skip / stop), and the run
   resumes from where it stopped.
5. **Done.** On completion the dashboard shows a **Pipeline Complete** summary — per-phase attempts,
   review verdicts, and cost (when your OpenClaw models report it).

Queue several projects and AutoDev works them in order, honoring dependencies between them.

---

## Architecture

**Multi-agent, gate-based.** Each phase moves through specialised agents: the **planner** turns the
phase into a concrete plan, the **executor** writes code and tests and commits, and the **reviewer**
checks that the result actually behaves as intended. Between every handoff sits a deterministic
**gate script** — a plain, LLM-free Python checker that inspects what the agent produced (its file
manifest, the git diff, test results, behavioral evidence, unaccounted deletions) and returns a
verdict: pass, retry, or escalate. The gates are the pipeline's source of truth, so a
confident-but-wrong agent can't advance on its own say-so; a failing phase loops back through a
fresh attempt until it passes or the retry budget runs out. A fourth agent, **escalation**, is
invoked only once the gates and retries are exhausted, and two ideation agents (**prd-creator**,
**roadmap-converter**) drive the idea → PRD → roadmap front-end. A single orchestrator state machine
sequences all of them and owns the git operations, blame attribution, and recovery logic.

**OpenClaw is the runtime.** AutoDev runs on top of [OpenClaw](https://docs.openclaw.ai), which hosts
the agent sessions and brokers every model call. AutoDev invokes agents through OpenClaw's
`/hooks/agent` webhook and reads the session files OpenClaw writes back. **All model and provider
configuration — including any API keys — lives in `openclaw.json`**, so AutoDev itself stays
model-agnostic and never handles a provider credential. You install OpenClaw and point AutoDev at it
(see [SETUP.md](SETUP.md)).

**Two state trees.** Pipeline state — the lock, queue, event log, ideas, and the active-project
symlink — defaults to **`<repo>/.autodev/`**; OpenClaw's own config and agent workspaces live under
**`~/.openclaw`**.

**Single-file by design.** `ui/server.py` (all API routes) and `autodev/pipeline/orchestrator.py`
(the whole state machine) are intentionally monolithic, keeping the control flow auditable in one
place. See [CLAUDE.md](CLAUDE.md) before refactoring either.

---

## Quick start

**Prerequisites**

- Linux, macOS, or WSL2 (native Windows is unsupported — the pipeline uses POSIX `fcntl` locking)
- Python 3.9+ and `git`
- Node.js 22+ with `npm` (builds the signals plugin and the Playwright visual-review MCP)
- A separate, running **OpenClaw** gateway on `localhost:18789` ([install OpenClaw](https://openclaw.dev/install))

**Install & run**

```bash
git clone <this-repo> autodev-ui
cd autodev-ui
./install.sh            # interactive, 14 steps; safe to re-run
source .env
uvicorn ui.server:app --host 127.0.0.1 --port 18790
```

Then open **http://127.0.0.1:18790**.

**Verify webhooks once** (a GET check can miss token mismatches — use POST):

```bash
curl -sS -o /dev/null -w "HTTP %{http_code}\n" -X POST http://127.0.0.1:18789/hooks/agent \
  -H "Authorization: Bearer <hooks.token>" -H "Content-Type: application/json" \
  -d '{"agentId":"prd-creator","sessionKey":"ideas:install-check:0","wakeMode":"now","message":"ping"}'
```

`HTTP 200` means you're wired up. `HTTP 401` means the Bearer token doesn't match `hooks.token` in
`openclaw.json`. Full walkthrough — including macOS LaunchAgent and Linux/WSL2 systemd units — is in
**[SETUP.md](SETUP.md)**.

---

## The dashboard

<!-- Add a screenshot or short GIF of the dashboard here before release. -->

- **Project Ideas** — chat an idea into a PRD, then generate the roadmap + verification contract.
- **Setup & Preflight** — point at a project repo, run preflight checks, and launch the pipeline.
- **Pipeline Monitor** — watch the live planner → executor → reviewer loop, per-phase metrics, and a
  real-time activity feed; recover from git errors or answer escalations.
- **Queue** — line up multiple projects with dependency ordering; AutoDev runs them sequentially.

---

## Security

- **`/api/*` has no authentication.** Anyone who can reach the bound port can launch runs and read
  project files. Bind to **`127.0.0.1`** (the default). Only use `--host 0.0.0.0` on a trusted LAN
  behind a firewall, and never expose the raw port to the internet without a reverse proxy + TLS +
  auth. See [SECURITY.md](SECURITY.md) and [SETUP.md — Security and network exposure](SETUP.md#security-and-network-exposure).
- The pipeline **executes agent-written code on the host** under your user account. Treat AutoDev as
  operator tooling for a trusted machine, not a multi-tenant service.
- Secrets (the webhook Bearer token) live in `.env` (gitignored) or `AUTODEV_HOOKS_TOKEN`. Never
  commit them in `ui/config.json` or any tracked file.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| UI says `RUNNING` but no agents ever fire | OpenClaw gateway is down | `curl -s http://localhost:18789/v1/models` — connection refused means start the gateway |
| Webhook returns **401** | `hooks.token` ≠ `AUTODEV_HOOKS_TOKEN` | Sync the Bearer secret (install.sh step 8 does this) |
| `orchestrator.py not found` / `No module named …` on launch | `.env` not sourced / `AUTODEV_REPO_PATH` wrong | `source .env` before starting uvicorn |
| Every **UI/INT** phase fails at the reviewer | Playwright MCP not installed | Re-run `./install.sh` without `--skip-playwright` |
| PRD → roadmap **convert** errors | conversion prompt path overridden to a missing file | Leave `conversion_prompt_path` empty in `ui/config.json` to use the bundled prompt |
| Header shows **Queue stalled** | all queued projects are blocked / in dependency hold | Clear a parent or resume a banked escalation answer |

A deeper **"Silent failure modes"** walkthrough lives in [SETUP.md](SETUP.md#silent-failure-modes-four-cases).

---

## Tests

Two suites; both must pass before a change merges.

```bash
source .env
pytest autodev/tests/ -q     # pipeline: orchestration, sentinel polling, skill injection (no live OpenClaw)
pytest tests/ -q             # UI server: FastAPI routes + frontend
```

Dev dependencies (`pytest`, `httpx`) are in [`requirements-dev.txt`](requirements-dev.txt).

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
install.sh          # 14-step interactive installer
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

## Maintainer notes

- **Pin runtime deps** in [requirements.txt](requirements.txt) / [ui/requirements.txt](ui/requirements.txt);
  run `pip-audit -r requirements.txt` periodically and upgrade pins after review.
- **`install.sh` is idempotent** (`set -euo pipefail`, atomic JSON patches) and safe to re-run after a
  `git pull`; it audits the OpenClaw `hooks` block, registers pipeline agents, and rebuilds the plugin.
- **Before a public release**, run a secret scanner over full git history (e.g.
  [gitleaks](https://github.com/gitleaks/gitleaks) / [trufflehog](https://github.com/trufflesecurity/trufflehog));
  rotate anything real that was ever committed and consider `git filter-repo`.

---

## License

[MIT](LICENSE) © 2026 AutoDev contributors.
