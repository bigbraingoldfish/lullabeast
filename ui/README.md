# AutoDev UI (`ui/`)

FastAPI server (`server.py`) and a single-file React app (`index.html`) for the pipeline monitor, Project Ideas, and Setup & Preflight screens.

## Key files

| File | Purpose |
|------|---------|
| `server.py` | API routes, OpenClaw webhook helpers, setup/preflight/launch |
| `index.html` | Inline Babel/React UI (all screens in one file) |
| `_build_screens.py` | Optional splice helper for `MIDDLE` → `index.html` — **may lag** `index.html`; treat `index.html` as source of truth for Ideas/Preflight until regenerated |

### Layout notes (Project Ideas)

- **Main nav** (`Sidebar`): collapsible (`«` / `»`); narrow strip shows icons only with tooltips.
- **Ideas screen**: dedicated **vertical Chats rail** (scrollable list + draft row + per-row delete). Picking a chat **collapses** the rail so conversation + PRD get more width; use **◀ / ▶** to expand/collapse. No session `<select>` dropdown. Each row shows **relative time** from API `updated`. **Delete** uses inline confirm (rail and **Delete session** header); conversation **auto-scrolls** to the latest message after new turns.
- **Readiness**: `GET /api/ideas/{id}/readiness` serves agent-written `readiness.json` after `readiness.done` exists; **`POST /api/ideas/{id}/message`** triggers a non-blocking readiness webhook (`ideas:{id}:readiness`). UI polls `/readiness/poll` every 3s while status is `updating`.
- **Assistant messages**: rendered with `marked` (CDN); user messages stay plain text.

### Setup (Preflight)

- **Launch** is disabled until **Run Preflight** has been executed at least once (`preflightChecks` non-null), both fields are locked, and no check has `fail`. Disabled Launch uses grey styling (not cyan at low opacity).
- **Repo path**: must be **absolute** (e.g. `/home/pi/...`); relative strings like `home/pi/...` are rejected. Debounced (500ms) checks call `validate-repo-path` then `check-repo-path`: **green ✓** only if the directory **exists on the machine running the server**; **amber +** if the parent exists but the folder does not (create on Confirm); **red ✗** if invalid or neither exists. If `exists()` is false because the server user cannot traverse `/home/pi` (mode 700), run the UI server as that user. **Confirm** calls `check-repo-path` again; if the path is missing but the parent exists, the UI offers **Create Folder** (`POST /api/setup/create-repo-dir`).
- **Roadmap seed**: **Paste content** (default) vs **Upload file** toggle; upload uses a hidden `<input type="file">` + accent **Upload .md file** button (filename shown below).
- **Preflight**: after the first run, the button label is **Re-run Preflight** with **Last run:** relative time; `status-pulse` on the button when any check is `fail`.

## Editing `index.html`

1. **Backup:** `cp ui/index.html ui/index.html.bak` (per roadmap).
2. **Regenerate middle block:** from a clean `git` baseline, run `python3 ui/_build_screens.py` after changing the `MIDDLE` string in `_build_screens.py`.
3. **App shell:** Root `App()` with `AppCtx.Provider`, setup state, and `PreflightScreen` props lives after `PipelineScreen` in `index.html` — re-apply if you only re-run `_build_screens.py`.

## Tests

From repo root: `pytest tests/ -q`

### Setup: validate-repo-path UI bug (fixed)

The first version of `onRepoPathConfirm` did `if (d.valid)` after `r.json()` **without** checking `r.ok`. Any non-200 response (404, 502, wrong server) returns FastAPI’s `{ "detail": ... }`, which has **no** `valid` field — so the UI fell through to the generic **"Invalid path"** string. The handler now checks `!r.ok`, surfaces `detail`, and trims the path before POST.

## Infra note

Manual integration tests (`tests/test_skill_mode_*`) expect `~/.openclaw/pipeline-project` → `/tmp/infra-e1-test-a` or `-b`. They skip automatically when that layout is not present.
