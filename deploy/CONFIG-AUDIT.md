# OpenClaw golden-config audit (DS-2b)

**What this is.** End-to-end pass over the live runtime `~/.openclaw/openclaw.json`
(as of 2026-07-06, OpenClaw 2026.6.11), asking for every key: is this what a 
standard user should get, and is it optimal for the pipeline? The output is
[openclaw.template.json](openclaw.template.json), the canonical config that the DS-3
container entrypoint renders into `/data/openclaw/openclaw.json` on first boot. Every
prior config audit was incident-driven; this one is the full walk.

**How to read the table.** One row per key path of the live config; every key of the
live file appears exactly once, under these conventions:

- A row for a block (for example `meta`) covers all of its children when the decision
  is uniform for the whole block.
- A `*` wildcard row covers all children of a homogeneous map (for example
  `skills.entries.*`).
- Secrets are redacted as `[REDACTED]`. No secret value appears in this document or
  in the template (the template carries `${HOOKS_TOKEN}` / `${GATEWAY_TOKEN}`
  placeholders instead; the tests reject secret-shaped strings).

**Decision categories** (from the roadmap):

1. **ship-as-is**: same key, same value in the template.
2. **ship-different-default**: key ships, value changed for a standard cloud-key user
   (the "template value" column says what).
3. **operator-only**: personal or hardware-specific; not shipped.
4. **fix-everywhere**: the live config is wrong or incomplete; the template ships the
   corrected form and host installs get the same fix through install.sh.

## Decision table

| Live key | Decision | Template value / notes |
|---|---|---|
| `meta` (`lastTouchedVersion`, `lastTouchedAt`) | operator-only | Gateway-owned bookkeeping; OpenClaw rewrites it at runtime. Never templated. |
| `wizard` (`lastRunAt`, `lastRunVersion`, `lastRunCommand`, `lastRunMode`) | operator-only | Gateway-owned onboarding record. |
| `update.channel` | operator-only | `"stable"` on the host. The container pins the OpenClaw version via the image build arg, so a self-update channel has no place in the template. |
| `auth.profiles` (`anthropic:default`) | operator-only | API-key auth is provisioned at first boot by the DS-3 entrypoint (from `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY`), never baked into a template. DS-3 must verify the exact provisioning mechanism against the pinned OpenClaw version. |
| `models.mode` | ship-as-is | `"merge"`: custom provider entries merge with OpenClaw's built-ins. |
| `models.pricing.enabled` | ship-as-is | `true`. Required for cost tracking (decided compromise, see "Cost tracking" below). |
| `models.providers.openrouter.baseUrl` / `.api` | ship-as-is | Standard OpenRouter endpoint, `openai-completions` API. |
| `models.providers.openrouter.models[]` | ship-different-default | Live carries 9 entries; the template ships the 4 recommended, cheap, pipeline-capable ones, each with a complete 4-field `cost` block: `qwen/qwen3.6-27b` (escalation default), `minimax/minimax-m3`, `moonshotai/kimi-k2.7-code`, `z-ai/glm-5.2`. Dropped: the live `qwen/qwen3.6-35b-a3b` (replaced by 27B), `minimax/minimax-m2.7` and `deepseek/deepseek-v4-pro` (text-only; the recommended set favors multimodal), `moonshotai/kimi-k2.6` (superseded by k2.7-code), `anthropic/claude-sonnet-4.6` and `anthropic/claude-opus-4.8` via OpenRouter (premium tier; meter-it-yourself). |
| `models.providers.anthropic` | operator-only | Direct-Anthropic model entry for personal use. The shipped pricing set is OpenRouter-only by decision; any other provider is meter-it-yourself. |
| `models.providers.llamacpp` | operator-only | Local-model hardware artifact (LAN llama-server URL, `timeoutSeconds: 600`, placeholder pricing). Local-model guidance lives in SETUP.md and DS-7. |
| `models.providers.google` | operator-only | Personal provider; contains a live API key `[REDACTED]` inline. Not shipped. |
| `agents.defaults.model` | ship-different-default | `${PLANNER_MODEL}` (was `openrouter/minimax/minimax-m2.7`). A safe fallback for any agent without an explicit model. |
| `agents.defaults.models.*` | ship-different-default | Live has 12 per-model param entries (sampling params, cache retention). The template keeps only the 4 entries for the shipped models, verbatim from the live validated values (qwen 0.6/0.95; minimax-m3 0.7/0.95 plus the `provider.ignore: ["morph"]` + `require_parameters` routing fix; kimi-k2.7-code 0.6/0.95; glm-5.2 0.6/0.95). The llamacpp/* and Anthropic-alias entries are operator-only. |
| `agents.defaults.skipBootstrap` | ship-as-is | `true`. Stops OpenClaw from seeding its own starter workspace files over the Lullabeast agent identity docs. |
| `agents.defaults.compaction` (`mode`, `postCompactionSections`) | ship-as-is | Required baseline: `safeguard` mode plus the three `Always-Apply:` headers and OpenClaw's own two defaults. Without the section names, the standing rules are dropped on every compaction. |
| `agents.defaults.heartbeat.every` | ship-as-is | `"0m"`. Required: a non-zero heartbeat interrupts pipeline runs mid-phase. |
| `agents.defaults.maxConcurrent` | ship-as-is | `3`. The pipeline runs one agent at a time; headroom covers Ideas flows running beside a phase. |
| `agents.defaults.subagents.maxConcurrent` | ship-as-is | `1`. Originally a single-GPU protection, kept deliberately as a cost bound: pipeline agents do not rely on subagent fan-out, and an uncapped cloud fan-out only multiplies spend. |
| `agents.defaults.imageGenerationModel` | operator-only | Points at the personal Google provider. |
| `agents.list[planner]` | ship-different-default | `model.primary` was `llamacpp/qwen3.6-27b` (operator hardware); ships as `${PLANNER_MODEL}` (default `openrouter/moonshotai/kimi-k2.7-code`). The acceptance run on 2026-07-07 with a fresh OpenRouter key got "404 No endpoints found for the following models: minimax/minimax-m3" from the planner three times (the `require_parameters` routing left no tool-capable endpoint for OpenClaw's tool-bearing requests), so the planner default moved to the proven kimi-k2.7-code; the `minimax/minimax-m3` entry stays shipped, priced, and selectable via `PLANNER_MODEL`. Workspace path, explicit `tools.allow: [read, write, exec]` (the OpenClaw 2026.6.8 planner-exec incident fix), and both context caps ship as-is. |
| `agents.list[executor]` | ship-different-default | Model ships as `${EXECUTOR_MODEL}` (default `openrouter/moonshotai/kimi-k2.7-code`, multimodal, required for screenshot capture on UI/INT phases). Tools (`read, write, edit, exec, process, browser`) and caps as-is. |
| `agents.list[reviewer]` | ship-different-default | Model ships as `${REVIEWER_MODEL}` (default `openrouter/z-ai/glm-5.2`, multimodal, required for visual review). Tools (`read, write, exec, process, browser`) and caps as-is. |
| `agents.list[prd-creator]` | ship-different-default | Model ships as `${PRD_MODEL}` (default `openrouter/moonshotai/kimi-k2.7-code`; was `openrouter/moonshotai/kimi-k2.6`). Read/write-only tool policy ships as-is. |
| `agents.list[escalation]` | ship-different-default | Model ships as `${ESCALATION_MODEL}` (default `openrouter/qwen/qwen3.6-27b` at $0.285 input / $2.40 output per M, in the standard `{primary, fallbacks}` dict shape; live used a bare string pointing at a local model). The notify-only tool policy (`profile: minimal`, `alsoAllow: read/write/message`, deny `edit/apply_patch/exec/process/browser`) and the `default: true` flag ship as-is; this is the reviewed security posture and must not be weakened. |
| `agents.list[roadmap-converter]` | ship-different-default | Model ships as `${ROADMAP_MODEL}` (default `openrouter/z-ai/glm-5.2`, matching the live pick). Read/write-only tool policy as-is. |
| `agents.list[personal-assistant]` | operator-only | Personal Signal advisor agent; also removed from `hooks.allowedAgentIds` in the template. |
| `tools.profile` | ship-as-is | `"coding"`. Required baseline. |
| `tools.agentToAgent.enabled` | operator-only | Pipeline agents never message each other (only escalation messages the human, via the gateway connector); a smaller default surface for a standard user. |
| `tools.exec` (`ask`, `safeBins`, `safeBinProfiles`) | ship-as-is | The unattended-exec whitelist the pipeline was validated with; without it, gate scripts and test runs stall on exec approval prompts. |
| `tools.loopDetection` | ship-as-is | OpenClaw's own in-turn loop guard, tuned values live-validated. (Lullabeast's Tier 1 catcher is independent of this.) |
| `bindings[]` | operator-only | Signal channel routing; contains a personal group id `[REDACTED]`. |
| `messages` (`ackReactionScope`, `tts`) | operator-only | Signal/TTS personalization. |
| `commands` (`native`, `nativeSkills`, `restart`, `ownerDisplay`) | operator-only | Chat-command settings for messaging channels; the container has no channels. |
| `session.dmScope` | operator-only | Messaging-channel session scoping. |
| `hooks.enabled` | ship-as-is | `true`. Required. |
| `hooks.token` | ship-different-default | Live value `[REDACTED]`; ships as the `${HOOKS_TOKEN}` placeholder, generated per install at first boot. |
| `hooks.defaultSessionKey` | ship-as-is | `"pipeline:default"`. |
| `hooks.allowRequestSessionKey` | ship-as-is | `true`. Required. |
| `hooks.allowedSessionKeyPrefixes` | ship-as-is | `["pipeline:", "ideas:"]`. Required. |
| `hooks.allowedAgentIds` | ship-different-default | The 6 Lullabeast agents only; live also allowed `personal-assistant`. |
| `hooks.internal` | ship-as-is | Internal hooks on, `session-memory` entry disabled (keeps per-session memory writes out of pipeline sessions). |
| `channels.signal` | operator-only | Personal Signal account: phone numbers and allowlists `[REDACTED]`. |
| `gateway.port` | ship-as-is | `18789`. |
| `gateway.mode` | ship-as-is | `"local"`. |
| `gateway.bind` | ship-as-is | `"lan"`, with a caveat: inside the container this is unexposed anyway because compose does not publish 18789. DS-3 may tighten to a loopback bind after verifying the accepted enum values on the pinned version; "lan" is kept here because it is the only live-validated value. |
| `gateway.controlUi` | operator-only | Personal Tailscale origins plus `allowInsecureAuth` for a LAN setup. |
| `gateway.auth` | ship-different-default | Token mode ships; the value `[REDACTED]` becomes the `${GATEWAY_TOKEN}` placeholder. The orchestrator reads this token for the `sessions.steer` abort lever. |
| `gateway.tailscale` | operator-only | Personal network config. |
| `skills.install.nodeManager` | ship-as-is | `"npm"`; the signals-plugin build uses npm. |
| `skills.entries.*` | ship-as-is | All 42 bundled OpenClaw skills disabled: pipeline agents get skills only via Lullabeast's per-phase workspace injection, and bundled-skill listings are context noise. Known limitation: a future OpenClaw release adding new bundled skills defaults them on until they are added here. |
| `plugins.allow` | ship-different-default | Drops `signal`, `google`, `microsoft` (operator channels/providers); keeps `anthropic`, `autodev-pipeline-signals`, `browser`, `memory-core`, `openrouter`. |
| `plugins.entries.signal` | operator-only | Personal messaging channel. |
| `plugins.entries.openrouter` | ship-as-is | `enabled: true`; the recommended provider path. |
| `plugins.entries.anthropic` | ship-as-is | `enabled: true`; supported first-party alternative. |
| `plugins.entries.memory-core` | ship-as-is | Enabled with `dreaming` disabled, matching the live validated posture. |
| `plugins.entries.google` | operator-only | Personal provider plugin. |
| `plugins.entries.autodev-pipeline-signals` | ship-as-is | `enabled: true` with `hooks.allowConversationAccess: true`. Required: without conversation access the activity-stamp hooks never fire and stall detection is blind. |
| `plugins.entries.browser` | ship-as-is | `enabled: true`; the executor/reviewer browser tool depends on it. |
| `plugins.entries.microsoft` | operator-only | TTS provider for the personal assistant. |
| `plugins.bundledDiscovery` | ship-as-is | `"compat"`. |
| `browser` (`enabled`, `headless`, `noSandbox`) | ship-as-is | `noSandbox` is required for containerized Chromium; headless is the only mode that makes sense in the image. |
| `mcp.servers.playwright` | fix-everywhere | Absent from the live config (the doctor's standing `playwright` warn on this machine). The template ships the registration install.sh step 12 writes (`npx -y @playwright/mcp@0.0.40 --headless`); host installs get it from install.sh. This is the one "actively wrong, fix everywhere" finding of the audit. |

## Cost tracking (decided compromise)

The template ships `models.pricing.enabled: true` plus the 4 recommended OpenRouter
model entries above, each with a complete pricing block. **Field names verified
against the pinned OpenClaw version (2026.6.11):** a model entry carries
`cost: {input, output, cacheRead, cacheWrite}` in USD per million tokens. (The
roadmap sketched `inputPerMillion`-style names; the live pinned config proves the
actual schema, and the template and its tests use the verified names.)

Anything outside the shipped set is meter-it-yourself: if the dashboard shows $0
for a run, OpenClaw has no pricing for the model that ran. See the pointer in
[deploy/README.md](README.md) and the full walkthrough in SETUP.md under "Cost
metrics: configuring OpenClaw so Lullabeast can report run cost".

## Per-agent model wiring (env substitution contract)

One knob per agent role; defaults are the audit picks (encoded in
`autodev/installer/openclaw_template.py::TEMPLATE_MODEL_DEFAULTS`):

| Env var | Default | Used by |
|---|---|---|
| `PLANNER_MODEL` | `openrouter/moonshotai/kimi-k2.7-code` | planner, `agents.defaults.model` |
| `EXECUTOR_MODEL` | `openrouter/moonshotai/kimi-k2.7-code` | executor (multimodal, required) |
| `REVIEWER_MODEL` | `openrouter/z-ai/glm-5.2` | reviewer (multimodal, required) |
| `PRD_MODEL` | `openrouter/moonshotai/kimi-k2.7-code` | prd-creator |
| `ROADMAP_MODEL` | `openrouter/z-ai/glm-5.2` | roadmap-converter |
| `ESCALATION_MODEL` | `openrouter/qwen/qwen3.6-27b` | escalation (notify-only) |

`HOOKS_TOKEN` and `GATEWAY_TOKEN` are the two required render-time secrets; the
DS-3 entrypoint generates them on first boot and persists them under `/data`.
Rendering fails loud (naming the variable) if either is missing.

## Hardware-defaults reset

Everything below was tuned for the operator's local-model hardware (Pi-era, then a
shared llama-server host) and is deliberately NOT shipped. With cloud inference the
built-in code defaults are correct:

| Knob | Operator value (live `.env`) | Cloud default (shipped) |
|---|---|---|
| `AUTODEV_STALL_TIMEOUT_{PLANNER,EXECUTOR,REVIEWER}` | 1200 / 1800 / 1200 | unset (code default 300 s) |
| `AUTODEV_INFRA_BACKSTOP_{PLANNER,EXECUTOR,REVIEWER}` | 10800 | unset (code default 4500 s) |
| Agent `model.primary` on `llamacpp/*` | local models | `${*_MODEL}` cloud placeholders |
| `models.providers.llamacpp` (incl. `timeoutSeconds: 600`) | present | not shipped |

Kept on purpose despite their local-hardware origin: `agents.defaults.subagents.maxConcurrent: 1`
(now a cost bound) and `tools.loopDetection` (model-agnostic guard). One operator
`.env` setting is recommended for cloud users too and should carry into DS-3's
`.env.example`: `PROVIDER_ERROR_RETRY=3` (transient 429/rate-limit retries are a
cloud-provider phenomenon, exactly what the knob exists for).

Local-model tuning guidance stays in SETUP.md's local-models section (DS-7 links
it); none of it belongs in the template.

## Minimum hardware statement

With cloud inference the container is light. Target minimums (DS-6 puts this in the
README):

- **2 CPU cores**
- **2-4 GB RAM** (Playwright/Chromium is the heaviest resident; the gateway, UI
  server, and orchestrator are small Python/Node processes)
- **A few GB of disk** for the image, `/data` state, and generated project repos

No GPU. GPU-in-container local models are explicitly out of scope for this release.
