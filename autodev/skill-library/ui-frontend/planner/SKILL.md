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

## Visual contract (web UI)

Roadmap visual language ("minimalist design", "settings menu", "render the dashboard") is the planner's *input*, not output. Translate it into specifications the executor cannot interpret away:

- **Token-to-renderable mapping.** Every logical token in the state model must map to a concrete renderable. The renderable type depends on the domain — Unicode glyph (specify the codepoint), SVG path, icon-font class, or image asset (specify the path). Never leave the executor to "pick something appropriate"; pick it in the plan.
- **Color rules.** Specify which logical states/categories render in which color, with concrete hex/rgb values. State-to-color is a plan-level decision, not an executor improvisation.
- **Intra-element layout.** Where do sub-parts sit inside a parent? Specify position (top-left, centered, bottom-right) and offsets, not just "inside the element".
- **Modal/overlay pattern.** Any overlay must specify `position: fixed`, backdrop styling, z-index above content, escape-to-close, click-outside-to-close. "A modal will appear" is not a spec.
- **At-rest visible state for empty layout zones.** Containers that may be empty before data arrives (placeholders, drop zones, list panels, target slots) need explicit at-rest styling (border, background, outline). Without this, the user sees nothing and assumes the zone doesn't exist.
- **Dev-server smoke targets.** Specify which rendered states the executor must capture (default load, modal open, mid-interaction, alternate theme, success/done state) and what each screenshot must visibly contain.

## Pass criteria (no "looks right" — but verifiable visual outcomes)

- Render tests: given fixed state, output is deterministic (snapshot/buffer assertions).
- Interaction tests: given input events, state transitions match expected sequence.
- Robustness: unknown keys don't crash; rapid input doesn't freeze; resize triggers redraw.
- **Visual smoke pass:** a `pass_criteria` entry phrased as a verifiable claim about what is visible in the executor's screenshot (e.g. "Screenshot at `.autodev/pipeline/visual-smoke/{phase}-default.png` shows the named layout zones distinct and positioned as described, with logical tokens rendered as styled glyphs/icons"). The reviewer is multimodal and will load the screenshot, so write each criterion as something a human looking at the image could verify.

## TDD structure

Require at least: pure render tests, reducer/state-transition tests, one E2E interaction test per critical workflow, one resize regression test. **On visual phases, also require the executor to capture screenshots via the Playwright MCP and list them in `executor_output.visual_smoke_artifacts`.**
