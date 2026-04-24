# Phase UI-4 — Unified sidebar collapse

**Status:** Complete  
**Date:** 2026-04-24

## Goal

Single `sidebarCollapsed` state in `App`, one chevron in the `Sidebar` brand row, nav rail `w-14` / `w-44`; chats rail `w-0` with overflow hidden when collapsed (no `w-16` void). Removed `.side-divider` / `.side-divider-btn` CSS and floating pills.

## Files

- `ui/index.html` — `App` state + `appCtxValue.sidebarCollapsed`; `Sidebar` props + header toggle; `IdeasScreen` reads `sidebarCollapsed` from `AppCtx`
- `tests/test_ui_sidebar_collapse.py` — mirror helpers + wiring assertions
- `tests/test_ui_ideas_pass4_restructure.py` — header chevron expectations
- `tests/test_ui_ideas_screen_split_panel.py` — `sidebarCollapsed` in IdeasScreen body

## Verification

```bash
source .env
pytest tests/test_ui_sidebar_collapse.py tests/test_ui_ideas_pass4_restructure.py tests/test_ui_ideas_screen_split_panel.py -q
pytest tests/ -q
```

## Notes

- `IdeasScreen` stays zero-arg so indentation-based `extract_function_body` tests keep matching; rail width uses `AppCtx.sidebarCollapsed`.
- `setChatsRailCollapsed` on new idea removed — collapse state is global.
- `sidebarCollapsed` persists when navigating away from Project Ideas (by design).
