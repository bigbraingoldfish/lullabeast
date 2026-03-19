# UI-E3 — Phase UI-E3: Add document management — list, create, resume, and delete idea documents
**Completed:** 2026-03-19T20:17:00Z
**Duration:** unknown
**Executor attempts:** 1
**Reviewer passes:** 1

## What was built
Implemented the ideas document management feature: added `_extract_summary` helper, four new REST endpoints (`GET/POST /api/ideas`, `DELETE /api/ideas/{id}`, `GET /api/ideas/{id}/download`), rewrote `IdeasScreen` with stateful idea list, create/delete/download buttons, and per-item confirmation modal.

## Tests
- `tests/test_api_ideas_list.py` — GET /api/ideas: empty/absent directory, list with id/name/summary/updated, name fallback to id, blank summary, multiple ideas sorted newest-first; DELETE returns 404 for nonexistent
- `tests/test_api_ideas_create.py` — POST /api/ideas creates directory and session.json with correct schema, returns UUID format, subsequent GET includes new idea
- `tests/test_api_ideas_delete.py` — DELETE removes directory recursively, returns 404 for nonexistent, deleted idea disappears from list
- `tests/test_api_ideas_download.py` — download returns Content-Disposition header, filename from first # heading or id fallback, returns prd_content body, 404 for nonexistent
- `tests/test_summary_extraction.py` — first sentence after ## Problem Statement, no period case, missing section, blank section, empty string, section at end of file, whitespace stripping

## Files changed
- ui/server.py
- ui/index.html
- tests/test_api_ideas_list.py
- tests/test_api_ideas_create.py
- tests/test_api_ideas_delete.py
- tests/test_api_ideas_download.py
- tests/test_summary_extraction.py

## Files deleted
None.

## Lessons
- `_extract_summary` needs to skip lines starting with `## ` (other section headings) to avoid treating them as the summary when the Problem Statement section is blank.
- FastAPI's `TestClient` uses the app object directly — no need to start a server for unit tests.
