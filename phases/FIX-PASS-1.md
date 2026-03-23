# FIX-PASS-1 — Critical fixes, readiness architecture, markdown rendering

**Completed:** 2026-03-23T00:00:00Z (local commit time may differ)

## Fixes applied

0. **AGENTS.md** — Readiness Context Integration section added after Output Contract (`~/.openclaw/workspace-prd-creator/AGENTS.md`).
1. **Launch button** — Requires `preflightChecks` to be non-null (preflight has run), plus locked fields and no `fail`.
2. **Launch disabled styling** — Grey background + `cursor-not-allowed` when disabled (no cyan `opacity-40` alone).
3. **Phantom Draft** — `currentIdeaId` starts `null`; first idea from `GET /api/ideas` auto-selected; empty state copy when none.
4. **UUID names** — `GET /api/ideas` resolves `New Idea` / empty / UUID-shaped names via `prd_draft.md` H1, first user message, or `Untitled Idea`; persists to `session.json`.
5. **Readiness** — Heuristic `_check_prd_readiness` removed; agent-driven `readiness.json` + `readiness.done`; `_trigger_readiness_assessment` after each conversational turn; poll endpoint; Ideas UI panel with score, gaps, expandable sections.
6. **Continue to Setup** — Validates roadmap via `POST /api/setup/validate-roadmap`; blocks on `Transformation Aborted`; warning with Review / Proceed Anyway.
7. **Git preflight FAIL** — Messages include copy-paste `git -C …` commands.
8. **Markdown** — `marked` CDN; assistant bubbles use `dangerouslySetInnerHTML` + `.msg-md` styles.

## Files changed

- `~/.openclaw/workspace-prd-creator/AGENTS.md`
- `ui/index.html`
- `ui/server.py`
- `ui/README.md`
- `roadmap.md`
- `tests/test_api_readiness.py` (new)
- `tests/test_api_ideas_list.py`, `tests/test_ui_ideas_screen_wired.py`, `tests/test_ui_setup_fetch_http_status.py`
- Removed: `tests/test_api_ideas_readiness.py` (replaced by `test_api_readiness.py`)

## Lessons

- `preflightChecks === null` must gate Launch; `hasFail` alone is falsy when preflight never ran.
- Readiness as an agent artifact (`readiness.json`) avoids placeholder text passing naive section heuristics.
- `_build_screens.py` is not auto-synced; edit `index.html` as canonical for Ideas/Preflight until the splice script is updated.

## Test suite

Final run: **498 passed**, 11 skipped (see commit era).
