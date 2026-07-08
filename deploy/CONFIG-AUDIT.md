# OpenClaw golden config: decision record

**What this is.** The rationale behind every key in
[openclaw.template.json](openclaw.template.json), the canonical OpenClaw config
that the container entrypoint renders into `/data/openclaw/openclaw.json` on
first boot and reconciles on every later boot. The template came out of an
end-to-end audit of a working install (OpenClaw 2026.6.11): every key was asked
"is this what a standard user should get, and is it optimal for the pipeline?"
This document records the answers, so a future change to any pinned value can
be weighed against the reason it was pinned.

**Secrets:** the template carries only `${HOOKS_TOKEN}` / `${GATEWAY_TOKEN}`
placeholders; real values are generated at first boot and persisted under
`/data/secrets`. The tests reject secret-shaped strings in the template.

## What ships, and why

| Template key | Value | Why |
|---|---|---|
| `models.mode` | `"merge"` | Custom provider entries merge with OpenClaw's built-ins. |
| `models.pricing.enabled` | `true` | Required for cost tracking (see "Cost tracking" below). |
| `models.providers.openrouter` | standard endpoint, `openai-completions` API | The recommended provider path; the shipped defaults use it. |
| `models.providers.openrouter.models[]` | 4 entries: `qwen/qwen3.6-27b`, `minimax/minimax-m3`, `moonshotai/kimi-k2.7-code`, `z-ai/glm-5.2` | The recommended, cheap, pipeline-capable set, each with a complete 4-field `cost` block so runs report real dollar costs. Three are multimodal because the reviewer does screenshot-based visual review; `z-ai/glm-5.2` is text-only (`input: ["text"]`) and backs the text-only planner/roadmap roles. Premium models (Claude via OpenRouter, etc.) work but are meter-it-yourself. |
| `agents.defaults.model` | `${PLANNER_MODEL}` | A safe fallback for any agent without an explicit model. |
| `agents.defaults.models.*` | per-model params for the 4 shipped models | Sampling and cache-retention values validated in real pipeline runs. The minimax entry carries `provider.ignore: ["morph"]` plus `require_parameters` routing. |
| `agents.defaults.skipBootstrap` | `true` | Stops OpenClaw from seeding its own starter workspace files over the Lullabeast agent identity docs. |
| `agents.defaults.compaction` | `safeguard` mode + the three `Always-Apply:` section names | Without the section names, the agents' standing rules are dropped from context on every compaction. |
| `agents.defaults.heartbeat.every` | `"0m"` | Required: a non-zero heartbeat interrupts pipeline runs mid-phase. |
| `agents.defaults.maxConcurrent` | `3` | The pipeline runs one agent at a time; headroom covers Ideas flows running beside a phase. |
| `agents.defaults.subagents.maxConcurrent` | `1` | Pipeline agents do not rely on subagent fan-out; an uncapped fan-out only multiplies spend. |
| `agents.list[planner]` | `${PLANNER_MODEL}`, tools `read, write, exec` | Explicit `exec` is load-bearing (a past OpenClaw release stripped it and broke planning). The planner default is `z-ai/glm-5.2` (text-only; planning needs no vision). `minimax/minimax-m3` and `kimi-k2.7-code` stay shipped, priced, and selectable via `PLANNER_MODEL` — `minimax/minimax-m3` was dropped as the planner default after a first-run test with a fresh OpenRouter key hit "404 No endpoints found" (its `require_parameters` routing left no tool-capable endpoint). |
| `agents.list[executor]` | `${EXECUTOR_MODEL}`, tools `read, write, edit, exec, process, browser` | Must be multimodal: it captures screenshots on UI/INT phases. |
| `agents.list[reviewer]` | `${REVIEWER_MODEL}`, tools `read, write, exec, process, browser` | Must be multimodal: it performs visual review. |
| `agents.list[prd-creator]` | `${PRD_MODEL}`, read/write only | Drafting agent; needs no execution surface. |
| `agents.list[escalation]` | `${ESCALATION_MODEL}`, notify-only policy (`profile: minimal`, allow `read/write/message`, deny `edit/apply_patch/exec/process/browser`), `default: true` | The reviewed security posture: the escalation agent reads diagnostics and notifies the human, nothing else. Must not be weakened. Its default model is the cheapest shipped entry. |
| `agents.list[roadmap-converter]` | `${ROADMAP_MODEL}`, read/write only | Conversion agent; needs no execution surface. |
| all six agents | `bootstrapMaxChars: 32000`, `postCompactionMaxChars: 8000` (pipeline roles) | OpenClaw's default caps truncate the agents' instruction files and silently drop their standing rules. |
| `tools.profile` | `"coding"` | Required baseline for the pipeline's tool surface. |
| `tools.exec` | unattended-exec whitelist (`ask`, `safeBins`, `safeBinProfiles`) | Without it, gate scripts and test runs stall on interactive exec-approval prompts. |
| `tools.loopDetection` | tuned values | OpenClaw's own in-turn loop guard; validated in real runs. (Lullabeast's own tool-loop catcher is independent of it.) |
| `hooks` | `enabled: true`, `${HOOKS_TOKEN}`, `defaultSessionKey: "pipeline:default"`, `allowRequestSessionKey: true`, prefixes `["pipeline:", "ideas:"]`, the 6 Lullabeast agent ids, internal hooks on with `session-memory` disabled | The webhook contract the whole pipeline runs on. The `session-memory` exception keeps per-session memory writes out of pipeline sessions. |
| `gateway.port` / `gateway.mode` | `18789` / `"local"` | Standard gateway wiring; compose publishes 18789 to the host loopback so OpenClaw's own Control UI (model/provider management) is reachable. |
| `gateway.bind` | `"lan"` | The gateway must accept the docker-proxied connection (non-loopback source), so it binds beyond loopback inside the container; host exposure stays loopback-only via the compose publish. `"lan"` is the validated enum value on the pinned version. |
| `gateway.auth` | token mode, `${GATEWAY_TOKEN}` | The orchestrator authenticates with this token for the `sessions.steer` abort lever, and the Control UI requires it to connect. The dashboard's Settings screen surfaces it (token-guarded). |
| `gateway.controlUi.allowedOrigins` | loopback origins for 18789 | The Control UI rejects any browser origin not on this list. Listing `http://127.0.0.1:18789` + `http://localhost:18789` lets the operator open it from the host without a manual origin edit. Port-coupled: matches the shipped `18789:18789` publish. |
| `skills.install.nodeManager` | `"npm"` | The signals-plugin build uses npm. |
| `skills.entries.*` | all bundled OpenClaw skills disabled | Pipeline agents get skills only via Lullabeast's per-phase workspace injection; bundled-skill listings are context noise. Known limitation: an OpenClaw release adding new bundled skills defaults them on until they are added here. |
| `plugins.allow` | `anthropic`, `autodev-pipeline-signals`, `browser`, `memory-core`, `openrouter` | The minimal plugin surface the pipeline needs. |
| `plugins.entries.autodev-pipeline-signals` | `enabled: true`, `hooks.allowConversationAccess: true` | Required: without conversation access the activity-stamp hooks never fire and stall detection is blind. |
| `plugins.entries.openrouter` / `.anthropic` | enabled | The recommended provider path and the supported first-party alternative. |
| `plugins.entries.memory-core` | enabled, `dreaming` disabled | The validated posture. |
| `plugins.entries.browser` | enabled | The executor/reviewer browser tool depends on it. |
| `plugins.bundledDiscovery` | `"compat"` | Matches the pinned OpenClaw version's discovery behavior. |
| `browser` | `enabled`, `headless`, `noSandbox` | `noSandbox` is required for containerized Chromium; headless is the only mode that makes sense in the image. |
| `mcp.servers.playwright` | `npx -y @playwright/mcp@0.0.40 --headless` | The visual-review MCP. The audit found this registration missing from an otherwise-working install (the one "actively wrong, fix it everywhere" finding), so the template ships it and `install.sh` writes it on host installs. |

## What is deliberately not in the template

Keys that are personal or install-specific are not templated, and because the
boot-time reconcile only enforces keys the template declares, anything you add
in these areas survives upgrades (see the customization contract in the
[deploy README](README.md)):

- **Provider auth**: API keys are provisioned at first boot from
  `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY`, never baked into a config file.
- **Extra providers and models**: direct-provider entries, local model servers,
  image-generation models. Add your own; pricing outside the shipped set is
  meter-it-yourself.
- **Messaging channels**: Signal/chat channel config, bindings, chat-command
  settings, TTS. The container has no messaging channels by default.
- **Gateway network config**: control-UI origins, VPN/tailnet settings.
- **OpenClaw's own runtime bookkeeping** (`meta`, `wizard`, `update`): the
  gateway rewrites these at runtime; the container pins its OpenClaw version
  via the image build arg, so no self-update channel applies.
- **Agent-to-agent messaging**: off. Pipeline agents never message each other;
  only the escalation agent messages the human.

## Cost tracking

The template ships `models.pricing.enabled: true` plus the 4 recommended
OpenRouter model entries, each with a complete pricing block. **Field names
verified against the pinned OpenClaw version (2026.6.11):** a model entry
carries `cost: {input, output, cacheRead, cacheWrite}` in USD per million
tokens.

Anything outside the shipped set is meter-it-yourself: if the dashboard shows
$0 for a run, OpenClaw has no pricing for the model that ran. See the pointer
in the [deploy README](README.md) and the full walkthrough in SETUP.md under
"Cost metrics: configuring OpenClaw so Lullabeast can report run cost".

## Per-agent model wiring (env substitution contract)

One knob per agent role; defaults are encoded in
`autodev/installer/openclaw_template.py::TEMPLATE_MODEL_DEFAULTS`:

| Env var | Default | Used by |
|---|---|---|
| `PLANNER_MODEL` | `openrouter/z-ai/glm-5.2` | planner, `agents.defaults.model` (text-only) |
| `EXECUTOR_MODEL` | `openrouter/moonshotai/kimi-k2.7-code` | executor (multimodal, required) |
| `REVIEWER_MODEL` | `openrouter/moonshotai/kimi-k2.7-code` | reviewer (multimodal, required) |
| `PRD_MODEL` | `openrouter/moonshotai/kimi-k2.7-code` | prd-creator |
| `ROADMAP_MODEL` | `openrouter/z-ai/glm-5.2` | roadmap-converter |
| `ESCALATION_MODEL` | `openrouter/qwen/qwen3.6-27b` | escalation (notify-only) |

`HOOKS_TOKEN` and `GATEWAY_TOKEN` are the two required render-time secrets; the
container entrypoint generates them on first boot and persists them under
`/data`. Rendering fails loud (naming the variable) if either is missing.

## Cloud-first timing defaults

Lullabeast's stall-detection and backstop knobs (`AUTODEV_STALL_TIMEOUT_*`,
`AUTODEV_INFRA_BACKSTOP_*`) are left unset in the container, so the code
defaults apply (300 s stall, 4500 s backstop). Those defaults assume
cloud-model latency. Local-model installs typically need them raised; that
guidance lives in SETUP.md's local-models section and the deploy README's
"Local models on the host" section, not in the template.

One `.env` setting is recommended for cloud users and carries into
[.env.example](.env.example): `PROVIDER_ERROR_RETRY=3` (transient 429 and
rate-limit responses are a cloud-provider phenomenon, exactly what the knob
exists for).

## Minimum hardware

With cloud inference the container is light. Target minimums (also stated in
the main README):

- **2 CPU cores**
- **2-4 GB RAM** (Playwright/Chromium is the heaviest resident; the gateway, UI
  server, and orchestrator are small Python/Node processes)
- **A few GB of disk** for the image, `/data` state, and generated project
  repos

No GPU. GPU-in-container local models are explicitly out of scope for this
release.
