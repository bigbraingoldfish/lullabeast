<p align="center">
  <img src="docs/assets/branding/banner-twilight.png" alt="Lullabeast" width="100%">
</p>

<div align="center">

[![Website: lullabeast.ai](https://img.shields.io/badge/website-lullabeast.ai-7c3aed.svg)](https://lullabeast.ai)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/bigbraingoldfish/lullabeast/actions/workflows/ci.yml/badge.svg)](https://github.com/bigbraingoldfish/lullabeast/actions/workflows/ci.yml)
[![Runs on OpenClaw](https://img.shields.io/badge/runs%20on-OpenClaw-c9962e.svg)](https://docs.openclaw.ai)
![Status](https://img.shields.io/badge/status-beta-orange.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20WSL2-lightgrey.svg)

</div>

Lullabeast is an open-source, local-capable, autonomous development pipeline. Describe what you want to build in plain English, and your team of agents (planner, executor, reviewer) implements it phase by phase against a real git repository, with deterministic gate scripts checking every step and an escalation path back to you when they get stuck.

Lullabeast runs on [OpenClaw](https://docs.openclaw.ai/) and requires it: Lullabeast is the pipeline and dashboard, while the agents themselves run inside OpenClaw's runtime environment. The Docker image bundles a pinned, pre-configured OpenClaw, so the install path below needs nothing but Docker and an API key.

<p align="center">
  <img src="docs/assets/diagrams/demo-cycle.gif" alt="One full Lullabeast run, from idea to finished app" width="720">
</p>

> **Early release, and honest about it.** Lullabeast reliably builds small, single-purpose webapps end to end, and hard phases escalate to you by design, but larger or more complex projects tend to surface more issues and need more polish before they're done. This beta release is a single-user tool meant to run on a trusted machine, protected by a locally generated access token. (I personally run the pipeline and OpenClaw in a VM for transparency.) I'm shipping now to get it in front of other builders and find out where it breaks so I can strengthen it. Bug reports and suggestions are welcome.

---

## Why it exists

Open-weight models like Qwen, Kimi, and MiniMax cost a fraction of frontier prices, and they fail in known, repeatable ways. Lullabeast is built around those failures:

| The failure mode | How Lullabeast handles it |
|---|---|
| **Deleting files** under context pressure | Every deletion has to be declared up front. If a file disappears that wasn't, the gate flags it and the phase re-runs. |
| **Drifting from the spec** as a build grows | Context is cleared between tasks and each one stays atomic, so focus stays narrow against a clear definition of done; the reviewer then confirms those principles held across the whole phase. |
| **Declaring victory** without running the tests | A phase can't close until the gate confirms every required test exists, is structured correctly, and passes. |
| **Disappearing mid-turn** and leaving you guessing | Each agent gets three real attempts before anything is flagged to you, and timeouts catch an agent that never starts or hangs, so one dropped turn never sinks the run. |

---

## How it works

<p align="center">
  <img src="docs/assets/diagrams/workflow.png" alt="Lullabeast workflow: idea to PRD to roadmap, then the gated planner/executor/reviewer build loop with escalation, to a finished app" width="760">
</p>

Queue several projects and Lullabeast works them in order, honoring dependencies between them.

**The agents.** Four pipeline agents and two ideation agents, run by a single orchestrator state machine that owns the git operations and recovery logic:

- **Planner:** turns the current roadmap phase into a concrete implementation plan.
- **Executor:** writes the code and tests, then commits to a phase branch.
- **Reviewer:** verifies the result actually behaves as intended, including screenshot-based visual review for UI phases.
- **Gate scripts:** deterministic, LLM-free Python checkers between every handoff: file manifest, git diff, test results, behavioral evidence, unaccounted deletions. The gates are the pipeline's source of truth; no agent advances on its own say-so.
- **Escalation:** invoked only when gates and retries are exhausted; notifies you and pauses.
- **prd-creator / roadmap-converter:** drive the idea, PRD, roadmap front end.

---

## Examples

Lullabeast works best for small, focused webapps. Each one below was built end to end by the pipeline.

### Flagship: SVG Pictionary

<p align="center">
  <img src="docs/assets/demos/pictionary.webp" alt="SVG Pictionary, a multi-screen AI drawing-and-guessing game with multiple AI players and a human, round-based" width="720">
</p>

Multiple AI players and a human in a round-based draw-and-guess game over real SVG: multi-screen routing, persistent per-round state, and live, simultaneous LLM API calls that render elements both the models and the player act on. The hardest target on this list, an application rather than a widget.

| GridBeast | 2048 | Regex Tester |
| :---: | :---: | :---: |
| ![GridBeast](docs/assets/demos/gridbeast.png) | ![2048](docs/assets/demos/2048.gif) | ![Regex Tester](docs/assets/demos/regex.png) |

| Conway (classic) | Conway (conquest) |
| :---: | :---: |
| <img src="docs/assets/demos/conway-classic.webp" alt="Conway classic mode" width="400"> | <img src="docs/assets/demos/conway-conquest.webp" alt="Conway conquest mode" width="400"> |

Every example links the exact PRD and phased roadmap that drove its build:

| Project | PRD | Roadmap | What it is |
|---|---|---|---|
| SVG Pictionary | [PRD](examples/svgbeast/PRD.md) | [Roadmap](examples/svgbeast/roadmap.md) | Flagship: multi-screen, persistent state, live simultaneous LLM API calls |
| GridBeast | [PRD](examples/gridbeast/PRD.md) | [Roadmap](examples/gridbeast/roadmap.md) | Mini spreadsheet; formula engine with precedence, ranges, cycle detection |
| Regex Tester | [PRD](examples/regex/PRD.md) | [Roadmap](examples/regex/roadmap.md) | Live matcher; inline flags, in-place highlighting, light/dark |
| 2048 | [PRD](examples/2048-api/PRD.md) | [Roadmap](examples/2048-api/roadmap.md) | Tile-merge game; correct merge semantics, score/best, spawn-on-move |
| MultiLife (Conway) | [PRD](examples/game-of-life/PRD.md) | [Roadmap](examples/game-of-life/roadmap.md) | Two rule systems (classic + conquest) over one grid engine |

*GridBeast's functionality was manually verified, before I generated the self-test panel using the same foumulas in a follow-up pass to surface correctness at a glance.*

**Built with.** No closed frontier models anywhere in the loop, just local and open-weight cloud:

| Project | Planner | Executor | Reviewer |
|---|---|---|---|
| MultiLife (Conway) | `llamacpp/Qwen3.6-27B-MTP` | `llamacpp/Qwen3.6-27B-MTP` | `llamacpp/Qwen3.6-27B` |
| Regex Tester | `llamacpp/Qwen3.6-27B-MTP` | `llamacpp/Qwen3.6-27B-MTP` | `llamacpp/Qwen3.6-27B` |
| GridBeast | `llamacpp/Qwen3.6-27B` | `llamacpp/Qwen3.6-27B` | `llamacpp/Qwen3.6-27B` |
| 2048 | `openrouter/z-ai/glm-5.2` | `openrouter/moonshotai/kimi-k2.7-code` | `openrouter/moonshotai/kimi-k2.7-code` |
| SVG Pictionary | `openrouter/z-ai/glm-5.2` | `openrouter/moonshotai/kimi-k2.7-code` | `openrouter/moonshotai/kimi-k2.7-code` |

**Play it live.** *MultiLife (Conway)* was built twice: once locally with Qwen & again on cloud open-weight models. Both finished builds are **[embedded and playable side by side](https://lullabeast.ai/living-proof)** in your browser.

---

## Run it your way

Lullabeast is model-agnostic. OpenClaw owns all model configuration, so you choose the cost/quality trade-off:

| Mode | What runs the agents | Trade-off |
|---|---|---|
| **Budget cloud** (best results so far) | Open-weight multi-modal models via your OpenRouter key (e.g. MiniMax, GLM, Kimi, Qwen) | Cheap per token; your key, your provider |
| **Fully local** | Validated on a single RTX 4090 (48GB, modded) with `unsloth/Qwen3.6-27B-MTP-GGUF` & `llamacpp/Qwen3.6-27B` (both q8_0); [wiring guide](deploy/README.md#local-models-on-the-host) | No cloud in the loop; front-end (UI) phases are the weak spot, with the most failures and retries |
| **Hybrid** | Local for **escalation + executor** (where most of the work, and the cost savings, happen), cloud for **planner and/or reviewer** (cheap to build a strong foundation and review thoroughly) | Often the best cost/quality balance; still being tuned |

**Model notes.** A **multi-modal** model is **required** for the **executor** and **reviewer** (the reviewer does screenshot-based visual review for UI phases) and **recommended** for the **planner**. Use the strongest model you're comfortable running for the **roadmap-converter**: it's isolated by design, so your most expensive model is spent only on conversion. I also suggest keeping the **idea-to-PRD chat (`prd-creator`) on a cloud model**, where it produces noticeably better drafts.

**Want the real numbers?** See the **[side-by-side runtime, tokens, and cost](https://lullabeast.ai/#a-build)** for the MultiLife builds.

---

## Quick start

### Requirements

- **Docker** with the Compose plugin, v2.24 or newer (`docker compose version`).
- **A model provider**: an [OpenRouter key](https://openrouter.ai/keys) (the shipped defaults; a few dollars of credit is enough) or a [local model server](deploy/README.md#local-models-on-the-host). No file editing needed: the dashboard asks on first boot.
- **Hardware**: 2 CPU cores, 2-4 GB RAM (Playwright/Chromium is the heaviest resident), and a few GB of disk for the image, state, and generated project repos. No GPU unless you run models locally.

Everything else (OpenClaw, Python, Node, Chromium) ships inside the image, pinned and pre-configured.

### Install & run

```bash
git clone https://github.com/bigbraingoldfish/lullabeast.git && cd lullabeast/deploy
cp .env.example .env    # optional: the dashboard asks for the key if you skip it
mkdir -p projects && docker compose up
```

On Windows, run these lines in Git Bash with Docker Desktop running ([Windows notes](deploy/README.md#windows-notes)).

Wait for the boot log to end with the doctor verdict (the built-in health check) and a banner containing your dashboard URL, then open that URL on the machine running Docker. It includes your access token and authorizes your browser via a cookie (30 days); scripts can send the same token as a `Bearer` header instead. First boot is slower: it provisions state and validates your API key end to end with a one-time live webhook ping.

The full container contract (environment variables, volumes, upgrades, customization, hardening) is in **[deploy/README.md](deploy/README.md)**.

> **Spend warning:** agent pipelines are token-hungry. Cache reads dominate and bill at a fraction of fresh input, but bills are real; watch the Monitor's cost strip on your first runs.

### Your first run

The repo bundles a known-good sample project, a deliberately tiny single-file Snake game ([examples/first-run-snake](examples/first-run-snake)), and the dashboard loads it for you with one click. No terminal, no file authoring:

1. **Open the dashboard** at the tokenized URL from the boot log.
2. **Enter your key if asked.** If you did not put a provider key in `deploy/.env`, the dashboard opens straight into a setup screen and asks for it. Paste an OpenRouter key (there is a direct link to create one; your account needs a small credit balance), submit, and the model gateway restarts and unlocks the pipeline on its own.
3. **Take the welcome tour.** On your first visit a welcome panel explains the demo and shows the Snake project's three documents: the PRD (what to build), the roadmap (the phased plan), and the verification contract (how success is checked). Click **Load the demo project**: the documents are staged like any project idea and you land on Setup & Preflight with the path drafted. Confirm the repository, run preflight, and add it to the queue; the default automatic queue mode starts the build. The tour stops appearing after your first run (replay it from **Settings**).
4. **Watch it build.** The pipeline plans, builds, and reviews the game phase by phase; follow the live loop in the **Pipeline Monitor**.
5. When the run completes, the finished game lands on your host at `projects/first-run-snake/index.html` (the `./projects` folder next to `docker-compose.yml` is bind-mounted into the container); open it in a browser and play.

### Developing Lullabeast

Contributors who want to work on the pipeline itself run bare-metal in development mode against their own OpenClaw install. That walkthrough, including the guest-mode installer, systemd/LaunchAgent units, and non-default ports, lives in **[SETUP.md](SETUP.md)**.

---

## The dashboard

<p align="center">
  <img src="docs/assets/screenshots/pipeline-monitor.png" alt="Pipeline Monitor, the live planner, executor, reviewer loop mid-run" width="720">
  <br><em>The Pipeline Monitor mid-run: live planner, executor, reviewer loop, per-phase metrics, activity feed.</em>
</p>

**Look before you install.** **[Tour the dashboard](https://lullabeast.ai/walkthrough)**. It's a snapshot preview of the interface from the MultiLife (Conway) build.

- **Project Ideas:** chat an idea into a PRD, then generate the roadmap + verification contract.
- **Setup & Preflight:** point at a project repo, run preflight checks, launch the pipeline.
- **Settings:** a **Health** card runs the doctor's full checklist against your install with a fix hint for anything red, and the welcome tour can be replayed from here.
- **Pipeline Monitor:** watch the live planner, executor, reviewer loop, per-phase metrics, and a real-time activity feed; recover from git errors or answer escalations.
- **Queue:** line up multiple projects with dependency ordering; Lullabeast runs them sequentially.
- **Cost & token visibility:** per-phase and per-agent cost/token breakdowns, live during a run and recallable after, in both the Monitor and the Queue (shown when your models report usage).

---

## Security

- **The dashboard and `/api/*` require an access token** (`AUTODEV_UI_TOKEN`, generated by `install.sh`). Open the tokenized URL printed at startup to authorize your browser; scripts send the token as a `Bearer` header. This is single-user, local-tool auth: one shared token, no accounts, roles, or audit trail.
- **Stay on loopback anyway.** Bind to **`127.0.0.1`** (the default); the server refuses non-loopback requests unless a token is configured. Never expose the raw port to the internet; anything beyond a trusted LAN belongs behind a reverse proxy + TLS. See [SECURITY.md](SECURITY.md) and [SETUP.md: Security and network exposure](SETUP.md#security-and-network-exposure).
- The pipeline **executes agent-written code on the host** under your user account. Treat Lullabeast as operator tooling for a trusted machine, not a multi-tenant service.
- Secrets (the dashboard token `AUTODEV_UI_TOKEN` and the webhook Bearer token `AUTODEV_HOOKS_TOKEN`) live in `.env` (gitignored). Never commit them in `ui/config.json` or any tracked file.

---

## Troubleshooting

Start with the doctor: it checks every known silent-failure mode in one pass and prints a fix hint for each red item. It renders as the **Health** card on the dashboard's Settings screen, or run it from a shell:

```bash
docker compose exec lullabeast python -m autodev.installer.doctor
```

Developing bare-metal? The development-install equivalents live in [SETUP.md](SETUP.md).

| Symptom | Likely cause | Fix |
|---|---|---|
| UI says `RUNNING` but no agents ever fire | OpenClaw gateway is down | `docker compose exec lullabeast curl -s http://localhost:18789/v1/models`; connection refused means the gateway is not up |
| Webhook returns **401** | `hooks.token` ≠ `AUTODEV_HOOKS_TOKEN` | Sync the Bearer secret (install.sh step 8 does this) |
| Dashboard or `/api/*` returns **401** | browser not authorized / wrong `AUTODEV_UI_TOKEN` | Open the tokenized URL printed at server startup |
| Header shows **Queue stalled** | all queued projects blocked / in dependency hold | Clear a parent or resume a banked escalation answer |
| `docker compose up` fails with `Bind for 0.0.0.0:18790 failed` / ports are not available | another process, or a Windows reserved port range, holds the dashboard port | Set `UI_PORT` to a free port in `deploy/.env` and re-run (details in [deploy troubleshooting](deploy/README.md#troubleshooting-first-boot)) |

A deeper **"Silent failure modes"** walkthrough lives in [SETUP.md](SETUP.md#silent-failure-modes-four-cases).

---

## Project layout

```
autodev/
  pipeline/         # orchestrator, sentinel poller, gate scripts, skill manager
  skill-library/    # per-discipline, per-role SKILL.md injected per phase
  agents/           # agent identity docs deployed into OpenClaw workspaces
  plugin/           # autodev-pipeline-signals OpenClaw plugin (TS to esbuild bundle)
  config/           # skill mapping, MCP + session setup
  docs/             # PIPELINE-SPEC, PIPELINE-CONSTRAINTS, assumptions
ui/                 # FastAPI server + single-file React dashboard (no build step)
tests/              # UI server tests
deploy/             # Docker image, compose file, golden OpenClaw config (the user install path)
examples/           # demo PRDs/roadmaps + the launchable first-run sample project
install.sh          # provisioning script (run by the container on every boot; guest mode for dev installs)
```

Pipeline state (lock, queue, event log, ideas) lives in `<repo>/.autodev/`; OpenClaw's own config and agent workspaces live under `~/.openclaw`. `ui/server.py` (all API routes) and `autodev/pipeline/orchestrator.py` (the whole state machine) are intentionally single-file to keep control flow auditable; read [CLAUDE.md](CLAUDE.md) before refactoring either. The full spec is [autodev/docs/PIPELINE-SPEC.md](autodev/docs/PIPELINE-SPEC.md).

---

## Documentation

| Doc | What it covers |
|---|---|
| [deploy/README.md](deploy/README.md) | The Docker install: env contract, volumes, upgrades, customization, hardening |
| [SETUP.md](SETUP.md) | Development-mode (bare-metal) setup, openclaw.json requirements, doctor reference, cost metrics |
| [GLOSSARY.md](GLOSSARY.md) | Dashboard terminology (pipeline/queue states, skills, metrics) |
| [CLAUDE.md](CLAUDE.md) | Complete contributor orientation and architecture deep-dive |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev setup, PR conventions, adding skills |
| [SECURITY.md](SECURITY.md) | Security model and vulnerability reporting |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Community expectations for participation (Contributor Covenant) |
| `autodev/docs/PIPELINE-SPEC.md` | The architecture spec / single source of truth |

---

## License

[MIT](LICENSE) © 2026 Lullabeast contributors.
