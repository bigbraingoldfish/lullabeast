# UI-E5 — Phase UI-E5: Add roadmap generation flow — readiness check, PRD-to-roadmap conversion, and download
**Completed:** 2026-03-19T21:45:00Z
**Executor attempts:** 3 (attempt 3 preempted mid-write — EXECUTOR_PREEMPTED_OUTPUT_INVALID)
**Reviewer passes:** 0 (human completed)

## What was built
- `GET /api/ideas/{id}/readiness` — checks prd_content for conversion-ready marker or all 10 required sections non-empty; returns `{ready, reason}`
- `POST /api/ideas/{id}/convert` — reads conversion prompt, webhooks prd-creator agent, polls `roadmap_draft.done` (2s/180s), atomically stores `roadmap_content` in session.json
- `GET /api/ideas/{id}/download-roadmap` — returns roadmap_content as a text/markdown attachment with filename derived from prd_content heading
- `REQUIRED_PRD_SECTIONS` constant (10 sections) and `_is_prd_section_nonempty` helper
- `CONVERT_TIMEOUT`/`CONVERT_POLL_INTERVAL` module-level constants (patchable in tests)
- IdeasScreen: `roadmapContent`, `isConverting`, `convertError` state; Generate Roadmap button (visible when ready); roadmap section; Proceed to Setup button

## Tests
- `test_api_ideas_readiness.py`: 7 tests (404, ready marker, all sections, empty, missing, header-only, field check)
- `test_api_ideas_convert.py`: 6 tests (404, 422 no prd, 503 no prompt, 408 timeout, 200 success, session.json write)
- `test_api_ideas_download_roadmap.py`: 8 tests (404 no idea, 404 no roadmap, 404 empty, 200, content-type, attachment, filename heading, filename fallback)
All 21 pass.

## Preemption note
`EXECUTOR_PREEMPTED_OUTPUT_INVALID`: executor wrote `executor_output.json` with `tests_passing: null` before completing, likely interrupted between JSON write and sentinel write. Orchestrator caught this, gate failed on null tests_passing, escalated. Test files and phase completed by human reviewer.
