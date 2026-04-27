# AutoDev UI (`ui/`)

FastAPI server (`server.py`) and a single-file React app (`index.html`) for the pipeline monitor, Project Ideas, and Setup & Preflight screens.

## Key files

| File | Purpose |
|------|---------|
| `server.py` | API routes, OpenClaw webhook helpers, setup/preflight/launch; on startup, optional **auto sync** of `autodev/agents/*` into `OPENCLAW_ROOT/workspace-*` (see `auto_sync_agent_workspaces` in `config.json` / `DEFAULTS` in `server.py`, same mtime rules as `install.sh` step 6) |
| `index.html` | Inline Babel/React UI (all screens in one file) |
| `_build_screens.py` | Optional splice helper for `MIDDLE` → `index.html` — **may lag** `index.html`; treat `index.html` as source of truth for Ideas/Preflight until regenerated |

### Layout notes (Project Ideas)

- **Main nav** (`Sidebar`): collapses to an icon-only abbreviated state (wider than a thin strip) and uses a centered divider toggle (`‹` / `›`) shared with the chats rail.
- **Chats rail**: dedicated vertical list with per-row kebab menu (`⋮`) for row actions (`Rename idea`, `Delete idea`). Rename uses inline edit with Enter/Escape save/cancel behavior and persists via `PATCH /api/ideas/{id}`. The rail can collapse to a compact width; width is intentionally wider than before to prevent divider/button overlap.
- **Action hierarchy**: `Generate Roadmap` is the primary CTA; `Continue to Setup →` appears only after roadmap generation; downloads moved into overflow menu (`⋮`) to reduce visual competition.
- **PRD checklist + document**: right pane starts with a 12-row PRD completeness checklist (status + criticality) that scrolls to sections. Toggle allows showing/hiding empty section placeholders.
- **Markdown rendering parity**: conversation assistant bubbles and PRD document pane both use `marked.parse()` + `dangerouslySetInnerHTML` with shared `.msg-md` styling (headers, lists, tables, code blocks).
- **Submission feedback**: user messages are appended optimistically (input clears immediately), and the UI shows explicit in-progress indicators while the backend/agent turn is running: pending assistant bubble + processing banner + PRD buffering state.
- **Readiness**: status model is `unavailable` / `updating` / `ready`. `POST /api/ideas/{id}/message` triggers readiness (`ideas:{id}:readiness`) and `/api/ideas/{id}/readiness` reports state based on sentinel + active/recent job window (180s). UI polls `/readiness/poll` every 3s while `updating`, stops after 120s with neutral timeout text, and logs structured `[READINESS]` lifecycle lines to `/tmp/ui-server.log`. When `status` is `ready`, the document strip labels the score **PRD readiness:** … **/ 10** and maps `data.conversion_confidence` to the label **Roadmap confidence:** (the JSON field name is unchanged).

### Setup (Preflight)

- **Launch** is disabled until **Run Preflight** has been executed at least once (`preflightChecks` non-null), both fields are locked, and no check has `fail`. Disabled Launch uses grey styling (not cyan at low opacity).
- **Repo path**: must be **absolute** (e.g. `/path/to/your-project/...`); relative strings like `path/to/...` are rejected. Debounced (500ms) checks call `validate-repo-path` then `check-repo-path`: **green ✓** only if the directory **exists on the machine running the server**; **amber +** if the parent exists but the folder does not (create on Confirm); **red ✗** if invalid or neither exists. If `exists()` is false because the server user cannot traverse `/home/otheruser` (mode 700), run the UI server as that user. **Confirm** calls `check-repo-path` again; if the path is missing but the parent exists, the UI offers **Create Folder** (`POST /api/setup/create-repo-dir`).
- **Roadmap seed**: **Paste content** (default) vs **Upload file** toggle; upload uses a hidden `<input type="file">` + accent **Upload .md file** button (filename shown below). Locking validates **format** only (not content quality). When you **Run Preflight**, the client sends optional `roadmap_seed` (if locked) and optional `prd_content` (from Project Ideas); the server writes `roadmap.md` / `prd.md` after validation and **fails** if on-disk files disagree with the staged text.
- **Preflight API**: `POST /api/setup/preflight` body: `repo_path` (required), `roadmap_seed` (optional), `prd_content` (optional). Checks include symlink/gitignore auto-fix, **`git`** (`git --version`), **git repo** (auto `git init` + `branch -M main` when `.git` is missing), workspace agent docs, and roadmap file presence. Per-repo `origin` is not checked — add a remote when you want to push.
- **Pipeline artifacts**: Orchestrator and gates write turn state under **`<project>/.autodev/pipeline/`** (e.g. `phase_state.json`, `phases/`, `metrics.jsonl`, escalation files). Preflight ensures that directory exists and **migrates** legacy files still at the repo root. `.gitignore` uses a single entry **`.autodev/pipeline/`**. Default `phase_state_path` in config resolution is **`<pipeline-project>/.autodev/pipeline/phase_state.json`** (overridable in `ui/config.json`). `roadmap.md` / `prd.md` stay at the project root.
- **Launch API**: `POST /api/setup/launch` body: `repo_path`, `roadmap_seed`, optional `prd_content`. After a successful init it writes a clean `pipeline_state.json` (matching the orchestrator’s project-switch template), then spawns `orchestrator.py` with `--project-path` set to the repo’s realpath. If `pipeline.lock` is held (`_check_orchestrator_liveness`), returns **409** with `{"ok": false, "code": "orchestrator_running", "error": "..."}` so the UI can warn without changing symlink/state.
- **Multi-roadmap**: If more than one `*oadmap*.md` exists under the repo, **Preflight** and **switch-project** return `roadmap_ambiguous` plus `roadmap_files` and `recommended_keep` until the client sends `confirm_roadmap_archive: true` and `keep_filename`. Extras are moved under `autodev_archive/` with timestamped names.
- **Recent projects**: `~/.openclaw/ui_recent_projects.json` (atomic JSON array of `{path, last_used}`) is updated when preflight or switch-project completes with no failing checks. `GET /api/setup/recent-projects` feeds the switch-project modal dropdown.
- **Switch project** (pipeline monitor): When `pipeline_status` is **STOPPED** or **UNKNOWN**, the header path is a control that opens a modal: validate via `POST /api/setup/switch-project` (`start_orchestrator: false`), then **Start pipeline** (`start_orchestrator: true`). While **RUNNING** or **WAITING_FOR_SENTINEL**, the path is plain text and a note explains the pipeline must be stopped first. The API returns **409** if the global pipeline is not stopped.
- **Resume path (Policy A):** `POST /api/resume-orchestrator` **repoints** the configured pipeline-project **symlink** to match `project_path` in `pipeline_state.json` when their realpaths disagree (state wins), then spawns. Response includes `reconciled`, `reconcile_action`, `previous_symlink_real`, `canonical_project_real`. **422** if the link path is unsafe to replace (e.g. a real directory at `project_dir_path`). **503** with a JSON body (`reconciled: true`, `error`) if spawn fails after repoint — operator can **Restart** again. **409** only when `pipeline.lock` indicates the orchestrator is already running. The Pipeline Monitor shows at most **one** amber header line for reconcile / reconcile+spawn-fail (no duplicate copy in sub-panels). **Resume**, **Restart Orchestrator**, and stopped-recovery resume flows map common server `detail` / spawn strings to **plain language** first (`resumeOrchestratorErrorPresentation` / `mapResumeOrchestratorFriendlyMessage` in `index.html`), with optional monospace technical detail — use **Switch project** or **Setup & Preflight** when paths or config are wrong.
- **Queue `ACTIVE` vs pipeline state**: A row in `pipeline_queue.json` with `state: ACTIVE` must match the global `pipeline_state.json` `project_path` (same realpath) or the Project Queue pill can disagree with the Pipeline Monitor header. On **resume orchestrator**, **launch** (mark-matching), **`POST /api/setup/switch-project`** (`start_orchestrator: true`), **`POST /api/queue/trigger-next`**, and **manual→auto** queue kick, the server demotes any **ACTIVE** row whose path does not match that canonical project to **READY** (clears `started_at`), promotes the matching row to **ACTIVE**, and renumbers so the matching row is **`position: 1`** (others keep relative order). **`GET /api/queue`** applies a read-only defensive sort **after** merging any synthetic **ingested** row so the entry whose path matches `pipeline_state.project_path` is always **first** in the JSON `queue` array (covers on-disk `position` drift and “active project not in queue file”).
- **Preflight**: after the first run, the button label is **Re-run Preflight** with **Last run:** relative time; `status-pulse` on the button when any check is `fail`. Launch stays disabled while `roadmap_ambiguous` is unresolved (banner: **Archive extras & continue preflight**).

### Pipeline Monitor (live phase + activity)

- **Agent attempts:** Under **Current phase**, an **Agent attempts** heading precedes three rows (Planner / Executor / Reviewer). Each row is **`AgentAttemptRow`**: three **boxed cells** (tight `gap-0.5`) with **outline + translucent fill** for success/fail (check / X in **`AGENT_ATTEMPT_DOT_HEX`** hues), **in-flight** uses **blue `#3b82f6`** (not pipeline teal `#0d9488`) so it does not read like success lime; **`n/3`** uses **`text-slate-500`**. State from `getAgentAttemptDotStates`; **`n`** from **`computeAgentAttemptFractionN`**. Native **`title`** on cells (`AGENT_ATTEMPT_DOT_TITLES`).
- **Activity tab:** Event badges show **human-readable** type labels; hover the badge for **`Machine id: …`** (raw `event` or `event_type`). Empty ring buffer: **No events yet. Events appear here as the pipeline runs.**
- **`GET /api/roadmap`**: Roadmap file resolution prefers `pipeline_state.json` `project_path` (then `_canonical_roadmap_path` on the real project directory) so the phase list matches the project shown in the header; it falls back to `config.roadmap_path` when that field is empty or the path is not a directory. This avoids split-brain when the `pipeline-project` symlink lags `project_path` (Policy A in **Resume** repoints the symlink, but the API does not require it to display correctly).

### Release / verification

- Before merging a change that touches setup or the pipeline monitor, run the full test suite and manually exercise Launch, preflight (including multi-roadmap confirm), and the switch-project modal in a browser (backend tests alone may miss UI regressions).
- **Queue vs monitor consistency (browser):** After seeding or reordering queue rows, wait **2–5 s** after navigation or POST (`browser_wait_for` / sleep) before asserting pills; compare `GET /api/queue` with `GET /api/state` `project_path` if the UI looks split-brain.

### Browser / MCP E2E testing protocol for Project Ideas agent turns

Agent turns in Project Ideas (prd-creator webhook → file writes → sentinel poll) can take **60–300 s** on real hardware. Failing to wait for completion before asserting produces false negatives (e.g. "Note / Show diff missing") that have nothing to do with the code under test.

**Required sequence for any agent using `cursor-ide-browser` to verify the Ideas PRD pane:**

1. Click **Send** (or submit the composer in whatever way the test requires).
2. Wait for the "Sending…" / pending state to disappear **before** taking any assertion snapshot:
   ```
   browser_wait_for(textGone="Sending…", timeout=120000)
   ```
   Use **`timeout` of at least 120 000 ms (2 min)** for warm models. Increase to **300 000 ms (5 min)** for cold starts or slow hardware.
3. After `textGone` resolves, add a fixed cushion of **3–5 s** to allow the PRD tab state to re-render and the optional readiness poll to start:
   ```
   browser_wait_for(time=5)
   ```
4. Then take a fresh `browser_snapshot` (or `browser_search` for a stable string such as **"PRD readiness:"** or **"Note"** in the section header row) to assert the final state.
5. **Never** conclude that controls are missing from a snapshot taken less than 2 s after send on a real agent run.
6. If using MCP code-review-graph after edits, run `get_impact_radius` on `_match_prd_section_heading_line` / `parsePrdSections` to ensure all affected paths are covered by the new tests.

## Editing `index.html`

1. **Backup:** `cp ui/index.html ui/index.html.bak` (per roadmap).
2. **Regenerate middle block:** from a clean `git` baseline, run `python3 ui/_build_screens.py` after changing the `MIDDLE` string in `_build_screens.py`.
3. **App shell:** Root `App()` with `AppCtx.Provider`, setup state, and `PreflightScreen` props lives after `PipelineScreen` in `index.html` — re-apply if you only re-run `_build_screens.py`.

## Tests

From repo root: `pytest tests/ -q`

### Session self-heal behavior

If an idea has turn artifacts (`turns/*.md`, `turns/1.done`, `prd_draft.md`) but `session.json` is stale/empty, the backend now rebuilds conversation/PRD payloads on read (`GET /api/ideas/{id}/session` and listing path). This prevents "named idea appears in list but opens blank" regressions after interrupted writes.

### Setup: validate-repo-path UI bug (fixed)

The first version of `onRepoPathConfirm` did `if (d.valid)` after `r.json()` **without** checking `r.ok`. Any non-200 response (404, 502, wrong server) returns FastAPI’s `{ "detail": ... }`, which has **no** `valid` field — so the UI fell through to the generic **"Invalid path"** string. The handler now checks `!r.ok`, surfaces `detail`, and trims the path before POST.

## Infra note

Manual integration tests (`tests/test_skill_mode_*`) expect `~/.openclaw/pipeline-project` → `/tmp/infra-e1-test-a` or `-b`. They skip automatically when that layout is not present.
