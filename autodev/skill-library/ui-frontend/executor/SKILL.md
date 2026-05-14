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

## Testability — necessary but not sufficient on the web

- Make render callable without real terminal: render into buffer backend (TUI) or test DOM (web).
- Write tests first: reducer tests, render snapshot tests, interaction tests.

**Web caveat:** jsdom is the default DOM for vitest/jest. jsdom does not implement CSS layout, computed style, or paint. A test that asserts `document.querySelector('.some-zone')` exists passes in jsdom even if the rendered page is visually broken: the zone's element is present but invisible because no CSS lays it out. A snapshot of `<span>foo</span><span>bar</span>` is deterministic but the page renders as the concatenated text "foobar" with no styling. **For web UI, jsdom tests are necessary but not sufficient.** Final acceptance requires a real-browser screenshot artifact captured via Playwright MCP.

## Visual smoke capture (visual phases)

After your jsdom (or equivalent) tests pass on a visual phase (subsystem prefix `UI` or `INT`, or any phase ID in `AUTODEV_VISUAL_PHASE_RAW_IDS`):

1. Launch the dev server in the background (`npm run dev`, `python -m http.server`, or whatever the project uses). Capture the printed URL.
2. Wait for the server to be ready (poll the URL with `curl -sf` or equivalent until it returns 200).
3. Use the Playwright MCP tool to navigate to the URL and capture screenshots:
   - `{phase_raw_id}-default.png` — the rendered default state on load.
   - Additional states relevant to the phase (modal open, mid-interaction, alternate theme, success/done state, etc.).
4. Save under `pipeline-project/.autodev/pipeline/visual-smoke/`.
5. Stop the dev server.
6. List each screenshot path in `executor_output.visual_smoke_artifacts` with a one-sentence description of what state is captured.

If the dev server fails to start, set `status: "failed"` and report the failure in `failure_reason`. Do NOT skip visual capture and claim success — the reviewer gate will reject the phase and consume your retry budget.

## "Done" means

All tests pass in CI/headless mode AND, on visual phases, real-browser screenshots captured and listed in `visual_smoke_artifacts`. Resize and unknown-input cases covered. No manual-only verification claims.
