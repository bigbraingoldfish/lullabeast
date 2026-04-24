# Phase UI-5 — Chat input bar layout

**Status:** Complete  
**Date:** 2026-04-24

## Goal

Project Ideas composer: textarea full width on top; dedicated row below with **Attach** (icon + label) left and **Send** right (`flex items-center justify-between`). Staged attachment / annotation pills unchanged above the composer. Submit logic unchanged.

## Files

- `ui/index.html` — IdeasScreen footer composer (`M2 UI-5: input composer`)
- `tests/test_ui_input_bar.py` — layout helpers + disabled/class contracts + wiring

## Verification

```bash
source .env
pytest tests/test_ui_input_bar.py -q
pytest tests/ -q
```

## Notes

- Send / Attach `disabled` conditions match pre-UI-5 behavior.
- Textarea uses `w-full min-w-0` instead of `flex-1` inside the column stack.
