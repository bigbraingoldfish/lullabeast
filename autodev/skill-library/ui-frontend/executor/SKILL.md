---
name: ui-frontend-executor
description: Domain guidance for implementing UI and frontend phases. Loaded when phase category is UI.
---

# UI/Frontend Implementation Guidance

## Render purity
- Render is a pure function: no globals, no caches, no logs, no timers, no state setters during render.
- All side effects in event handlers or post-render effects only.

## Input handling
- Centralize keymap; default handler for unknown input (no-op, no crash).
- Never block UI loop waiting for input — use poll/timeouts with fixed tick for redraw.
- Handle key repeat and rapid input: throttle/debounce where needed.

## Terminal/TUI hardening
- Restore terminal state on ALL exits: normal, error, panic (install panic hook).
- Resize: listen for resize events → recompute layout from current size → full redraw.
- Reset styles/attributes each frame (no attribute leaks between renders).
- Set cursor position explicitly; hide/show deterministically.

## Layout correctness
- Never hardcode widths/heights without fallback to dynamic sizing.
- Use "inner rect" helpers for borders/padding; avoid manual off-by-one math.
- Clamp all coordinates and lengths to viewport bounds.

## Testability
- Make render callable without real terminal: render into buffer backend (TUI) or test DOM (web).
- Write tests first: reducer tests, render snapshot tests, interaction tests.

## "Done" means
All tests pass in CI/headless mode. Resize and unknown-input cases covered. No manual-only verification claims.
