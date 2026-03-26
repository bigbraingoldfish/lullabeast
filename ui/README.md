# AutoDev UI (`ui/`)

FastAPI server (`server.py`) and a single-file React app (`index.html`) for the pipeline monitor, Project Ideas, and Setup & Preflight screens.

## Key files

| File | Purpose |
|------|---------|
| `server.py` | API routes, OpenClaw webhook helpers, setup/preflight/launch |
| `index.html` | Inline Babel/React UI (all screens in one file) |
| `_build_screens.py` | Optional splice helper for `MIDDLE` → `index.html` — **may lag** `index.html`; treat `index.html` as source of truth for Ideas/Preflight until regenerated |

### Layout notes (Project Ideas)

- **Main nav** (`Sidebar`): collapses to an icon-only abbreviated state (wider than a thin strip) and uses a centered divider toggle (`‹` / `›`) shared with the chats rail.
- **Chats rail**: dedicated vertical list with per-row kebab menu (`⋮`) for destructive action (`Delete idea`). The rail can collapse to a compact width; width is intentionally wider than before to prevent divider/button overlap.
- **Action hierarchy**: `Generate Roadmap` is the primary CTA; `Continue to Setup →` appears only after roadmap generation; downloads moved into overflow menu (`⋮`) to reduce visual competition.
- **PRD checklist + document**: right pane starts with a 12-row PRD completeness checklist (status + criticality) that scrolls to sections. Toggle allows showing/hiding empty section placeholders.
- **Markdown rendering parity**: conversation assistant bubbles and PRD document pane both use `marked.parse()` + `dangerouslySetInnerHTML` with shared `.msg-md` styling (headers, lists, tables, code blocks).
- **Submission feedback**: user messages are appended optimistically (input clears immediately), and the UI shows explicit in-progress indicators while the backend/agent turn is running: pending assistant bubble + processing banner + PRD buffering state.
- **Readiness**: status model is `unavailable` / `updating` / `ready`. `POST /api/ideas/{id}/message` triggers readiness (`ideas:{id}:readiness`) and `/api/ideas/{id}/readiness` reports state based on sentinel + active/recent job window (180s). UI polls `/readiness/poll` every 3s while `updating`, stops after 120s with neutral timeout text, and logs structured `[READINESS]` lifecycle lines to `/tmp/ui-server.log`.

### Setup (Preflight)

- **Launch** is disabled until **Run Preflight** has been executed at least once (`preflightChecks` non-null), both fields are locked, and no check has `fail`. Disabled Launch uses grey styling (not cyan at low opacity).
- **Repo path**: must be **absolute** (e.g. `/home/pi/...`); relative strings like `home/pi/...` are rejected. Debounced (500ms) checks call `validate-repo-path` then `check-repo-path`: **green ✓** only if the directory **exists on the machine running the server**; **amber +** if the parent exists but the folder does not (create on Confirm); **red ✗** if invalid or neither exists. If `exists()` is false because the server user cannot traverse `/home/pi` (mode 700), run the UI server as that user. **Confirm** calls `check-repo-path` again; if the path is missing but the parent exists, the UI offers **Create Folder** (`POST /api/setup/create-repo-dir`).
- **Roadmap seed**: **Paste content** (default) vs **Upload file** toggle; upload uses a hidden `<input type="file">` + accent **Upload .md file** button (filename shown below). Locking validates **format** only (not content quality). When you **Run Preflight**, the client sends optional `roadmap_seed` (if locked) and optional `prd_content` (from Project Ideas); the server writes `roadmap.md` / `prd.md` after validation and **fails** if on-disk files disagree with the staged text.
- **Preflight API**: `POST /api/setup/preflight` body: `repo_path` (required), `roadmap_seed` (optional), `prd_content` (optional). Checks include symlink/gitignore auto-fix, **`git`** (`git --version`), **git repo** (auto `git init` + `branch -M main` when `.git` is missing), workspace agent docs, and roadmap file presence. Per-repo `origin` is not checked — add a remote when you want to push.
- **Launch API**: `POST /api/setup/launch` body: `repo_path`, `roadmap_seed`, optional `prd_content`. After a successful init it writes a clean `pipeline_state.json` (matching the orchestrator’s project-switch template), then spawns `orchestrator.py` with `--project-path` set to the repo’s realpath. If `pipeline.lock` is held (`_check_orchestrator_liveness`), returns **409** with `{"ok": false, "code": "orchestrator_running", "error": "..."}` so the UI can warn without changing symlink/state.
- **Multi-roadmap**: If more than one `*oadmap*.md` exists under the repo, **Preflight** and **switch-project** return `roadmap_ambiguous` plus `roadmap_files` and `recommended_keep` until the client sends `confirm_roadmap_archive: true` and `keep_filename`. Extras are moved under `autodev_archive/` with timestamped names.
- **Recent projects**: `~/.openclaw/ui_recent_projects.json` (atomic JSON array of `{path, last_used}`) is updated when preflight or switch-project completes with no failing checks. `GET /api/setup/recent-projects` feeds the switch-project modal dropdown.
- **Switch project** (pipeline monitor): When `pipeline_status` is **STOPPED** or **UNKNOWN**, the header path is a control that opens a modal: validate via `POST /api/setup/switch-project` (`start_orchestrator: false`), then **Start pipeline** (`start_orchestrator: true`). While **RUNNING** or **WAITING_FOR_SENTINEL**, the path is plain text and a note explains the pipeline must be stopped first. The API returns **409** if the global pipeline is not stopped.
- **Resume path safety**: `POST /api/resume-orchestrator` passes `--project-path` from the **pipeline-project symlink** realpath when it disagrees with `project_path` in `pipeline_state.json`, so resume targets the active directory after Launch/switch.
- **Preflight**: after the first run, the button label is **Re-run Preflight** with **Last run:** relative time; `status-pulse` on the button when any check is `fail`. Launch stays disabled while `roadmap_ambiguous` is unresolved (banner: **Archive extras & continue preflight**).

### Release / verification

- Before merging a change that touches setup or the pipeline monitor, run the full test suite and manually exercise Launch, preflight (including multi-roadmap confirm), and the switch-project modal in a browser (backend tests alone may miss UI regressions).

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
