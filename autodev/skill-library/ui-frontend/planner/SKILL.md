---
name: ui-frontend-planner
description: Domain guidance for planning UI and frontend phases. Loaded when phase category is UI.
---

# UI/Frontend Planning Guidance

## Architecture constraints
- Define UI state shape first (serializable, minimal, explicit).
- Render contract: render(state) → view output. No side effects, no mutations in render.
- Effect contract: effects triggered only from event handlers or lifecycle hooks, never from render.

## Decomposition pattern
- State model + transitions (reducer/state machine)
- Rendering (pure) per screen/component
- Input mapping (keymap/handlers) + focus model
- Layout engine (constraint-based, dynamic sizing)
- Visual feedback states (loading/error/empty/success)
- Tests (unit + interaction + regression) with no manual inspection

## Terminal/TUI requirements
- Lifecycle: init → enter raw/alt-screen → loop → restore on exit AND panic.
- Resize: on resize event → recompute layout → full redraw.
- Cursor: when visible, where, reset rules.

## Pass criteria (no "looks right")
- Render tests: given fixed state, output is deterministic (snapshot/buffer assertions).
- Interaction tests: given input events, state transitions match expected sequence.
- Robustness: unknown keys don't crash; rapid input doesn't freeze; resize triggers redraw.

## TDD structure
Require at least: pure render tests, reducer/state-transition tests, one E2E interaction test per critical workflow, one resize regression test.
