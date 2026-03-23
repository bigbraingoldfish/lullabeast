# FIX-PASS-2 — Polish and QOL improvements

**Completed:** 2026-03-23T17:02:21Z

## Fixes applied

1. Auto-select most recent idea on mount — already satisfied by Pass 1 (`useEffect` on `ideasList` / `currentIdeaId`); verified on remount.
2. Native file picker replaced with hidden input + styled **Upload .md file** button and filename line (Setup; Ideas already used this pattern).
3. Repo path: debounced `validate-repo-path` for ✓/✗; `check-repo-path` + **Create Folder** via `create-repo-dir` when parent exists.
4. Preflight: **Re-run Preflight** label after first run, **Last run:** relative time, `status-pulse` when any check `fail`.
5. Delete idea: inline confirmation in chats rail and conversation header (no `alert()`).
6. Auto-scroll conversation to bottom on new messages (`scrollIntoView` smooth).
7. Ideas list: relative timestamps from `updated` (replaces summary line under name).
8. Roadmap seed: **Paste content** | **Upload file** toggle; single visible input at a time.

## Files changed

- `ui/index.html`
- `ui/server.py` (new endpoints: `POST /api/setup/check-repo-path`, `POST /api/setup/create-repo-dir`)
- `tests/test_api_setup_repo_path.py`
- `roadmap.md`, `ui/README.md`

## Lessons

- Empty checkpoint commit (`--allow-empty`) is useful when the working tree is already clean before a pass.
- Preflight `PreflightScreen` now uses local `useState`/`useRef` for roadmap input mode and file name; keep hook order stable when extending.

## Tests

Full suite: `503 passed, 11 skipped` (at commit time).
