# UI-E8 — Add orchestrator preflight validation with per-check status display and .gitignore auto-inject
**Completed:** 2026-03-19T19:30:00Z
**Executor attempts:** 1
**Reviewer passes:** 1 (direct implementation)

## Changes
- `ui/server.py`: Added `_run_preflight_checks(repo_path)` (7 ordered checks) and `POST /api/setup/preflight`
  - Check 1: symlink `~/.openclaw/pipeline-project` → repo_path
  - Check 2: .gitignore presence
  - Check 3: .gitignore entries (auto-inject 7 pipeline entries with header)
  - Check 4: git repo + main/master branch (subprocess)
  - Check 5: workspace-{planner,executor,reviewer,escalation} dirs + 5 required docs each
  - Check 6: git remote origin (warn-only)
  - Check 7: roadmap file glob (warn-only)
- `ui/index.html`: Added "Run Preflight" button + per-check status display (pass/fail/warn) to PreflightScreen
- `tests/test_api_setup_preflight.py`: 18 tests covering all checks
