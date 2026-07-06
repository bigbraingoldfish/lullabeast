# Lullabeast container deploy

One container runs everything: the OpenClaw gateway, the Lullabeast dashboard,
and the two maintenance loops. `docker compose up` with an API key in `.env`
yields a running system with agents registered, the signals plugin loaded, a
green doctor, and the dashboard URL (with its access token) printed in the
boot log.

## OpenClaw redistribution (Task 0 decision, 2026-07-06)

OpenClaw is MIT-licensed (verified against the `openclaw` npm package,
version 2026.6.11: `npm view openclaw license` reports MIT, and the package
ships the MIT license text). Redistribution in a published image is therefore
permitted, and the default image bakes the pinned version at build via
`npm install -g openclaw@<pin>` (the same official npm install path a host
install uses). The decision is reversible: build with
`--build-arg OPENCLAW_VERSION=` (empty) and the image ships without OpenClaw;
the entrypoint then installs the same pin into the `/data` volume on first
boot.

The pin starts at **2026.6.11** (operator-verified working). Never float
`latest`; bump the pin deliberately and re-run the doctor's version check,
which knows the documented floor (2026.5.18) and the known-bad releases.

## Quickstart

```bash
git clone <this repo> && cd <repo>/deploy
cp .env.example .env        # then edit: set OPENROUTER_API_KEY
mkdir -p projects
docker compose up
```

Wait for the boot log to end with the doctor verdict and a banner containing
the dashboard URL, then open that URL (it includes the access token) on the
machine running docker. First boot is slower: it provisions `/data`, runs the
owned-mode installer, and performs a one-time live webhook ping that creates
one tiny agent session to validate your API key end to end.

Spend warning: agent pipelines are token-hungry. Cache reads dominate and
bill at a fraction of fresh input, but bills are real; watch the Monitor's
cost strip on your first runs.

## Environment contract (`deploy/.env`)

Every variable is documented inline in [.env.example](.env.example).

| Variable | Required | Meaning |
|----------|----------|---------|
| `OPENROUTER_API_KEY` or `ANTHROPIC_API_KEY` | one of them | Provider key for the agents' models. Missing both: the container exits immediately, naming the variables. The shipped model defaults use OpenRouter. |
| `PLANNER_MODEL`, `EXECUTOR_MODEL`, `REVIEWER_MODEL`, `PRD_MODEL`, `ROADMAP_MODEL`, `ESCALATION_MODEL` | no | Per-agent model overrides; audited defaults in [CONFIG-AUDIT.md](CONFIG-AUDIT.md). Executor and reviewer picks must accept image input. |
| `UI_PORT` | no | Dashboard port, default 18790, published to the host loopback only. |
| `GIT_USER_NAME`, `GIT_USER_EMAIL` | no | Identity for the commits the pipeline makes inside project repos. |

## Volume layout

| Path in container | Backing | Holds |
|-------------------|---------|-------|
| `/app` | image | The Lullabeast repo (read-mostly; source of truth for agent files, skills, the plugin bundle). |
| `/data` | named volume `lullabeast-data` | ALL state: `/data/openclaw` (the OpenClaw tree: config, workspaces, sessions), `/data/pipeline-state` (pipeline state, queue, events, metrics history), `/data/secrets` (generated tokens). |
| `/data/projects` | bind mount `./projects` | Your projects. Generated code appears here on the host so you can open it with your own tools. |

`/data` must live on a local volume driver: the pipeline's advisory lock uses
`fcntl.flock`, which is unreliable on NFS, so NFS-backed docker volumes are
unsupported for `/data`.

Secrets (`hooks_token`, `gateway_token`, `ui_token`) are generated with
`secrets.token_urlsafe` on first boot and persisted under `/data/secrets`, so
they survive container recreation. The dashboard URL printed at every boot
carries the current UI token.

## Upgrade procedure

```bash
docker compose pull   # or: docker compose build
docker compose up -d
```

State survives in `/data`. Every boot re-runs `install.sh --owned-openclaw`,
so the agent identity files, skills, and plugin bundle from the new image are
automatically re-deployed over `/data`. The persisted `openclaw.json` is
reconciled toward the new image's template on boot, so a template change in an
upgrade (a newly required key, a changed pinned value) is applied automatically
rather than dead-ending at the conformance check; keys the template does not
pin are preserved. Nothing re-provisions from scratch: tokens, sessions, and
pipeline state are kept.

## Customization contract (mounts, not edits)

The installer OWNS the OpenClaw tree inside the container. Hand-edits under
`/data` are overwritten by the next boot's owned-mode install (this is the
contract, not an accident). The supported customization route is mounting
replacement files over the tree, for example:

```yaml
    volumes:
      - ./my-executor-identity.md:/app/autodev/agents/executor/IDENTITY.md:ro
```

Config keys the golden template does **not** declare (an added provider or
model, extra `plugins.allow` entries, and OpenClaw's own runtime bookkeeping)
can be edited in `/data/openclaw/openclaw.json` and survive boots. Any key the
template **does** pin is reconciled back to the template on every boot, so a
direct hand-edit to a pinned key (`tools.profile`, `heartbeat.every`, the
agent models, `gateway.bind`, ...) is reverted. Set the agent models through
the `*_MODEL` variables (which the reconcile re-applies on restart), not by
editing the file. The doctor's `template_conformance` check backs the same
contract.

## Ports and exposure

- **18790** (dashboard): published to `127.0.0.1` on the host only. The UI
  server binds all interfaces inside the container (docker's proxy connects
  with a non-loopback source address), which is safe because access requires
  `AUTODEV_UI_TOKEN` and the compose file publishes to loopback. Exposing the
  dashboard beyond loopback is a conscious edit; read "Security and network
  exposure" in [SETUP.md](../SETUP.md) first.
- **18789** (OpenClaw gateway): container-internal, never published.
- `host.docker.internal` resolves to the docker host (via `extra_hosts`), so
  a model server running on the host is reachable from inside the container
  (the local-model bridge, documented in DS-7).

## Security hardening

The container is the blast-radius boundary for LLM-generated code: the
executor agent runs generated code with exec, and there is a documented model
bug that deletes files. The default posture:

- **Non-root everywhere.** Every process (gateway, dashboard, maintenance
  loops, the orchestrator and the agents it spawns) runs as the unprivileged
  `lullabeast` user. There is no sudo in the image.
- **No capabilities, no privilege escalation.** The compose file ships
  `cap_drop: [ALL]` and `no-new-privileges:true`. Nothing in the container
  needs a capability: all ports are unprivileged, and Playwright launches
  Chromium with Chromium's own sandbox disabled by default, so no
  `SYS_ADMIN` add-back is needed. Any future add-back must be justified by a
  real probe, documented inline in [docker-compose.yml](docker-compose.yml).
- **Lullabeast's code is read-only to the runtime user.** `/app` is
  root-owned, so the orchestrator, gate scripts, UI server, installer, and
  agent identity sources cannot be modified or replaced by anything running
  as `lullabeast`. Three narrow write islands exist because install.sh runs
  on every boot: `.env` and `.autodev/` (created inside the sticky,
  group-writable `/app`), `ui/config.json` (same mechanism in `/app/ui`),
  and `/app/autodev/plugin` (the per-boot signals-plugin rebuild). The
  sticky bit means root-owned entries in those two directories cannot be
  removed or renamed either.
- **Everything else writes to `/data`, `/tmp`, and the home directory.**
  All state lives under `/data`; `/tmp` holds scratch files and Python
  bytecode caches; `/home/lullabeast` holds the baked Python venv and the
  per-boot npm/pip caches.
- **Resource ceilings** (`pids_limit`, `mem_limit`) ship commented in the
  compose file with sizing guidance; uncomment them to keep a runaway
  process tree from starving the host.

### Read-only rootfs: assessed, deliberately off

`read_only: true` (plus tmpfs mounts) was assessed and not shipped:
install.sh is the single provisioning brain and runs on every boot, writing
inside `/app` by contract (the `.env` merge, `ui/config.json`, the plugin
rebuild), and `/home/lullabeast` carries the baked Python venv that a tmpfs
mount would shadow. A read-only rootfs breaks boot outright, and relocating
those writes would fork provisioning logic out of install.sh (a rule this
roadmap treats as load-bearing). The protection it would have bought is
shipped through image file ownership instead: the read-only `/app` described
above.

### Secrets posture

The provider API key exists only in your `deploy/.env` (gitignored, and
excluded from the image build context) and in the container environment.
Generated tokens persist under `/data/secrets` (directory mode 700, files
600). Neither the entrypoint nor install.sh ever prints the API key or the
hooks/gateway tokens. One deliberate exception: the boot banner prints the
dashboard URL including `AUTODEV_UI_TOKEN`, so `docker compose logs` can
always recover dashboard access; treat container logs as sensitive. A dev
tree's local `ui/config.json` (which carries a real webhook token) is
excluded from the build context so it can never be baked into an image.

### What the sandbox does and does not contain

Contained:

- **Filesystem damage.** Generated code writes only to `/data` (its own
  project tree and the OpenClaw state), `/tmp`, and the container home; the
  host filesystem is reachable only through the `./projects` bind mount, and
  Lullabeast's own code and gates are read-only to it.
- **Host process access.** Code runs as an unprivileged user inside the
  container, with no capabilities and no privilege escalation; it cannot see
  or signal host processes.

Not contained (residual risk, documented honestly; out of scope for this
release):

- **Network exfiltration.** Cloud model APIs need the internet, so there is
  no egress lockdown. Generated code can reach the network and could send
  data out, including anything in its project tree or environment.
- **Package-install supply chain.** The executor legitimately installs
  packages (pip, npm) while building projects. A malicious or typosquatted
  dependency executes inside the container with exactly the access described
  above, network included.

If those residual risks are unacceptable for your environment, run the
container on an isolated host or behind your own egress controls; Lullabeast
does not provide them.

## Cost tracking and the $0 case

The golden config ships `models.pricing.enabled: true` and complete pricing
blocks for its 4 recommended OpenRouter models, so runs on the shipped
defaults report real dollar costs in the dashboard. Every other provider or
model is meter-it-yourself: **if a run's cost shows $0, OpenClaw has no
pricing for the model that ran**, not that the run was free. To add pricing
for your own model, follow the walkthrough in [SETUP.md](../SETUP.md) under
"Cost metrics: configuring OpenClaw so Lullabeast can report run cost".

## Contents of this directory

- [Dockerfile](Dockerfile): the single-container image. Base is the official
  Playwright image (Node, Chromium, browser deps, Python 3.12); adds the
  pinned OpenClaw, the Python deps, and the built signals plugin.
- [entrypoint.sh](entrypoint.sh): first-boot provisioning + per-boot config
  reconcile + per-boot owned-mode install + the process supervisor (gateway,
  UI server, heartbeat loop, session-cleanup loop). The orchestrator is not
  supervised; the UI server spawns it per run.
- [docker-compose.yml](docker-compose.yml): the one-service deployment.
- [.env.example](.env.example): the environment contract, every variable
  commented.
- [openclaw.template.json](openclaw.template.json): the canonical OpenClaw
  config; rendered into `/data/openclaw/openclaw.json` on first boot with
  generated secrets and the `*_MODEL` env knobs, and reconciled into it on
  every later boot. The doctor's `template_conformance` check (owned mode)
  flags drift against it.
- [CONFIG-AUDIT.md](CONFIG-AUDIT.md): the key-by-key decision record behind
  the template, including the minimum-hardware statement.
- [EVAL-MIGRATION.md](EVAL-MIGRATION.md): the before/after contract diff for
  the `lullabeast-eval` sister repo (which stays on bare-metal guest mode).
