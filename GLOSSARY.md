# Lullabeast / Lullabeast UI glossary

Short reference for operators and contributors. Install and networking details stay in [SETUP.md](SETUP.md). Deep pipeline rules are in `autodev/docs/PIPELINE-SPEC.md`.

**UI labels vs enums.** The dashboard maps internal enums to pill labels in `ui/index.html` (`PIPELINE_LIVE_PILL` for live `pipeline_status`, `queueOnlyRowPill` / `queueRowDisplay` for queue rows). This document lists both where they differ.

---

## OpenClaw vs Lullabeast

| Piece | Role |
| ----- | ---- |
| **OpenClaw** | External runtime: gateway (e.g. `localhost:18789`), `openclaw.json`, agent workspaces under **`OPENCLAW_ROOT`** (`workspace-planner`, `workspace-executor`, …), sessions, webhooks. Lullabeast does not embed it; the orchestrator POSTs to OpenClaw to wake agents. |
| **Orchestrator** | Python process in **this repo**: `autodev/pipeline/orchestrator.py`. Reads/writes pipeline lock and state under **`AUTODEV_PIPELINE_ROOT`** (default `<repo>/.autodev/`). Uses the **`pipeline-project`** symlink to reach the target git repository. |
| **Lullabeast UI** | FastAPI app in `ui/server.py` (default port **18790**). Serves the React dashboard and **`/api/*`** routes. Access requires the **`AUTODEV_UI_TOKEN`** dashboard token (browser cookie via the startup URL, or `Authorization: Bearer` for scripts) — still bind to loopback on trusted machines (see SETUP **Security and network exposure**). |
| **This repository** | Contains pipeline code, UI, skills, agent identity sources, and tests. **`AUTODEV_REPO_PATH`** should point at this clone for a correct install. |

**A note on the three names you'll meet.** *Lullabeast* is the product/brand. *autodev* is the
internal name — it surfaces as the repo directory (`autodev-ui`), the package path (`autodev/…`),
and the **`AUTODEV_`** environment-variable prefix; that prefix is legacy, so newer feature-scoped
knobs are intentionally **unprefixed** (e.g. `PREREQ_PROBE_TIMEOUT`). *OpenClaw* is the external
runtime Lullabeast drives, not part of this codebase. So the three are layers — brand / internal +
env / external dependency — not competing names for the same thing.

---

## Doctor

The one-command health check for a Lullabeast install (`python -m autodev.installer.doctor`, also served at `GET /api/doctor` and run automatically as `install.sh`'s final gate). It probes every documented silent-failure mode (paths, the PRD conversion prompt, `openclaw.json` health, OpenClaw version floor, gateway, plugin bundle freshness, webhook secrets, `pipeline-project` symlink agreement, stale locks, Playwright, tokens, ports) and prints a green/red checklist with a one-line fix per red item. Strictly read-only. Exit codes: 0 all ok, 1 any failure, 2 warnings only. `--live` adds the webhook ping (creates a real OpenClaw session, so it is opt-in). See SETUP **Installation** for the full description.

**Health card**: the dashboard rendering of the same report, on the **Setup & Preflight** screen. It loads once when the screen opens and shows each check with its status and, for anything amber or red, the fix hint. Read-only: it has no re-run button and never triggers the live webhook ping.

**Installer modes** (`install.sh`): **guest** (default; non-destructive, prompt-driven, shared-host etiquette), **`--strict`** (non-interactive; a failing doctor exits 1), **`--owned-openclaw`** (the container mode: the script owns the OpenClaw tree, overwrites unconditionally, never prompts, and treats any warning as a fatal error).

---

## Pipeline states

Orchestrator-valid values are `VALID_STATES` in `autodev/pipeline/orchestrator.py`. The UI and `GET /api/state` may also show **`IDLE`** or **`UNKNOWN`** when no state file exists yet or the status cannot be read — those are not entries in `VALID_STATES`.

| `pipeline_status` (enum) | Typical UI pill / header | Meaning (short) | What usually happens next |
| ------------------------ | ------------------------ | --------------- | --------------------------- |
| `RUNNING` | **RUNNING** (pulsing) | Planner/executor/reviewer leg active. | Work continues until sentinel, human wait, or failure. |
| `WAITING_FOR_SENTINEL` | **Running** or **Running {Agent}** (`formatWaitForSentinelLabel` from `current_agent`) | Waiting on agent output / sentinel file. | Advances when gate passes or retries. |
| `WAITING_FOR_HUMAN` | **NEEDS YOUR INPUT** | Escalation / human decision path. | Use escalation command panel or external channel per your setup. |
| `HALTED_SILENT` | **INTERVENTION REQUIRED** | Bad terminal; escalation *delivery* failed (all fallbacks exhausted). An invalid resume command now defaults to STOPPED, not here. | Inspect orchestrator logs and project `escalation_failed.json` if present. |
| `BLOCKED` | **BLOCKED** | Pipeline blocked (gate or policy). | Resolve gate output / state; may need reset or manual fix. |
| `PIPELINE_COMPLETE` | **COMPLETE** | Roadmap finished successfully. | New run or queue advance per configuration. |
| `STOPPED` | **STOPPED** | Clean operator halt (e.g. stop sentinel / UI). | Resume or reset from UI when ready. |
| `QUEUE_HALTED` | **Queue stalled** | Queue runner stopped: no runnable entries (blocked, dependency hold, preflight, or empty). | Fix queue rows; see toast reason (`all_blocked`, `all_dependency_hold`, `mixed`, `all_completed`). |
| `IDLE` | **IDLE** | No run in progress (UI reading). | Open **Project Ideas** or **Setup & Preflight** to start. |
| `UNKNOWN` | **UNKNOWN** | Status could not be determined. | Check `pipeline_state.json` path and server logs. |

---

## Queue entry states

Each row in `pipeline_queue.json` has a `state`. Labels below are the queue-only or live pills from `ui/index.html` (live `pipeline_status` from the active project overrides several pills when present). The Queue screen renders entries as a flat table with status filter chips (`running` / `attention` / `queued` / `complete`); pill labels are unchanged — the chips are display buckets, not states.

| `state` (enum) | Typical pill label | Meaning (short) | What clears or advances it |
| -------------- | -------------------- | --------------- | --------------------------- |
| `READY` | **READY** | Eligible to run when selected and dependencies met. | **`Run next project`** / auto queue advance; may become `ACTIVE`. In `queue_mode=auto` with an **idle** pipeline it auto-starts immediately (server `_maybe_autostart_queue`); in manual mode it waits for `Run next project` or the manual→auto toggle. |
| `ACTIVE` | **ACTIVE** (or live pipeline pill when `live_pipeline_status` set) | This row is the current queue slot for a project path. | Completes to `COMPLETED` / `FAILED`, or demoted when stale. |
| `BLOCKED` | **QUEUE BLOCKED** | Row blocked (dependency / gate / policy). | Operator fixes underlying issue; may return to `READY` after revalidation. |
| `SKIPPED_PENDING` | **Preflight failed** | Preflight failed for this path or a cascaded descendant; not removed from queue. | Preflight passes again (often after fixing repo); can return to `READY`. |
| `DEPENDENCY_HOLD` | **Waiting on parent** | Parent is in a *blocking* state (`BLOCKED`/`ESCALATION`/`ESCALATION_ANSWERED`). A non-blocking but still-incomplete parent (`READY`/`ACTIVE`) keeps the child `READY` — the dashboard still shows it *Waiting for parent*. | Completing the parent or clearing the parent id. |
| `ESCALATION` | **ESCALATION** | Parked for human escalation flow on this entry. | Commands / pipeline progression per server rules. |
| `ESCALATION_ANSWERED` | **Answer banked** | Parked escalation whose operator answer is saved (`pending_escalation_command.json`); resumes automatically when the queue reaches it. An un-promoted `ESCALATION` row with a banked answer shows the same pill. | Queue revival applies the banked command; **Resume banked answer** / **Resume now** relaunches immediately. |
| `COMPLETED` | **COMPLETED** | Project finished successfully in queue terms. | Terminal for that row. |
| `FAILED` | **FAILED** | Failed after retries / terminal failure. | Terminal; fix project and re-add or reset per operator workflow. |

---

## Git branch layout

- Work on phases is typically done on branches named like **`phase/<N>`** (orchestrator / pipeline convention; exact naming follows your project’s git workflow).
- Optional **`base_branch`** in `ui/config.json` overrides the default branch used for checkout/recovery heuristics when set.
- **Recover Git** in the UI calls `POST /api/pipeline/git-recover`: **stash including untracked**, then **`git checkout`** — not **`git reset`**. Prefilled branch comes from **`GET /api/state`** → `git_recover_suggested_branch`. See SETUP **Pipeline Monitor: git checkout recovery**.

---

## PRD-agent metrics

| Concept | API / UI | Notes |
| ------- | -------- | ----- |
| **PRD readiness** | Score out of 10 in the Ideas UI (**PRD readiness:** … `/ 10`). | Agent rubric score; **8+** recommended before **Generate Roadmap** (see dashboard `title` on that strip). |
| **Roadmap confidence** | Display label for **`conversion_confidence`** from the API (field name unchanged). | Agent-estimated confidence before roadmap generation; commentary only. |
| **Alignment check** | **Run Alignment Check** | Long-running check (~tens of seconds to a few minutes); compares PRD vs roadmap direction; **commentary in thread only**, does not edit the PRD. |
| **Adversarial review** | **Run Adversarial Review** | Stress-tests the PRD for gaps; **commentary only**. |
| **Edit (PRD section)** | Per-section **Edit** button → `PUT /api/ideas/{id}/prd-section` | Rewrites one section of `prd_draft.md` directly, no agent turn. The agent is told on its next turn via a `[SYSTEM EVENTS]` breadcrumb so it doesn't revert your change. Refused (409) while an agent reply is in flight or when the section changed under you (offers **Reload section**). Manual edits get the same **Changed** badge / diff / **Revert** as agent edits — the snapshot replaces the previous agent-turn diff. |

---

## Retry counters vs escalation resets (D-08)

Three different counter families govern how many attempts an agent phase can use before the pipeline escalates or halts. They reset at different scopes and serve different purposes.

### Per-agent retry counters (`planner_retries`, `executor_retries`, `reviewer_retries`)

- Stored in `phase_state.json` under the respective key.
- **Auto-reset to 0 at the start of each new phase.** A retry consumed on Phase 1 does not carry into Phase 2.
- Incremented each time the orchestrator re-invokes that agent after a gate failure within the same phase.
- Typical max values are set in orchestrator logic (e.g. 3 executor retries before escalation triggers).
- Visible in the dashboard counter strip as **Executor retries: N/3** (and equivalents).

### Escalation reset counter (`escalation_resets`)

- Stored in `phase_state.json` under `escalation_resets`.
- **Cumulative for the entire lifetime of a phase** — it is NOT reset between escalation episodes within the same phase. Each time a RESET_PHASE, RESET_EXECUTION, or RESET_REVIEWER command is issued via the escalation panel, this counter increments by 1.
- Hard cap: **3 combined resets per phase**. Once `escalation_resets >= 3`, the Recover group (Reset Phase / Reset Execution / Re-run Reviewer) is replaced by a status message in the dashboard. Only PROCEED (Mark Complete), Abandon Phase, and Stop Pipeline remain available.
- The cap is intentional: if three structured recovery attempts have not resolved a phase, continued automated recovery is unlikely to succeed without operator investigation or a fundamental change to the project.
- Visible in the dashboard counter strip as **Escalation loops: N/3**.

### What happens at the cap

When `escalation_resets >= 3`, the escalation panel disables all three reset commands. The operator must either:
- **Mark Complete** (`PROCEED`) — force-advances the phase as if it succeeded (requires merge probe to pass first).
- **Abandon Phase** (`SKIP`) — marks the phase as skipped and lets the pipeline continue.
- **Stop Pipeline** — halts the pipeline cleanly for manual investigation.

There is no automatic way to increment the cap. It can be manually decremented by editing `phase_state.json` directly, which is an operator escape hatch for exceptional cases.

---

## Skill injection

- Each planner / executor / reviewer invocation can receive **one** skill file copied into **`OPENCLAW_ROOT/workspace-{role}/skills/{discipline}-{role}/SKILL.md`**, sourced from `autodev/skill-library/{discipline}/{role}/SKILL.md`.
- The roadmap phase id prefix (e.g. `CORE` from `CORE-E2`) maps to a discipline directory via **`autodev/config/skill_mapping.yaml`**. Unmapped prefixes skip injection.
- The escalation agent receives a **permanent workspace skill** at `OPENCLAW_ROOT/workspace-escalation/skills/escalation-summary/SKILL.md`, deployed by `install.sh`. It instructs the agent to write a structured `escalation_summary.json` to the project directory so the dashboard advisory block can show AI-generated context rather than the raw error code.
- Workspace skills are **wiped and recreated** before each injection so no stale skill carries between phases.
- Orchestrator logs lines like **`[SKILL] Status=none_mapped`** when the subsystem has no YAML mapping, skills are disabled in `openclaw.json`, the skill file is missing, PyYAML is missing, or similar — **not necessarily an error**; it means that phase ran without an injected skill file.

---

## Cost & token metrics

- **Where the numbers come from** — each completed phase writes one durable metrics row carrying per-role (`planner`/`executor`/`reviewer`) token counts and cost, summed from the OpenClaw session files of every attempt. Models that report no usage (typical for local models) produce zeros.
- **Zero-suppression** — every cost/token surface hides at 0, so local-model runs stay clean: a missing Cost card or token figure means "nothing reported," not "nothing happened."
- **Token total vs. class breakdown** — the headline token figure is `input + output + cache reads + cache writes`. Cache reads usually dominate it but bill far cheaper than fresh input; the "in X · out Y · cache Z" sub-lines and tooltips show that split.
- **Per-role splits** — the Pipeline Monitor's expanded phase rows split both cost *and* tokens by planner / executor / reviewer in the **By agent** card (tokens⇄$ toggle; the $ view hides when no cost was captured), alongside a **By token type** card. Run-level splits open from the roadmap header's **Total cost / Total tokens** pills.
- **Skill / model badges** — the expanded phase's Run Metrics header shows which skill was injected and which model(s) ran the phase (hover a model badge for the full provider id and the roles it served). Model capture starts with new runs; older phases show no badge.
- **Queue metric chips (METRICS column)** — each queue row shows quiet cost and token chips (totals only, e.g. `$1.20` / `11.4M tok`). Clicking a chip quick-opens that row's expansion on the **Cost & Tokens** tab.
- **Queue expansion tabs** — an expanded queue row has two views: **Overview** (status, activity, stat cards — Cost/Tokens cards link "view breakdown ›") and **Cost & Tokens**: total pills that expand by-agent / by-type split cards, and a **Spend by phase** table (sortable by timeline or top spend, tokens⇄$ toggle) where each row shows a share-by-agent bar and an outcome badge, and clicking a phase reveals its By agent / By token type breakdown.
- **Outcome badges** — per-phase results render as pills everywhere (spend table + completion report): **CLEAN** (passed first attempt), **RETRIED** (multiple executor attempts), **ESCALATED** (needed a human); hover for detail.
- **View report (COMPLETED rows)** — opens the project's full completion report (summary cards, per-phase table, `completion_report.md`) from the queue at any time, even after the queue has moved on to other projects.
- **`token_capture_warning`** (activity feed) — an attempt's token usage could not be read (its session file was missing), so that phase's token totals under-count. The phase's metrics row carries `token_capture_degraded: true` as the durable marker.

## Prerequisites & environment readiness

Full history + de-scope note live in an internal roadmap (not part of the public tree); operator walkthrough in SETUP.

- **Prerequisite** — a tool/SDK or environment-variable **name** a project declares it needs to build or be tested, in `verification.md`'s `## Prerequisites` block (`### Tools` / `### Environment`). Names, types, and purposes only — **never values**.
- **`### Tools`** — a documentation-only list of host tools the project needs. **Not checked or gated** — host-tool detection was removed (2026-06-16) because a reliable verdict from an arbitrary declared name isn't achievable and a false-positive block with no recourse is worse than nothing. Make sure your host has them before you run; a genuinely-missing tool surfaces when a phase fails.
- **`.env.example`** — the committed file Preflight materializes from the declared `### Environment` names (`# purpose` + blank `KEY=` lines, append-only, value-free). You copy it to your own `.env` and fill the values, which never leave your machine. Preflight also gitignores that real `.env` (so the per-phase `git add .` can't commit your secrets) while keeping `.env.example` trackable. Env vars are **not** a Preflight gate.
- **Mock-first verification** — the pipeline mocks paid/external APIs by default and accepts mocked / recorded / local-stub evidence as satisfying behavioral verification (DEC-6). A paid-API feature is built and verified without spending your provider budget; there is no live-paid call in the automated loop. Final live validation is the user's, with their own key.
