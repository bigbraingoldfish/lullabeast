# PIPELINE-CONSTRAINTS.md — Known Issues, Risks & Deferred Items

> **Purpose:** Reference material for "watch out for this" — loaded as context, not as active implementation tasks.
> **Audience:** AI coding agents and human operators.

---

## 1. Hardware Constraints

### VRAM Management (RTX 4090 — custom 48GB)

- Qwen3-Coder-Next (executor) and Qwen3.5-27B Q6_K (reviewer/escalation) are the two active local models. Both served by the same llama-server instance on the Main machine. Model switching occurs between executor and reviewer phases — see § OB-6 for race condition and mitigation. Qwen3.5-27B: ~19GB weights + KV cache q8_0 at 98304 ctx ≈ 22GB total on 48GB card.

### Quantization Limits

- **FP8 not viable on RTX 4090** — Hopper-only feature (H100/H200). Q6_K is the chosen quantization for Qwen3.5-27B — optimal quality/VRAM balance on the 48GB card.

### SD Card Exhaustion (Raspberry Pi)

- **Log Rotation** — The Raspberry Pi SD card is highly susceptible to exhaustion from unbounded log growth. `heartbeat.log`, `session_cleanup.log`, and `orchestrator.log` (or whatever target systemd uses for orchestrator stdout) must be included in the logrotate configuration. Use `missingok` since log files may not exist in all deployment configurations. Keep only the last ~5MB per log file. Without rotation, unbounded log growth risks SD card exhaustion on the Pi 5. This is a critical safety-of-hardware concern. The in-process fallback rotation in `session_cleanup.py` (`rotate_pipeline_logs`) resolves `heartbeat.log` and `orchestrator.log` against `AUTODEV_PIPELINE_ROOT` (the `.autodev` state dir — where both are actually written); `session_cleanup.log` stays under `OPENCLAW_ROOT`.

---

## 2. Model-Specific Issues

### Local Model Roster (updated 2026-03-10)

All local models are served by the `llama-local` provider (e.g. `http://<llama-server-host>:11434/v1` — set `models.providers.llama-local.baseUrl` in `openclaw.json`, or set env `AUTODEV_LLAMA_BASE` for orchestrator/heartbeat fallbacks). All are registered at `contextWindow: 65536` (64K) in OpenClaw config.

All GGUFs redownloaded 2026-03-09 from Unsloth Dynamic 2.0 (March 5 update). Includes improved quantization algorithm, new imatrix calibration data, tool calling chat template fix, and MXFP4 layer retirement from K_XL quants.

| Model ID | Role | Status | ctx-size (server) |
|---|---|---|---|
| `qwen3-coder-next` | Executor (attempts 1–2) | Active | 65536 (64K) |
| `qwen3.5-27b` | Reviewer, Escalation | Active | 65536 (64K) |
| `darkqwen3.5-27b` | — | Registered, not utilized | 65536 (64K) |
| `qwen3.5-9b` | — | Registered, not utilized | 32768 (32K) |
| `darkqwen3.5-9b` | — | Registered, not utilized | 32768 (32K) |

Cloud fallback for executor (attempt 3): `anthropic/claude-sonnet-4-6`.

### Qwen3-Coder-Next (Executor)

- **Role:** Executor local model (attempts 1–2). Reinstated 2026-03-08 after temporary retirement in favour of Qwen3.5-27B.
- **Context window:** 65K (65536 tokens) server-side ctx — bumped from 32K to 64K on 2026-03-10 after CORE-4 OOM. Now matches OpenClaw's registered `contextWindow: 65536`. Q3_K_XL quant. Further increase to 97K is viable if needed (48GB card has VRAM headroom).
- **Required flags:** `--chat-template-file qwen3-coder-chat_template.jinja`, `chat-template-kwargs = {"enable_thinking":false}` — Coder-Next (Unsloth Dynamic 2.0, March 5 GGUF) generates thinking tokens before tool calls without it, corrupting the XML tool call parser and causing JSON stringify failures. The `qwen3-coder-chat_template.jinja` template must include thinking suppression in the generation prompt (same pattern as `chat_template.jinja`).
- **Sampling:** `temperature: 0.7`, `top_p: 0.8`, `top_k: 20`, `min_p: 0.0`, `repeat_penalty: 1.05` — uses `repeat_penalty`, **NOT** `presence_penalty` (different algorithm). Do not cross-apply.
- Served by the same llama-server instance as Qwen3.5-27B. Session key separation is the isolation mechanism.

### llama-server / Windows

- **Windows 11 GPU utilization bug during tool calls** — llama.cpp Issue #15389. Monitor for fix.

### `apiKey: "no-key"` Required on `llama-local` Provider (CRITICAL)

OpenClaw requires an explicit `apiKey` field on all providers, even unauthenticated local ones. Without it, OpenClaw inherits the `anthropic:default` auth profile and injects the Anthropic API key into the request. The local server rejects this as an auth failure and OpenClaw **silently falls back to `anthropic/claude-sonnet-4-6`** — no warning in the agent response, only visible in the session's `fallbackNoticeReason: auth` field. This caused all Signal DM sessions to spend on Sonnet 4.6 even when per-agent model was correctly configured. Fix: `"apiKey": "no-key"` in the provider definition. Use any non-empty placeholder value.

### MiniMax M2.5 — File Deletion Under Token Pressure

**Confirmed behaviour:** MiniMax M2.5 (used for planner, executor, and reviewer via OpenRouter) deletes existing project files when approaching context limits during the executor phase. The executor then writes `all_passing: true` with the deleted file still listed in `file_manifest` — a false-positive completion report.

**Observed pattern:** `ui/index.html` (and similar static assets) deleted mid-execution; `executor_output.json` reports the file as present; reviewer or integration test discovers the absence.

**Primary mitigation (ERR_UNACCOUNTED_DELETION, Phase 3 enhancement):** The executor gate runs `git diff --diff-filter=D <phase_base_commit> HEAD` after all other checks. Any file present at `phase_base_commit` that is absent from both `file_manifest` and `files_deleted` triggers `ERR_UNACCOUNTED_DELETION` — a gate failure that prevents false-positive advancement to review. This catches the deletion regardless of what the executor self-reports.

**Residual risk:** If the executor omits the deleted file from `file_manifest` entirely (rather than listing it with no physical file), the manifest check passes and only the git-diff-based deletion check catches it. If `phase_base_commit` is absent or the git command fails, the check is skipped (non-fatal). Monitor for this in production; the `files_deleted` field in `executor_output.json` provides the opt-out path for intentional deletions.

**Secondary mitigation:** The executor `AGENTS.md` contract requires listing all intentionally deleted pre-existing files in `files_deleted`. Files created and deleted within the same phase do not need to be listed.

---

### ~~MiroThinker-30b (Reviewer)~~ Retired 2026-03-04

Ollama tools API limitation; role now served by Qwen3.5-27B.

### Qwen3.5-27B (Reviewer, Escalation)

- **Quantization:** Q6_K, 48GB card, ~19GB weights, KV cache q8_0 (K+V) at 98304 ctx = ~22GB total.
- **Required flags:** `--chat-template-file chat_template.jinja`, `--jinja`, `--chat-template-kwargs '{"enable_thinking":false}'`, `--reasoning-budget 0`, `--fit on`.
  - `--chat-template-file` takes precedence over the embedded GGUF template; `--jinja` enables the Jinja engine required by both.
  - `--chat-template-kwargs` suppresses `<think>` tokens at the template level; `--reasoning-budget 0` is the server-level backstop.
- **Flash attention:** `-fa on` enabled.
- **Sampling:** `presence_penalty: 0.8`, `temperature: 0.6`, `top_p: 0.95`, `top_k: 20`, `min_p: 0.0` — set as per-model defaults in `models-preset.ini` and overridable per-request.
- **Serves reviewer and escalation roles.** Session key separation is the isolation mechanism. Executor uses `qwen3-coder-next` (see §2 > Qwen3-Coder-Next).
- **Three known failure modes:** (1) jinja `items` filter — fixed in llama.cpp b8149+, but `--chat-template-file` is retained for XML format enforcement (pipeline behavioral contract); (2) recursive thinking trap — covered by `--reasoning-budget 0` + `--chat-template-kwargs`; (3) JSON-prose hybrid tool output — covered by jinja template XML format enforcement.

---

## 3. OpenClaw Platform Notes

### Webhook `agentId` Routing

- `POST /hooks/agent` routes to specific agents via the `agentId` field in the request body — this is a built-in feature of the OpenClaw webhook endpoint, not a custom `hooks.mappings` configuration. The `hooks.allowedAgentIds` array in `openclaw.json` restricts which agents can be targeted. Phase 0 should validate that this routes correctly in the deployed environment. See: PIPELINE-ROADMAP.md § Phase 0.

### Tool Policy Granularity

- Per-agent `tools.allow`/`tools.deny` with individual tool names and `group:*` shorthand is confirmed supported. This is granular, not all-or-nothing.

### Bootstrap Skip

- `agents.defaults.skipBootstrap: true` required for all pipeline agents — applied globally via `agents.defaults`, not per-agent. See PIPELINE-SPEC.md § OpenClaw Configuration for full details. Without it, OpenClaw overwrites hand-crafted identity files on first run.

### Prompt Cache TTL

- Prompt caching configured via `agents.defaults.models["anthropic/claude-sonnet-4-6"].params.cacheRetention: "short"` (5-minute TTL). See PIPELINE-SPEC.md § OpenClaw Configuration for full details. Key risk: modifying workspace files mid-pipeline invalidates the 5-minute cache window and negates cost savings.

### Signal Channel

- Must be verified as Phase 0 prerequisite — see PIPELINE-SPEC.md § Pure Script Inventory > Signal Notification Implementation for verification command.

### OpenClaw Auth Chain

When an agent turn requires the Anthropic API, OpenClaw resolves credentials in this order:

1. **`auth-profiles.json`** — per-agent file at `~/.openclaw/agents/<id>/agent/auth-profiles.json`. OpenClaw merges each sub-agent's store with the main agent's store (`agents/main/agent/auth-profiles.json`), so a key present in `main` propagates to all agents.
2. **Environment variable** — `ANTHROPIC_API_KEY` (or `ANTHROPIC_OAUTH_TOKEN`). The gateway loads `~/.openclaw/.env` via dotenv at startup; the systemd unit does **not** load `secrets.env`.
3. **`models.json` provider `apiKey` field** — last resort; not used in this deployment.

**What breaks silently:** if `~/.openclaw/agents/` is deleted or recreated from scratch (e.g. after accidental removal), `auth-profiles.json` is not regenerated automatically. The agent directories and `auth.json`/`models.json` stubs are created, but `auth-profiles.json` is absent. Agents appear healthy in `openclaw doctor` and the webhook returns `ok: true`, but every Anthropic API call inside a turn will fail.

**Recovery:** For each of the five agent dirs (`main`, `planner`, `executor`, `reviewer`, `escalation`), write:

```json
{
  "version": 1,
  "profiles": {
    "anthropic:default": {
      "type": "api_key",
      "provider": "anthropic",
      "key": "<key from ~/.openclaw/secrets.env>"
    }
  }
}
```

Set permissions to `600`. Also append `ANTHROPIC_API_KEY=...` to `~/.openclaw/.env` and restart the gateway (`systemctl --user restart openclaw-gateway`) so the env-var fallback tier is live. Verify with `POST /hooks/agent` → expect `{"ok":true}`.

---

## 3.5. Executor Status Corner Case

An executor that hits the 20-tool-call hard stop writes `status: "stuck"` with partial files in the workspace. In the typical case, tests fail on partial implementation and the gate catches it via `test_results.all_passing: false` — correct behavior, increments `executor_retries`.

The corner case: if the executor hits the hard stop mid-implementation but enough code exists that tests pass (unlikely but possible on a partially implemented phase), the gate would pass it to the reviewer without the `status` check. The reviewer catches stubs and incomplete implementations, so it functions as a backstop — but relying on a reviewer pass to catch what the gate should have caught is fragile.

The `status != "complete"` gate check (see PIPELINE-SPEC.md § Gate Scripts > Executor Output Gate) short-circuits this: any self-reported `"stuck"` or `"failed"` is an immediate gate failure regardless of test results. This is cheap to implement and eliminates the backstop dependency.

---

## 3.6. OpenClaw Write Sandbox Behavior

- OpenClaw sandboxes the `write` tool to each agent's declared workspace directory
- Writes targeting paths outside the workspace silently report success but discard the file — no error, no warning
- This is platform behavior, not configurable per-agent or per-path
- Mitigation: each workspace contains a `pipeline-project` symlink pointing to `~/.openclaw/pipeline-project`, which resolves to the actual project directory
- Discovered during Layer 2 live testing when the escalation agent's read-only policy was applied and sentinel writes silently failed

---

## 4. Deferred Items — Not in v1

Items intentionally excluded from v1 scope. Revisit based on operational experience.

### Planner Gate Size Validation

Max tasks / max test cases per phase. Every phase is meant to be atomic; if patterns emerge via `lessons.md` that suggest this is needed, add it then.

### Harder Escalation Notification Guarantee

Cron retrying `escalation_failed.json` instead of relying on silent halt + manual discovery. Silent halt is acceptable for v1.

### Token / Context Usage Instrumentation

Monitoring executor token usage. Verbosity cap in `AGENTS.md` + tool-call-count threshold covers the risk adequately for now.

### Blame Heuristic Tuning

Validate heuristic coverage after first 5 real pipeline runs — if LLM fallback never fires, heuristic is sufficient; if it fires often, invest in improving pattern matching. See: PIPELINE-SPEC.md § Gate Scripts > Blame Attribution.

### Audit Archive Path Hardcoding

- **Hardcoded Path** — The `pipeline-audit` path is currently hardcoded in `orchestrator.py`. This is acceptable for v1, but should be revisited if the deployment topology changes. Do not abstract it for v1.

### ~~Heartbeat Cron — No Project Path Argument~~ ✓ Resolved 2026-03-03 (B4)

- **Gap discovered 2026-02-26 during live run.** ~~The heartbeat cron restarts a dead orchestrator by re-running `orchestrator.py`. It does not pass the `--project-path` argument.~~ **Fixed (B4, 2026-03-03):** `project_path` is now written to `pipeline_state.json` by the orchestrator on every startup (both project-switch and same-project-resume cases). The heartbeat cron reads `project_path` from state and passes it as `--project-path` to the restarted orchestrator. If `project_path` is missing from state, the heartbeat now halts with a logged error requiring manual intervention rather than silently resuming with a potentially stale symlink.

### ~~Roadmap Checkbox Overwrite on Branch Checkout~~ ✓ Resolved 2026-03-03 (B5)

- **Root cause identified 2026-02-26.** ~~When the orchestrator writes the `[x]` checkbox update to `roadmap.md` and then immediately runs `git checkout -b phase/NEXT`, git resets the working tree of `roadmap.md` to HEAD, discarding the in-memory edit.~~ **Fixed (B5, 2026-03-03):** The roadmap checkbox write is now folded atomically into the merge commit via `git commit --amend --no-edit`. The write, `git add roadmap.md`, and amend all occur BEFORE the git tag, and all inside the same try block. The tag is placed AFTER the amend so it points to the commit that includes the checkbox. Since the checkbox is now part of HEAD, `git checkout -b phase/NEXT` cannot revert it.

### ~~qwen3-coder-next Dual Role (Executor + Reviewer)~~ Resolved 2026-03-04 / Revised 2026-03-08

Originally retired in favour of Qwen3.5-27B for all local roles. Reinstated 2026-03-08 with dedicated executor role: qwen3-coder-next for executor, Qwen3.5-27B for reviewer/escalation. Session key separation is the isolation mechanism.

### ~~mirothinker-30b Ollama tools API limitation~~ Resolved 2026-03-04

mirothinker-30b retired; Ollama tools API limitation no longer relevant.

### Escalation Agent — No Git Operation Capability

- **Active constraint as of 2026-02-26.** The escalation agent's tool policy (`allow: [read, write]`, `deny: [exec, process, edit, apply_patch, browser]`) prevents it from performing git operations. This means escalation cannot self-resolve merge conflicts or reset working trees — it can only read the state and notify the human. The `PROCEED` command covers the human-resolution path. **The `RESET_PHASE` and `RESET_EXECUTION` commands (added B6, 2026-03-03) extend this: the escalation agent outputs these command tokens in its `escalation_output.json`; the orchestrator parses them and executes the corresponding Python functions (`reset_phase()` / `reset_execution("escalation")`). The escalation agent gains NO exec capability — the orchestrator owns all git and state-mutation operations.**

### Escalation Reset Commands — Cap Enforcement (B6, 2026-03-03)

- `RESET_PHASE` and `RESET_EXECUTION` are capped at **3 combined escalation-triggered resets per phase** (`escalation_resets` counter in `phase_state.json`). After 3 resets, the orchestrator sends a Signal notification and stays in `WAITING_FOR_HUMAN` until a human issues `PROCEED` or `STOP`. This prevents an infinite escalation-reset loop if the LLM consistently produces bad plans or implementations.
- `escalation_resets` is NOT zeroed inside `reset_phase()` — only zeroed when the roadmap genuinely advances to a new phase (which deletes `phase_state.json`). Zeroing it inside `reset_phase()` would allow circumventing the cap by repeated resets.
- `executor_retries` (incremented by the automatic retry path via `reset_execution("auto")`) and `escalation_resets` (incremented by human-triggered commands) are separate counters. Neither increments the other.

### Executor Retry Counter Split (P0 Stage H, 2026-05-27)

The legacy `executor_retries` counter resets to 0 on every reviewer `ROUTE_EXECUTOR` rejection (it is the per-segment escalation/cap budget). That makes it useless as a lifetime "how many executor attempts ran in this phase" figure — a phase with 4 attempts driven by 1 self-failure + 2 reviewer rejections + initial would show `executor_retries == 0` at metrics-write time.

Stage H persists two additional lifetime counters alongside `executor_retries`:

| Counter | Tracks | Reset on reviewer ROUTE_EXECUTOR | Reset on operator escalation | Reset on `reset_phase()` |
|---|---|---|---|---|
| `executor_retries` (legacy, unchanged) | Per-segment budget for escalation/cap logic | ✓ (segment boundary) | ✓ (operator gives fresh budget) | ✓ |
| `executor_self_failure_retries` (NEW) | Lifetime count of executor self-failures (gate exit 1, sentinel crash, blame=impl) | ✗ preserved | ✗ preserved | ✓ |
| `executor_reviewer_rejection_retries` (NEW) | Lifetime count of reviewer-driven re-runs | ✗ preserved | ✗ preserved | ✓ |

Increment sites:
- `executor_self_failure_retries` → inside `reset_execution("auto")` (alongside the legacy increment)
- `executor_reviewer_rejection_retries` → inline at the `ROUTE_EXECUTOR` handler in the orchestrator main loop (the rejection path bypasses `reset_execution()` entirely; co-locating the increment with the rejection event keeps the counter accurate)

The canonical `metrics.jsonl` row sources `executor_attempts` from these lifetime counters so the invariant `executor_attempts == executor_self_failures + executor_reviewer_rejections + 1` holds. New top-level fields `executor_self_failures` and `executor_reviewer_rejections` give the dashboard the breakdown directly.

Also Stage H: `gate_fail` and `attempt_end` pipeline events now carry `detail.retry_class` (`"initial_attempt"` / `"executor_self_failure"` / `"reviewer_rejection"`) sourced from the orchestrator's process-local `_current_attempt_retry_class` tracker. The activity feed labels retries by class so operators can distinguish "executor stuck → auto-retry" from "reviewer rejection → executor retry" at a glance.

Same release: `apply_reviewer_routing` pass-2 routing tightened from `blocking_issues[0].attribution` (ordering-sensitive) to "any-plan" semantics (if any blocking_issue is tagged `attribution: "plan"`, route to planner). Uses more of the reviewer agent's existing JSON output; does NOT touch the orchestrator's separate `run_blame_attribution()` AI-driven attribution system.

### Reviewer Counter Split (RR-4, 2026-03-12)

The single `reviewer_retries` counter was split into three distinct counters to prevent conflation of genuine LLM rejections with infrastructure failures:

| Counter | Tracks | Cap | Zeroed by `reset_execution()` | Zeroed by `reset_phase()` |
|---|---|---|---|---|
| `reviewer_retries` | Genuine LLM rejection (ROUTE_EXECUTOR, ROUTE_PLANNER, ROUTE_ESCALATE) | 3 passes | ✓ | ✓ |
| `reviewer_infra_retries` | INFRA_FAILURE + model healthy (soft retry) | 3 | ✗ preserved | ✓ |
| `reviewer_infra_recovery_attempts` | INFRA_FAILURE + model unhealthy + within cooldown | 2 | ✗ preserved | ✓ |

`reviewer_infra_retries` and `reviewer_infra_recovery_attempts` are preserved across `reset_execution()` because executor retries do not fix an infrastructure problem. They are zeroed by `reset_phase()` because a phase reset constitutes a clean slate for all attempt budgets.

### Heartbeat Model Decision (B7, 2026-03-03) — Requires Main Machine Endpoint

- The heartbeat cron now queries local llama-server at `http://<llama-server-host>:11434` (configurable via `AUTODEV_LLAMA_BASE`) for a RESUME/WAIT/NOTIFY decision when the orchestrator lock is free. **This requires Main Machine Plan A Phase 4 (llama-server endpoint) to be complete and running.** Until the endpoint is confirmed, the heartbeat will send a Signal notification ("local model unreachable") rather than silently failing or restarting blindly. The conservative fallback ensures the cron never makes an unguided restart decision.
- The model's system prompt is deliberately narrow: output exactly one token (RESUME / WAIT / NOTIFY). Unexpected output is treated as NOTIFY. The model is not asked to diagnose the pipeline — only to classify the state.

### ~~Stale Model Alias Cleanup~~ ✓ Resolved 2026-02-25

Removed from `agents.defaults.models`: `anthropic/claude-haiku-4-5` (alias: orchestrator), `anthropic/claude-sonnet-4-5` (alias: cloud), `qwen-local/qwen3-coder-next` (alias: local). Only `anthropic/claude-sonnet-4-6` (alias: sonnet, cacheRetention: short) remains.

### ~~`agents.defaults.workspace` Removal~~ ✓ Resolved 2026-02-25

`agents.defaults.workspace` has been removed from `openclaw.json`. All four agents retain explicit per-agent workspace declarations. PIPELINE-SPEC §9 constraint is now enforced at the config level.

---

## 5. Design Rationale Archive

Decisions made explicitly — not defaults to drift from. Preserved for human reference.

### Signal over Pushover / WhatsApp / Telegram

- **Signal chosen deliberately:** end-to-end encrypted, self-sovereign, already in use.
- **Pushover ruled out:** too basic, lacks E2E encryption.
- **WhatsApp ruled out:** privacy concerns (Meta data practices).
- **Telegram not selected:** Signal preferred.

### Webhook over Subagent

- Agents triggered via `POST /hooks/agent` — not spawned as subagents via `sessions_spawn`.
- `sessions_spawn` is unreliable: non-blocking spawn + LLM must maintain poll loop.
- Webhook POST is deterministic orchestrator-controlled invocation.

### Fresh Session over History Injection

- Each agent attempt gets a fresh session (new key) — no prior context loaded.
- Reviewer-rejection retries inject only targeted `blocking_issues`, not full session history.
- Rationale: full history creates context pressure on local model (Qwen3's 96K window) across multi-phase runs. Targeted injection gives executor exactly what it needs without the noise.
- Failed-to-complete retries get zero history — partial work is noise that risks anchoring the next attempt on a broken path.

### Separate Workspace per Agent (not Shared)

- Each agent gets its own `AGENTS.md`, `SOUL.md`, etc. — different behavioral constraints per role require different files.
- Shared project files (JSON outputs, sentinels, source code) live at a separate path via symlink — not in any agent workspace.
- Config set once per agent, never touched again.

### Sentinel Pattern over JSON Watch

- File writes are not atomic — orchestrator could read a partially written JSON.
- Sentinel (`.done` file) written after JSON provides an atomic signal.
- Soft dependency on agent instruction-following — requires careful `AGENTS.md` guidance, but deliverable is explicit and testable.

### No Messaging Channel Routing

- Webhook only for agent invocation. Signal used exclusively for human escalation notifications.
- Messaging channels (Signal, WhatsApp, Telegram) are not used for agent invocation.

### Memory/Vector Index Disabled

- Disabled for all pipeline agents by design.
- Fresh context per phase — agents read explicit JSON files, not memory search.

### Git Timing Strictness

- No git operations during agent turns — agents write only to shared workspace.
- Git operations happen only after reviewer gate passes.
- Rationale: agents should not see git state changes during their work; the workspace is their contract boundary.

### Merge Conflict Policy

- Merge conflict → escalation agent immediately; do not attempt auto-resolve.
- Rationale: auto-resolution risks silently corrupting code that passed review.

### Archive Failure Policy

- Archive failure (phase snapshot copies under `$OPENCLAW_ROOT/pipeline-audit/` or `AUTODEV_AUDIT_ARCHIVE_DIR`) is non-blocking.
- Rationale: losing audit data is preferable to halting a working pipeline. Archive is informational, not structural.

### JSON Parse = Structural Validation Failure

- Unparseable JSON treated identically to structural validation failure — same retry counter, same branch logic.
- Rationale: one occurrence is a plausible fluke; repeated occurrences indicate something systemic (bad tool-call format, model degradation, context issue) — escalation timer handles this naturally.

### Local-First Design Intent and Current Production Configuration

**Original design intent:** Local-first inference for all agents using llama.cpp on local hardware, with no cloud dependencies in the execution path.

**Current production configuration:** Cloud inference via OpenRouter — planner on MiniMax M2.7 (`openrouter/minimax/minimax-m2.7`); executor and reviewer on Kimi K2.6 / Moonshot AI (`openrouter/moonshotai/kimi-k2.6`). Local inference (Qwen3.6-27B, llama.cpp) retained for the escalation agent. This cloud-first configuration was adopted after smoke testing confirmed local inference latency was not viable for the development loop and direct cloud API costs were prohibitive. (`openclaw.json` is the live source of truth; the local-model registry and dated incident notes below describe the preserved local-first infrastructure and historical model states.)

The local inference infrastructure is intentionally preserved. Returning to local-first operation requires only updating agent LLM config entries and re-enabling the SSH recovery path — no structural changes needed.

### External Dependencies (Current Production)

OpenRouter is a runtime dependency for planner, executor, and reviewer agents. The escalation agent has no external dependency — it runs entirely on local llama-server.

Required configuration values in `openclaw.json`:
- `models.providers.openrouter.apiKey` — OpenRouter API key
- `models.providers.openrouter.baseUrl` — OpenRouter base URL (`https://openrouter.ai/api/v1`)
- `models.providers.llama-local.apiKey` — must be set to `"no-key"` (see §2 > `apiKey: "no-key"` Required)

### `fcntl.flock` over PID-based Lock Checking

- Raw PID checking is unsafe across reboots. After a Pi reboot, the OS resets the PID counter and an unrelated process may receive the orchestrator's old PID. The heartbeat cron would see the PID as "alive" and never attempt recovery — a silent permanent halt.
- `fcntl.flock` (POSIX advisory locking) is immune: the OS drops the lock on process death or reboot. Heartbeat tests liveness by attempting the lock, not by checking PIDs.
- PID + timestamp are still written to the lock file as diagnostic metadata, but are never used for liveness decisions.

### Blocked State Design

- See PIPELINE-SPEC.md § Error Classification > Roadmap Checkbox States for behavior. Rationale: even if `[!]` blocked states are rare, programmatic detection is worth the cost — false alarms are informative signals worth investigating.

---

## 5.1 Heartbeat False Positive — Model Down While Pipeline Idle (2026-03-09)

**Symptom:** Heartbeat cron fired Signal alerts at 11:00 AM and 11:30 AM saying "local model unreachable, manual check required" even though no phase was actively running (pipeline was in `HALTED_SILENT` with no work in flight).

**Root cause:** `heartbeat_cron.py` sent the model-unreachable Signal notification unconditionally on `ConnectionError`, regardless of `pipeline_status`. The guard only needed the model for a RESUME/WAIT/NOTIFY routing decision — but if the pipeline is already halted or waiting for human, model reachability is irrelevant.

**Fix (2026-03-09):** Added `model_alert_required` flag before the model query:
```python
pipeline_status = state.get("pipeline_status", "")
model_alert_required = pipeline_status in ("RUNNING", "WAITING_FOR_SENTINEL")
```
`ConnectionError` and general exception handlers now only call `send_signal_notification()` when `model_alert_required` is `True`. For `HALTED_SILENT`, `WAITING_FOR_HUMAN`, `BLOCKED`, `PIPELINE_COMPLETE`, and `STOPPED` states, the failure is logged locally and the heartbeat returns silently.

**Rule:** Only alert on model unreachability when a phase is actively in flight.

> **`STOPPED` state (added 2026-03-18):** `STOPPED` is a new terminal state written when the operator requests a clean halt via the UI stop button (`POST /api/stop` writes `pipeline_stop_requested` sentinel; orchestrator's `_check_stop_requested()` consumes it and transitions to `STOPPED`). The heartbeat cron's B7 model decision treats `STOPPED` as `WAIT` — identical to `PIPELINE_COMPLETE`. `model_alert_required` is `False` for `STOPPED` by definition (the pipeline is not actively running). See PIPELINE-SPEC.md §2 > State Machine and §14 > UI Server API Reference.

---

## 5.2 ~~Escalation Agent Wrote Invalid Command → HALTED_SILENT~~ ✓ Resolved 2026-06-01 (incident 2026-03-09)

**Symptom:** Pipeline went to `HALTED_SILENT` during CORE-4 escalation. Subsequent RESET_EXECUTION and RESET_PHASE commands sent by the human via Signal were written to `escalation_output.json` by the escalation agent but never consumed — the orchestrator was already dead.

**Root cause:** The escalation agent wrote `{"command": "WAITING_FOR_HUMAN"}` as its command response. `WAITING_FOR_HUMAN` is a **pipeline state**, not a valid resume command. The valid commands are: `RETRY`, `RESET_PHASE`, `RESTART PHASE`, `RESET_EXECUTION`, `SKIP`, `PROCEED`, `STOP`. The orchestrator's command dispatch hit the `else` branch (line ~1080): `transition_state("HALTED_SILENT", "Stopped via command WAITING_FOR_HUMAN")` and then exited the main loop. Subsequent escalation files had no running process to consume them.

**Fix (2026-03-09):** Manual recovery — patched `pipeline_state.json` back to `RUNNING` with `current_agent=planner`, cleared stale `escalation_output.*` files, reset phase/pipeline state counters, and restarted the orchestrator to resume CORE-4 from the planner.

**Fix (shipped 2026-06-01):** Both halves of the recommended fix landed. **Orchestrator consumer** — the `else` branch no longer transitions to `HALTED_SILENT` or marks the queue entry `FAILED`. It now emits a `[WARN]` log + an `escalation_command_invalid` pipeline event (`{received_command, defaulted_to: "STOP"}`) and defaults to `STOP` — recoverable via the Resume control, and consistent with the sibling fallbacks (`_apply_pending_escalation_command`, JSON-parse failure) that already default to `STOP`. **Agent contract** — the escalation webhook default message (`webhook_client.py`) now names the `command` field and enumerates the offerable verbs (`RETRY`, `RESET_PHASE`, `RESET_EXECUTION`, `RESET_REVIEWER`, `PROCEED`, `STOP`) with `{"command": "STOP"}` as the no-instruction default; `AGENTS.md` adds a never-write-`.done`-without-a-valid-`command` rule. `WAITING_FOR_HUMAN` (the original incident value) now defaults to `STOP` instead of dead-ending. Guarded by `autodev/tests/test_orchestrator_escalation_invalid_command.py` and the new `test_webhook_client_default_messages.py` cases.

---

## 5.3 ~~Git Checkout / Reset Overwrites `current_phase.json` and `planner_output.json`~~ ✓ Resolved 2026-03-10

**Symptom:** After `reset_phase()`, `reset_execution()`, or orchestrator startup, `current_phase.json` (and on executor retries, `planner_output.json`) contained stale data from a previously completed phase. The planner received CORE-1 context while CORE-4 was active; executor retries received the CORE-1 plan.

**Root cause:** Three git operations overwrite committed (stale) versions of these files:
1. **`reset_phase()` path:** `git checkout main` restores the committed `current_phase.json`.
2. **Startup path:** `git checkout phase/CORE-N` restores the committed `current_phase.json` when resuming mid-phase.
3. **`reset_execution()` path:** `git reset --hard HEAD` on the phase branch restores both `current_phase.json` AND `planner_output.json` (the executor's working plan) to the committed version.

**Primary fix (2026-03-10):** All pipeline metadata files added to `.gitignore` in project repos. `current_phase.json`, `planner_output.json`, `phase_state.json`, all `*_output.json`, all `*.done` sentinels are now excluded from git tracking. Since these files are never committed, `git checkout` and `git reset --hard HEAD` cannot overwrite them. The executor's plan and phase context survive resets intact.

Template `.gitignore` entry (must be present in every pipeline project — include in INFRA-1 phase setup):
```
# Pipeline metadata — orchestrator-managed per-turn state, never committed
*.done
phase_state.json
planner_output.json
executor_output.json
reviewer_output.json
escalation_output.json
failure_context.json
current_phase.json

# mkstemp atomic-write temp files (8-char hex suffix from tempfile.mkstemp)
pipeline_state_????????
phase_state_????????
current_phase_????????
```

`failure_context.json` was added in the Phase 1–6 enhancement pass (failure context artifact). The three mkstemp patterns (`????????` matches the 8-character hex suffix produced by `tempfile.mkstemp`) catch stranded temp files if the orchestrator is killed mid-atomic-write. Canonical filenames are not matched by the wildcard. The startup `cleanup_stranded_temp_files()` call handles `~/.openclaw/` directly; these `.gitignore` entries prevent them from appearing as untracked files in `git status` within the project directory.

**Defensive backstop (2026-03-10):** `roadmap_parser.py` subprocess call added after each of the three git operations in `orchestrator.py`. If a project's `.gitignore` is missing these entries, the roadmap_parser refreshes `current_phase.json` regardless. Wrapped in try/except; non-blocking on failure.

**Gate enforcement (2026-03-10):** `repo_init_check.py` (runs at every pipeline startup) now verifies that all required pipeline metadata entries are present in the project's `.gitignore`. Any missing entries are auto-injected rather than hard-failing — this is self-healing for existing projects created before this fix. New projects should include these entries from INFRA-1 (see Dev Roadmap template).

---

## 5.4 Pre-Run Reset Procedure Gap — `~/.openclaw/pipeline_state.json` Not Cleared (2026-03-09)

**Symptom:** On the first fresh-start attempt of the Phase 14 E2E test, the orchestrator immediately resumed a prior run's state (reviewer_retries: 4, Attempt 5) instead of starting clean.

**Root cause:** The pre-run cleanup procedure cleared pipeline working files from `pipeline-project/` (current_phase.json, phase_state.json, planner/executor/reviewer output pairs) but did not clear `~/.openclaw/pipeline_state.json` — the main orchestrator state file stored outside the project directory. On restart, the orchestrator loaded the stale state and resumed mid-phase.

**Fix:** Always include `~/.openclaw/pipeline_state.json` in the pre-run cleanup:
```bash
rm -f ~/.openclaw/pipeline_state.json ~/.openclaw/pipeline.lock
```

---

## 5.5 Executor HTTP 500 on Complex Integration Phases — Context Window (2026-03-10)

**Symptom:** CORE-4 (game loop integration — touching 5+ source files simultaneously) exhausted all 5 executor retries. Error: `"500 Server Error: Internal Server Error for url: http://<llama-server-host>:11434/v1/chat/completions"`. Executor writes partial files but never produces a valid `executor_output.json` sentinel.

**Root cause:** llama-server returns HTTP 500 when the request context exceeds its server-side `--ctx-size`. CORE-4 executor prompt (all prior module source + planner plan + test suite) exceeded qwen3-coder-next's then-current 32K server limit. The server crashed mid-response, leaving the sentinel unwritten.

**Resolution (2026-03-10):** qwen3-coder-next server-side context bumped from 32K → 64K (65536 tokens) on the Main machine. This directly resolves the CORE-4 OOM condition — the integration phase prompt fits within 64K. If a future phase with a larger codebase hits this again, `--ctx-size` can be increased further to 97K (VRAM headroom exists on the 48GB card with Q3_K_XL quant).

**Active mitigation:** The pipeline deliberately keeps local model as the primary executor (cloud is last resort). If context pressure returns, the lever is `--ctx-size` on the llama-server, not routing logic changes.

---

## 5.6 Executor `NO_REPLY` — Root Cause and Prevention (2026-03-10)

**Symptom:** All executor attempts using qwen3.5-27b responded with `"NO_REPLY"` in 3 tokens (stop reason: `"stop"`, ~7 seconds), never writing any output files. Session log confirmed: input 7,577 tokens, output 3 tokens — `NO_REPLY` — immediately on receiving the webhook invocation.

**What NO_REPLY is:** The model itself outputs the text `"NO_REPLY"` as its first token; OpenClaw's delivery layer recognizes this and suppresses the response. The OpenClaw system prompt includes a NO_REPLY hint for background/housekeeping turns. Models that follow OpenClaw's behavioral conventions use this to signal "this turn requires no visible output."

**Full root cause — three conditions combined:**
1. **`[cron:... Hook]` message prefix** — ALL OpenClaw webhook deliveries are framed this way, regardless of `wakeMode`. The prefix is structural to the platform's delivery mechanism; it cannot be changed from the orchestrator side.
2. **OpenClaw's NO_REPLY system prompt hint** — the platform injects a hint that agents should use NO_REPLY for background/housekeeping turns.
3. **`USER.md` text: "They will not see your work unless escalation occurs"** — a model reading this alongside the cron-hook framing correctly concludes: operator absent + cron delivery = background task → NO_REPLY. The model was not malfunctioning; it followed its instructions accurately.

**Primary prevention fix (2026-03-10):** Executor `USER.md` updated to explicitly suppress NO_REPLY and clarify the foreground nature of the task. The added paragraph reads: *"You are a foreground implementation task. Never output NO_REPLY. This message is delivered via a webhook hook event — that framing is a platform delivery mechanism, not an indication that this is a background or housekeeping task. You MUST produce executor_output.json and executor_output.done on every invocation."*

This fix is **model-agnostic**: it applies to any current or future executor model that reads OpenClaw conventions, not just qwen3.5-27b.

**Secondary prevention:** Keep qwen3-coder-next as the executor model (already enforced). As a general-purpose coder model without OpenClaw convention fine-tuning, it doesn't follow the NO_REPLY hint. If a future model update changes this behavior, the USER.md instruction provides the backstop.

**OB-3 — Blame Attribution Has No Failure Context (open):** When a sentinel is never written (due to NO_REPLY or server OOM), the gate writes `executor_output.json` with `failure_reason: null`. Blame attribution receives null and returns "ambiguous." Note: with the USER.md fix in place, NO_REPLY should no longer occur. This OB-3 item is now primarily relevant to the server OOM (HTTP 500) path, where a model crashes mid-response. Fix direction: after a sentinel timeout, the orchestrator injects a synthetic `failure_reason` before running blame. Not yet implemented.

**OB-4 — Empty model completion causes 600s sentinel wait [RESOLVED 2026-03-10, updated 2026-03-13]:** Traffic cop can return an empty completion (0 tokens, `content: []`) rather than a NO_REPLY text response. The session JSONL immediately goes idle but the orchestrator waited the full 600s sentinel timeout before retrying (~540s wasted per occurrence). Root cause differs from NO_REPLY: the model returns a structurally valid but empty assistant message. Fix: `poll_for_sentinel_with_idle_detect()` added to `sentinel_poller.py`. Monitors executor session JSONL mtime alongside sentinel polling. If JSONL stops growing for `idle_threshold` seconds with no sentinel → early exit → existing `reset_execution("auto")` retry path fires. Session JSONL resolved from `sessions.json` via session key (up to 30s retry); falls back to original 600s polling if lookup fails.

**OB-4a — idle_threshold too short for batch-output models [RESOLVED 2026-03-13]:** MiniMax M2.5 (and similar models) batch their responses rather than streaming token-by-token. JSONL is only written at tool-call boundaries, not during reasoning. Inter-tool-call silence observed up to ~5 minutes on complex CORE phases — far exceeding the original 60s idle_threshold. This caused cascading `executor_crashed` / `executor_preempted` retry cycles: multiple concurrent orphaned sessions raced to write output, corrupting the retry logic and causing `ERR_VALIDATION_FAILED` escalation loops.

**Threshold evolution:** 60s (original) → 120s (first attempt, 2026-03-13, still too short for CORE-2+) → 360s (blunt fix, worked but too slow to detect genuinely hung sessions).

**Final fix (2026-03-14):** Two complementary changes to `sentinel_poller.poll_for_sentinel_with_idle_detect()`:

1. **`watch_dirs` parameter** — The idle clock now resets on ANY of: (a) JSONL mtime change, OR (b) any file write in the supplied watch directories. `orchestrator.py` passes `watch_dirs=[SYMLINK_TARGET]` so every code file, test file, or output JSON the executor writes resets the clock. `idle_threshold` restored to 120s — the only truly "dark" period is the model reasoning silently between tool calls with no file I/O (~30–90s for MiniMax M2.5). Genuinely hung sessions (no JSONL AND no file writes for 2 min) are still caught promptly.

2. **`min_sentinel_mtime` parameter** — A separate class of failure: orphaned sessions from prior attempts write a `.done` sentinel after the orchestrator has already run `git reset --hard` to clean the working tree. The new attempt's sentinel poll would accept this stale `.done`, the gate would fail (`ERR_MANIFEST_FILE_MISSING` — code files deleted by reset), and the retry budget would be consumed. Fix: `orchestrator.py` records `time.time()` immediately before `cleanup_output_files()` and passes it as `min_sentinel_mtime`. The poller discards any `.done` file whose mtime predates this value, removes it, and continues waiting for a fresh sentinel from the current session. Validated in E2E: all 6 phases completed with **0 executor retries** (28 min 24s total). Note: `session:end` OpenClaw hook (planned event type) would be the preferred long-term trigger — revisit when available.

**OB-5a — Escalation agent fires NO_REPLY on webhook invocation [RESOLVED]:** The escalation agent (`qwen3.5-27b`, local) has the same NO_REPLY susceptibility as the reviewer (see OB-5 below): OpenClaw injects `## Silent Replies → NO_REPLY` guidance, and the model misclassifies an active-task webhook as a housekeeping event. Unlike the reviewer, a prohibition cannot be added to `USER.md` without suppressing the legitimate NO_REPLY use case in escalation's Signal DM context. **Fix:** `workspace-escalation/IDENTITY.md` was updated with an explicit override at the top of the file: *"You are never a passive observer. When invoked via webhook, you always have a task. 'NO_REPLY' is never valid in this context regardless of system-level guidance at invocation."* The `IDENTITY.md` is loaded before any system prompt guidance, giving it priority. The NO_REPLY suppression applies only to webhook-triggered turns; Signal DM turns are not affected because the framing is different. Operators adding new identity files or modifying `IDENTITY.md` must preserve this override.

**OB-5 — qwen3.5-27b Reviewer fires NO_REPLY on webhook invocation [RESOLVED 2026-03-10]:** The reviewer agent (`qwen3.5-27b`, local) pattern-matches webhook/cron delivery framing as a passive event and immediately returns NO_REPLY without reading any task files. Root cause confirmed via model self-report: it sees the OpenClaw system-injected `## Silent Replies → NO_REPLY` guidance and misclassifies an active task webhook as a "nothing to say" scenario. Fix: added foreground task prohibition to `workspace-reviewer/USER.md` — mirrors the executor fix but with reviewer-specific deliverables (`reviewer_output.json` + `reviewer_output.done`) and consequence framing ("executor's work is never reviewed"). OpenClaw system prompt intentionally left unchanged: NO_REPLY remains valid for the escalation agent's conversational DM context and modifying it globally would require editing package files (update-fragile). Note: escalation agent is also `qwen3.5-27b` and has the same susceptibility — legitimate in DM context, so no prohibition added there.

---

## 5.7 Model Swap Race Condition — Executor → Reviewer Handoff [RESOLVED 2026-03-10]

**Symptom:** Executor gate passes, orchestrator fires reviewer webhook immediately. Traffic cop (llama-server on Main machine) begins evicting qwen3-coder-next and loading qwen3.5-27b. If the swap takes >10s, the router force-kills the outgoing model mid-eviction, returning HTTP 500. The orchestrator retries → triggers another swap → cascade. LLM generation cut-off appears clean (no crash log) but is an interrupted eviction.

**Root cause:** A single `time.sleep(5)` between executor gate pass and reviewer webhook was insufficient. GPU model swap can exceed 10s under load. No mechanism existed to detect when the swap had settled before proceeding.

**Fix (2026-03-10):** Replaced fixed sleep with `wait_for_model_stable()` method on the Orchestrator class (`orchestrator.py`, line ~188). Polls `GET {llama-base}/v1/models` every 5s (URL from `openclaw_config["models"]["providers"]["llama-local"]["baseUrl"]`, with fallback from env `AUTODEV_LLAMA_BASE`). Stable state = no model in a transitioning status (all entries `loaded` or `unloaded`). Timeout: 300s (proceeds anyway on expiry — no pipeline stall). Replaces the fixed sleep entirely — proceeds as fast as the GPU allows rather than waiting a fixed 60s.

---

## 5.8 OpenClaw Native Heartbeat Disabled [2026-03-10]

**Symptom:** OpenClaw's built-in heartbeat was firing the escalation agent (qwen3.5-27b) every 30 minutes regardless of pipeline state. This caused: (1) model swap interruptions — loading 27b on the traffic cop while qwen3-coder-next was mid-generation, triggering the race condition described in §5.7; (2) noisy Signal DMs — OpenClaw bundles hook errors from other agents (executor, reviewer) into the next heartbeat delivery, causing the escalation agent to act on stale error context.

**Root cause:** The heartbeat was OpenClaw's platform feature, entirely separate from our `heartbeat_cron.py`. Configured via `agents.defaults.heartbeat: {}` in `openclaw.json` (empty = 30m default). The escalation agent has `"default": true`, making it the heartbeat target.

**Fix (2026-03-10):** Set `agents.defaults.heartbeat.every: "0m"` in `openclaw.json`. Gateway restarted to apply. OpenClaw native heartbeat is now fully disabled.

**What remains:** `heartbeat_cron.py` (system cron, every 30 min) is the sole monitoring mechanism. It only queries qwen3.5-27b when the orchestrator lock is **free** (process dead) — zero GPU impact during active pipeline runs. This is the correct separation: OpenClaw heartbeat covered redundant ground with harmful side effects; our cron covers the one case that matters (dead orchestrator recovery).

---

## 5.9 SSH Recovery Interface — Reviewer INFRA_FAILURE Handler (RR-1, 2026-03-12)

**What this is:** When the reviewer gate returns `INFRA_FAILURE` AND `check_traffic_cop_health()` reports the model is unhealthy, the orchestrator can attempt automated recovery by invoking the llama-server restart script on the Main machine via SSH.

**SSH invocation contract:**

```python
subprocess.run(
    ["ssh", "-i", key_path, "-o", "StrictHostKeyChecking=no",
     "-o", "ConnectTimeout=10",
     f"{user}@{host}", "recovery"],
    timeout=70, check=False
)
```

Parameters are read from `openclaw.json` under the `recovery` section:

```json
"recovery": {
  "user": "deploy",
  "host": "gpu-host.example.com",
  "key_path": "/path/to/ssh_recovery_key"
}
```

**Exit code semantics (server-side `command=` restriction ignores the argument; runs `restart_llama.sh` unconditionally):**

| Exit Code | Meaning | Orchestrator Action |
|---|---|---|
| 0 | Recovery succeeded — llama-server restarted | `wait_for_model_stable()` → re-invoke reviewer |
| 1 | Recovery failed — restart script returned error | Route to escalation |
| 2 | Skipped — model was already healthy when script ran | `wait_for_model_stable()` → re-invoke reviewer (treat as success) |
| Timeout (70s) | SSH hung | Treat as exit 1 → escalation |

**Cooldown enforcement:** The orchestrator writes `reviewer_infra_recovery_attempted=True` and `reviewer_infra_recovery_timestamp` to `phase_state.json` atomically BEFORE invoking SSH. A 10-minute cooldown (600s) prevents repeated recovery attempts if the restart keeps failing. Within cooldown: increment `reviewer_infra_recovery_attempts` (cap 2); at cap → escalation.

**Key path requirement:** The file at `recovery.key_path` must have `600` permissions and authorize SSH access only to `recovery.user@recovery.host` for the forced command that runs the restart script. The `command=` restriction in `authorized_keys` ensures the recovery key cannot be used for general shell access.

---

## 6. Pre-Phase-14 Remediation Log

Post-Phase-13 audit identified 6 critical findings and 4 warnings. Remediation completed in three layers:

- **Layer 1 (Code Bugs):** Reviewer gate wired into orchestrator event loop (was disconnected, caused `NameError` crash). Executor gate now cross-references `tests_written` against planner's `tdd_test_structure`. Top-level exception handler added (unhandled exceptions trigger escalation or `HALTED_SILENT`, not crash loops). Retry double-count fixed — gate scripts record error codes only, orchestrator owns the single retry increment.

- **Layer 2 (Config Drift):** Escalation tool policy corrected (write access retained for sentinel pattern, all other write-adjacent tools denied). Memory/vector search disabled. Session key prefix restriction added. Test agent removed. Primary model corrected from `claude-haiku-4-5` to `claude-sonnet-4-6`. Two spec bugs identified: `defaultSessionKey` prefix violation and escalation write policy incompatibility.

- **Layer 3 (Agent Workspaces):** All 20 agent workspace files written or rewritten. Planner stripped of chat-assistant boilerplate. Reviewer written from scratch (was 0 bytes). Executor expanded from 55 words to full behavioral contract. Escalation enhanced with missing items. All files use workspace-relative `pipeline-project/` paths.
