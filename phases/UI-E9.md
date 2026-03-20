# UI-E9 — Add launch sequence — initialize project directory, set symlink, navigate to pipeline monitor
**Completed:** 2026-03-19T19:45:00Z
**Executor attempts:** 1
**Reviewer passes:** 1 (direct implementation)

## Changes
- `ui/server.py`: Added `_run_init_project(repo_path, roadmap_seed)` and `POST /api/setup/launch`
  - Mode A (no .git): creates phases/, tests/, src/{name}/__init__.py, pipeline.json, roadmap.md, prd.md, lessons.md, metrics.jsonl, .gitignore; validates roadmap; git init + checkout main + commit
  - Mode B (.git exists): creates only missing files, appends missing gitignore entries, commits only new files
  - Both modes: sets `~/.openclaw/pipeline-project` symlink atomically
  - Returns `{"ok": bool, "error": str|null}`; Mode A cleans up on failure
- `ui/index.html`: Added Launch button (disabled until repo locked + roadmap locked + valid + preflight passed) and launch error display to PreflightScreen. On success navigates back to Ideas screen.
- `roadmap.md`: Marked UI-E5 through UI-E9 as [x] complete
- `tests/test_api_setup_launch.py`: 22 tests covering Mode A structure, Mode B preservation, git failure handling, symlink setting, return values
