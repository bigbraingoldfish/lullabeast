# UI-E7 — Add roadmap seed format validation with line-specific errors
**Completed:** 2026-03-19T19:15:00Z
**Executor attempts:** 1
**Reviewer passes:** 1 (direct implementation)

## Changes
- `ui/server.py`: Added `_PHASE_LINE_RE` compiled regex, `_validate_roadmap_content(content)` helper, `POST /api/setup/validate-roadmap` endpoint
  - Phase line regex: `^- \[.\] \`[A-Z]+-[A-Z]\d+\` \| (?:LOW|HIGH) \| .+` (MULTILINE)
  - Checks `> Test:` within 10 lines of each phase line
  - Reports duplicate phase IDs
  - Returns `{"valid": bool, "errors": [{"line": int, "content": str, "message": str}]}`
- `ui/index.html`: Added Validate Roadmap button (shown when locked with content) and validation results panel to PreflightScreen
- `tests/test_api_setup_validate_roadmap.py`: 14 tests covering all validation rules
