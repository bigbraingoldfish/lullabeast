# AutoDev / AutoDev UI glossary

Short reference for operators and contributors. Install and networking details stay in [SETUP.md](SETUP.md). Deep pipeline rules are in `autodev/docs/PIPELINE-SPEC.md`.

**UI labels vs enums.** The dashboard maps internal enums to pill labels in `ui/index.html` (`PIPELINE_LIVE_PILL` for live `pipeline_status`, `queueOnlyRowPill` / `queueRowDisplay` for queue rows). This document lists both where they differ.

---

## OpenClaw vs AutoDev

| Piece | Role |
| ----- | ---- |
| **OpenClaw** | External runtime: gateway (e.g. `localhost:18789`), `openclaw.json`, agent workspaces under **`OPENCLAW_ROOT`** (`workspace-planner`, `workspace-executor`, …), sessions, webhooks. AutoDev does not embed it; the orchestrator POSTs to OpenClaw to wake agents. |
| **Orchestrator** | Python process in **this repo**: `autodev/pipeline/orchestrator.py`. Reads/writes pipeline lock and state under **`AUTODEV_PIPELINE_ROOT`** (default `<repo>/.autodev/`). Uses the **`pipeline-project`** symlink to reach the target git repository. |
| **AutoDev UI** | FastAPI app in `ui/server.py` (default port **18790**). Serves the React dashboard and **`/api/*`** routes. **No authentication** on `/api` — bind to loopback on trusted machines (see SETUP **Security and network exposure**). |
| **This repository** | Contains pipeline code, UI, skills, agent identity sources, and tests. **`AUTODEV_REPO_PATH`** should point at this clone for a correct install. |

---

## Pipeline states

Orchestrator-valid values are `VALID_STATES` in `autodev/pipeline/orchestrator.py`. The UI and `GET /api/state` may also show **`IDLE`** or **`UNKNOWN`** when no state file exists yet or the status cannot be read — those are not entries in `VALID_STATES`.

| `pipeline_status` (enum) | Typical UI pill / header | Meaning (short) | What usually happens next |
| ------------------------ | ------------------------ | --------------- | --------------------------- |
| `RUNNING` | **RUNNING** (pulsing) | Planner/executor/reviewer leg active. | Work continues until sentinel, human wait, or failure. |
| `WAITING_FOR_SENTINEL` | **Running** or **Running {Agent}** (`formatWaitForSentinelLabel` from `current_agent`) | Waiting on agent output / sentinel file. | Advances when gate passes or retries. |
| `WAITING_FOR_HUMAN` | **NEEDS YOUR INPUT** | Escalation / human decision path. | Use escalation command panel or external channel per your setup. |
| `HALTED_SILENT` | **INTERVENTION REQUIRED** | Bad terminal; escalation path failed or unrecoverable error path. | Inspect orchestrator logs and project `escalation_failed.json` if present. |
| `BLOCKED` | **BLOCKED** | Pipeline blocked (gate or policy). | Resolve gate output / state; may need reset or manual fix. |
| `PIPELINE_COMPLETE` | **COMPLETE** | Roadmap finished successfully. | New run or queue advance per configuration. |
| `STOPPED` | **STOPPED** | Clean operator halt (e.g. stop sentinel / UI). | Resume or reset from UI when ready. |
| `QUEUE_HALTED` | **Queue stalled** | Queue runner stopped: no runnable entries (blocked, dependency hold, preflight, or empty). | Fix queue rows; see toast reason (`all_blocked`, `all_dependency_hold`, `mixed`, `all_completed`). |
| `IDLE` | **IDLE** | No run in progress (UI reading). | Open **Project Ideas** or **Setup & Preflight** to start. |
| `UNKNOWN` | **UNKNOWN** | Status could not be determined. | Check `pipeline_state.json` path and server logs. |

---

## Queue entry states

Each row in `pipeline_queue.json` has a `state`. Labels below are the queue-only or live pills from `ui/index.html` (live `pipeline_status` from the active project overrides several pills when present).

| `state` (enum) | Typical pill label | Meaning (short) | What clears or advances it |
| -------------- | -------------------- | --------------- | --------------------------- |
| `READY` | **READY** | Eligible to run when selected and dependencies met. | **`Run next project`** / auto queue advance; may become `ACTIVE`. |
| `ACTIVE` | **ACTIVE** (or live pipeline pill when `live_pipeline_status` set) | This row is the current queue slot for a project path. | Completes to `COMPLETED` / `FAILED`, or demoted when stale. |
| `BLOCKED` | **QUEUE BLOCKED** | Row blocked (dependency / gate / policy). | Operator fixes underlying issue; may return to `READY` after revalidation. |
| `SKIPPED_PENDING` | **Preflight failed** | Preflight failed for this path or a cascaded descendant; not removed from queue. | Preflight passes again (often after fixing repo); can return to `READY`. |
| `DEPENDENCY_HOLD` | **Waiting on parent** | Parent project not `COMPLETED`. | Completing parent or clearing parent id. |
| `ESCALATION` | **ESCALATION** | Parked for human escalation flow on this entry. | Commands / pipeline progression per server rules. |
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

---

## Skill injection

- Each planner / executor / reviewer invocation can receive **one** skill file copied into **`OPENCLAW_ROOT/workspace-{role}/skills/{discipline}-{role}/SKILL.md`**, sourced from `autodev/skill-library/{discipline}/{role}/SKILL.md`.
- The roadmap phase id prefix (e.g. `CORE` from `CORE-E2`) maps to a discipline directory via **`autodev/config/skill_mapping.yaml`**. Unmapped prefixes skip injection.
- Workspace skills are **wiped and recreated** before each injection so no stale skill carries between phases.
- Orchestrator logs lines like **`[SKILL] Status=none_mapped`** when the subsystem has no YAML mapping, skills are disabled in `openclaw.json`, the skill file is missing, PyYAML is missing, or similar — **not necessarily an error**; it means that phase ran without an injected skill file.
