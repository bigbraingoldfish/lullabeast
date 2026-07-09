# Lullabeast container deploy

One container runs everything: the OpenClaw gateway, the Lullabeast dashboard,
and the two maintenance loops. `docker compose up` yields a running system with
agents registered, the signals plugin loaded, a green doctor, and the dashboard
URL (with its access token) printed in the boot log. A provider API key is not
required at boot: if none is set, the container boots into setup mode and the
dashboard collects the key. If you put a key in `.env`, that headless path is
unchanged and takes precedence.

## OpenClaw redistribution and the version pin

OpenClaw is MIT-licensed (verified against the `openclaw` npm package,
version 2026.6.11: `npm view openclaw license` reports MIT, and the package
ships the MIT license text). Redistribution in a published image is therefore
permitted, and the default image bakes the pinned version at build via
`npm install -g openclaw@<pin>` (the same official npm install path a host
install uses). The decision is reversible: build with
`--build-arg OPENCLAW_VERSION=` (empty) and the image ships without OpenClaw;
the entrypoint then installs the same pin into the `/data` volume on first
boot.

The pin starts at **2026.6.11** (verified working end to end). Never float
`latest`; bump the pin deliberately and re-run the doctor's version check,
which knows the documented floor (2026.5.18) and the known-bad releases.

## Quickstart

```bash
git clone <this repo> && cd <repo>/deploy
cp .env.example .env        # optional: set OPENROUTER_API_KEY, or enter it in the dashboard later
mkdir -p projects
docker compose up -d
docker compose logs -f
```

The container runs detached (`-d`) so it survives closing the terminal; the
`logs -f` view is a window into it, and Ctrl+C there detaches without
stopping the container (a foreground `docker compose up` stops the whole
container on Ctrl+C or when the shell closes). Wait for the boot log to end
with the doctor verdict and a banner containing the dashboard URL, then open
that URL (it includes the access token) on the machine running docker. First boot is slower: it provisions `/data`, runs the
owned-mode installer, and performs a one-time live webhook ping that creates
one tiny agent session to validate your API key end to end.

### First boot without a key (setup mode)

You do not have to edit `.env` before the first boot. With no provider set (no
`OPENROUTER_API_KEY` and no `LOCAL_MODEL_URL`), `docker compose up` boots
into setup mode: it provisions everything except agent capability, prints a
loud SETUP MODE banner with the dashboard URL, and waits. Open the dashboard,
enter your provider key in the setup screen, and the container wires it,
restarts the gateway, validates it with the one-time live webhook ping, and
unlocks the pipeline automatically. No terminal, no file editing.

A key in `deploy/.env` still works exactly as before and takes precedence: if
it is set, the container boots straight to a running system and never enters
setup mode. The persisted key from the dashboard lives at
`/data/secrets/provider.env` (mode 600, never logged), so it survives
container recreation.

The setup screen also offers a third path: **skip model setup** and manage
models and providers by hand in OpenClaw. It is confirmed via a modal because
it is one-way (the welcome screen never reappears; the Settings screen keeps
the OpenClaw gateway link). The skip persists as a
`PROVIDER_SETUP_SKIPPED=1` line in the same provider file; agents cannot run
until a provider actually exists in OpenClaw, and the doctor's
`provider_key` check keeps saying so. The one-time live validation ping stays
unspent, so a real key added later still gets it.

A local model server is an alternative to a cloud key: either set
`LOCAL_MODEL_URL` in `deploy/.env` (which satisfies the provider gate on its
own, no cloud key needed) or, on a keyless boot, use the setup screen's
detected-server card to wire a server running on the host. See "Local models
on the host" below.

Spend warning: agent pipelines are token-hungry. Cache reads dominate and
bill at a fraction of fresh input, but bills are real; watch the Monitor's
cost strip on your first runs.

For your first pipeline run, the repo bundles a known-good sample project
([examples/first-run-snake](../examples/first-run-snake), a tiny single-file
Snake game): copy it into `./projects/` and follow "Your first run" in the
[main README](../README.md).

### Windows notes

- Install **Docker Desktop** and make sure it is running (steady whale icon)
  before `docker compose up`.
- Install **Git for Windows** and run the quickstart in its **Git Bash**
  shell, not PowerShell or Command Prompt, so `cp` and `mkdir -p` work
  verbatim.
- If scripts fail on a stray `\r`, or the port bind fails with nothing in
  netstat, see the CRLF and reserved-port items under "Troubleshooting first
  boot" below.

## Troubleshooting first boot

- **`Bind for 0.0.0.0:18790 failed: port is already allocated` (or similar
  "ports are not available ... 18790").** Another process on the host already
  holds the dashboard port. Set `UI_PORT` to a free port in `deploy/.env` and
  re-run. Note that `docker compose up -d` can swallow this error and the
  container looks like it started; if the dashboard never answers, run a
  one-off foreground `docker compose up` to see the bind error in the boot
  log. On Windows, Hyper-V and WSL2 reserve blocks of ports that
  produce this bind error while nothing shows up in `netstat`. List the
  reserved ranges with `netsh interface ipv4 show excludedportrange
  protocol=tcp` from an elevated (Run as administrator) prompt; the fix is the
  same `UI_PORT` change to a port outside those ranges.
- **Pulling `ghcr.io/bigbraingoldfish/lullabeast` is denied.** There is no
  published image to pull yet: images are published only from the first tagged
  release onward (see "CI, published images, and OFFLINE mode" below for the
  `v*` tag publish). You do not need one. The compose default builds the image
  locally as `lullabeast:local` and never touches the registry, so the
  quickstart works with no pull.
- **Windows: `bash\r: No such file or directory` (or scripts failing on a
  `\r`).** Windows git checked the shell scripts out with CRLF line endings,
  which the container's Linux shell cannot run. The repo's `.gitattributes`
  forces LF for the affected files, so a fresh clone is correct. If you cloned
  before that fix existed, re-clone the repo (or delete and re-checkout the
  deploy scripts) so git rewrites them with LF.
- **A model fails with "404 No endpoints found" (or another provider
  rejection) on the first run.** Model availability on OpenRouter shifts over
  time, so a shipped default can stop resolving for a given key. Override that
  role's `*_MODEL` variable in `deploy/.env` with a model your key can reach
  and restart the container. The defaults are chosen from models verified
  working, and the per-agent variables are listed in the environment contract
  table below.

## Environment contract (`deploy/.env`)

Every variable is documented inline in [.env.example](.env.example).

| Variable | Required | Meaning |
|----------|----------|---------|
| `OPENROUTER_API_KEY` | no (optional) | Provider key for the agents' models; the golden path, since the shipped model defaults are OpenRouter models. Optional at boot: with no provider key set, the container boots into setup mode and the dashboard collects the key (see "First boot without a key" above). A key here takes precedence and skips setup mode. |
| `LOCAL_MODEL_URL` | no (optional) | Points the container at a model server on the docker host (for example `http://host.docker.internal:11434`). Setting it satisfies the boot's provider gate exactly like a cloud key and auto-wires a local provider. See "Local models on the host" below. |
| `PLANNER_MODEL`, `EXECUTOR_MODEL`, `REVIEWER_MODEL`, `PRD_MODEL`, `ROADMAP_MODEL`, `ESCALATION_MODEL` | no | Per-agent model overrides; audited defaults in [CONFIG-AUDIT.md](CONFIG-AUDIT.md). Executor and reviewer picks must accept image input. |
| `UI_PORT` | no | Dashboard port, default 18790, published to the host loopback only. |
| `GIT_USER_NAME`, `GIT_USER_EMAIL` | no | Identity for the commits the pipeline makes inside project repos. |

## Local models on the host

The container does not run models, but it can talk to a model server running
on the docker host. From inside the container, `host.docker.internal`
resolves to the host (wired via `extra_hosts` in the compose file), so a
llama.cpp `llama-server`, llama-swap, Ollama, or LM Studio instance on the
host is reachable with no compose changes. Pointing `LOCAL_MODEL_URL` at it
satisfies the boot's provider gate, so a local-only install needs no cloud
key. To wire it up:

1. **Run the model server on the host**, listening on an interface the
   docker bridge can reach. `host.docker.internal` points at the host's
   bridge address, not the host's loopback, so a server bound only to
   `127.0.0.1` is invisible to the container. Bind it to `0.0.0.0` (or the
   docker bridge interface) and firewall it from everything else.
2. **Set `LOCAL_MODEL_URL` in `deploy/.env`** (for example
   `LOCAL_MODEL_URL=http://host.docker.internal:11434`), then start the
   container. At boot it normalizes the URL, probes `<url>/v1/models` plus the
   server's own metadata endpoint (Ollama `/api/show`, llama.cpp `/props`,
   LM Studio `/api/v0/models`), and auto-generates the
   `models.providers.local` entry in `openclaw.json` with the mandatory
   `apiKey: "no-key"`. Each model entry is written complete, not bare: the
   probed context window, reasoning, and vision support where the server
   reports them, and always a working `maxTokens` (half the context window,
   capped at 16384; never OpenClaw's 8192 fallback, which truncates real
   pipeline turns). The boot log prints each model in the `local/<model-id>`
   form with the values it was wired with. Same-id fields already in the
   entry survive restarts, so a hand-tuned model is never regressed by a
   reboot. Detection requires the server to answer `/v1/models`.
3. **Confirm what the probe cannot know.** llama.cpp and LM Studio do not
   report whether a model is a reasoning model; an undeclared reasoning model
   burns its output budget thinking and ends its turn with nothing. If yours
   is one (Qwen3.x, DeepSeek-R1 and kin), set `LOCAL_MODEL_REASONING=1` in
   `deploy/.env` (`0` pins it off). `LOCAL_MODEL_MAX_TOKENS` (output budget),
   `LOCAL_MODEL_CONTEXT_WINDOW` (also re-derives the budget when MAX_TOKENS
   is unset), and `LOCAL_MODEL_VISION` (image input; the executor and
   reviewer require a multimodal model, so unprobed models default to
   text+image) work the same way. All apply to the `*_MODEL`-assigned local
   models and win over probed values and hand-edits. The doctor's
   `local_model_completeness` check warns while any role-assigned local model
   entry is missing `maxTokens`, `contextWindow`, `input`, or `reasoning`.
4. **`"apiKey": "no-key"` is mandatory on every local provider entry.** The
   `LOCAL_MODEL_URL` path sets it for you; the advanced hand-edit path below
   makes it your responsibility. Without it OpenClaw inherits the cloud auth
   profile and silently falls back to a cloud model, with no error shown; the
   only signal is `fallbackNoticeReason: auth` in the agent's `sessions.json`.
   This is the classic local-model silent failure.
5. **Point roles at the local models through the `*_MODEL` variables** in
   `deploy/.env` (for example `ESCALATION_MODEL=local/qwen3.5-27b`, where
   `local` is the provider name `LOCAL_MODEL_URL` generates and
   `qwen3.5-27b` is one of the discovered model ids from the boot log), then
   restart the container. Do not edit an agent's `model.primary` in the file:
   it is a template-pinned key and the per-boot reconcile reverts it. The
   executor and reviewer must stay on models that accept image input (the
   reviewer does screenshot-based visual review on UI phases). The doctor's
   `model_modality` check enforces this at boot: a confirmed text-only
   reviewer fails the boot with the offending `*_MODEL` named, instead of
   surfacing as HTTP 400s at the last phase of a run.
6. **Raise the reviewer's infrastructure backstop** for slow local
   reviewers: add `AUTODEV_INFRA_BACKSTOP_REVIEWER=10800` to `deploy/.env`.
   Compose passes it into the container environment and the orchestrator
   inherits it there. A thorough long-context reviewer pass on local
   hardware can need minutes per model call, which the default 75 minute
   backstop misreads as a dead gateway.

**Boot without a key: the setup screen auto-detects local servers.** On a
keyless boot (setup mode, see "First boot without a key" above), the
container best-effort probes `host.docker.internal` on the known ports
(Ollama 11434, llama.cpp 8080, LM Studio 1234) and the dashboard setup screen
shows any detected server with one-click wiring. Detection still requires the
server to answer `/v1/models`. One click picks a single model for all roles,
with the max output tokens, context window, reasoning, and image-input fields
prefilled from the probe for you to confirm (they persist as the
`LOCAL_MODEL_*` overrides from step 3). Answering No on image input blocks
the wiring with a warning: the executor and reviewer require a multimodal
model, and flipping it back to Yes on a model you know better than the probe
is your call. Per-role tuning stays in `deploy/.env` through the `*_MODEL`
variables. If no server answers, re-check the bind requirement in step 1: a
`127.0.0.1`-bound server is invisible to the container.

**Advanced: hand-edit `openclaw.json` for multiple providers or per-model
metadata.** `LOCAL_MODEL_URL` covers the single-provider case. For multiple
local providers, custom base URLs, or per-model pricing, add a provider entry
by hand under `models.providers` in `/data/openclaw/openclaw.json`, next to
the shipped `openrouter` entry: `"baseUrl":
"http://host.docker.internal:<port>/v1"`, your model entries, and
`"apiKey": "no-key"` (mandatory, see step 4). The golden template does not
declare your provider's key, so the edit survives boots (the customization
contract below). Reference the models as `<your-provider-name>/<model-id>` in
the `*_MODEL` variables.

Local models have no shipped pricing entries, so their runs report $0 in the
dashboard (see the cost section below; that is correct, not a bug). The
model-side notes in [SETUP.md](../SETUP.md) under "Running with local models"
apply unchanged in the container: Qwen think-token suppression flags, which
roles do well locally, and the quality trade-offs. This section only adds the
container networking on top of them.

### Local-model non-goals for this release

GPU passthrough into the container, model weights in docker volumes, and an
all-local turnkey compose file are explicitly out of scope for this release;
do not burn time trying to make this sandbox do them. The host bridge above
is the supported local-model path. If you want the model server itself
containerized, run and manage it as your own separate container that
publishes its port on the host; the bridge instructions then apply
unchanged.

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

## CI, published images, and OFFLINE mode

The GitHub Actions workflow
[.github/workflows/deploy-image.yml](../.github/workflows/deploy-image.yml)
keeps this deploy path from rotting: every PR or push that touches
`deploy/`, `install.sh`, the requirements files, the signals plugin, or the
installer modules rebuilds both image variants (baked and no-bake) and
smoke-tests the baked one. The smoke boots the container with `OFFLINE=1`,
waits for the supervisor, then runs the doctor with `--json` inside the
container and asserts the result via [smoke_assert.py](smoke_assert.py): no
check may fail, every keyless-runnable check must be green, and the live-only
`webhook_ping` must report skipped.

`OFFLINE=1` is the entrypoint's CI/smoke mode: it skips the provider API key
requirement and the one-time billable `--live` doctor probe (leaving the
first-boot marker unwritten, so a later real boot still performs its one live
ping). Everything else provisions for real. Agents cannot run without a key;
the boot log prints a loud banner saying so. Never use it for a real
deployment.

On version tags (`v*`) the workflow publishes the baked image to
`ghcr.io/bigbraingoldfish/lullabeast:<tag>` and `:latest` (OpenClaw is MIT
licensed, so publishing with it baked is permitted; see the redistribution
note
above). To use a published image instead of building locally, replace the
service's `build:` block and `image:` line in
[docker-compose.yml](docker-compose.yml) with:

```yaml
    image: ghcr.io/bigbraingoldfish/lullabeast:latest
```

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
- **18789** (OpenClaw gateway): published to `127.0.0.1` on the host only,
  token-authenticated. Model and provider management and the one-time
  gate-script approvals happen in OpenClaw's own UI, so the dashboard's
  Settings screen links here. Same rule as the dashboard: exposing it beyond
  loopback is a conscious edit.
- `host.docker.internal` resolves to the docker host (via `extra_hosts`), so
  a model server running on the host is reachable from inside the container
  (see "Local models on the host" above).

## Looking at the OpenClaw gateway

The gateway runs inside the container on 18789 and is published to the host's
loopback, so OpenClaw's own UI is at `http://127.0.0.1:18789`. That is where
model and provider management lives, and where you approve the pipeline's gate
scripts the first time they run. The dashboard's **Settings** screen opens it
signed in: the button carries the gateway token in the URL hash fragment,
which stays in the browser (never sent in the request, absent from server
logs). The golden config already allow-lists the loopback origin, so there is
no origin prompt on the default port. If you prefer the shell, the token is
at:

```bash
docker compose exec lullabeast cat /data/secrets/gateway_token
```

Two shell-side ways to inspect it:

```bash
docker compose exec lullabeast curl -s http://localhost:18789/v1/models
docker compose exec lullabeast bash
```

The first lists the models the gateway can serve; the second drops you into an
interactive shell inside the container to poke around.

The gateway is the agents' control plane, so the same exposure rule as the
dashboard applies: it is published to `127.0.0.1` only, and widening that is a
conscious edit. See the "Ports and exposure" bullets above.

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
excluded from the image build context), in the container environment, and (when
you enter it in the dashboard setup screen instead of `.env`) in the persisted
key file `/data/secrets/provider.env` (mode 600, never logged). Generated
tokens persist under `/data/secrets` (directory mode 700, files 600). Neither the entrypoint nor install.sh ever prints the API key or the
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
  supervised; the UI server spawns it per run. With no provider key it boots
  into setup mode and runs a watch loop that unlocks the pipeline once the
  dashboard supplies the key.
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
- [smoke_assert.py](smoke_assert.py): the CI assertion script; validates
  the doctor's `--json` report from an `OFFLINE=1` smoke boot (run by
  [.github/workflows/deploy-image.yml](../.github/workflows/deploy-image.yml)).
