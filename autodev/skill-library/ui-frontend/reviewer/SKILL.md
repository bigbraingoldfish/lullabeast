---
name: ui-frontend-reviewer
description: Domain guidance for reviewing UI and frontend phases. Loaded when phase category is UI.
---

# UI/Frontend Review Guidance

## Core invariants
- Render is pure (no state updates, no globals, no side effects in render paths).
- State transitions explicit and test-covered.
- Layout constraint-based; adapts to size changes.

## Terminal/TUI checklist
- Raw mode / alt screen entered and ALWAYS restored on exit/panic.
- Resize handling exists and is tested.
- Cursor and style resets deterministic (no attribute leaks).

## Input handling checklist
- Unknown keys handled safely (no crash, no unintended mutation).
- No blocking reads in hot loop; tick/poll used.
- Rapid input has defined behavior.

## Visual verification (web UI — REQUIRED on visual phases)

Tests passing is necessary but not sufficient for web UI. jsdom does not implement CSS layout, computed style, or paint, so a test suite can be 100% green while the rendered output is visually broken (logical tokens emitted as raw concatenated text, overlays rendering inline instead of as modals, empty zones invisible because containers have no rest-state styling).

You are multimodal. On visual phases (subsystem prefix `UI` or `INT`, or any phase ID in `AUTODEV_VISUAL_PHASE_RAW_IDS`):

1. Read `executor_output.visual_smoke_artifacts` — the list of screenshot paths the executor captured.
2. Load each screenshot PNG directly through the file-read tool. Do not just check that the file exists — look at the image.
3. For each screenshot, ask:
   - **Token rendering.** Are logical tokens from the state model (categories, types, statuses, indicators, identifiers) rendered as styled glyphs / icons / images / SVG, or as raw English-word text strings concatenated together?
   - **Layout zones.** Are the named zones from the roadmap (panels, sections, columns, regions, modals, menus, controls) visibly distinct and positioned as described? Or are some of them invisible because empty containers have no rest-state styling?
   - **Modal pattern.** When the roadmap calls for an overlay (settings, popup, dialog, drawer), is the rendered overlay positioned over the content with a backdrop, or is it stacked inline below the main view?
   - **Color and contrast.** Where the design requires color discrimination (state categories, dark mode, status indicators), is it actually visible?
4. Compare what you see against the roadmap Done Criteria and the PRD's described user experience.
5. Set `visual_verification` to:
   - `"pass"` — rendered output matches the design language and Done Criteria.
   - `"fail"` — visible problems. Add a blocking issue with `attribution: "impl"` describing what you see specifically (e.g. "tokens render as concatenated text spans with no CSS for the wrapping classes; expected styled glyphs per the Done Criteria").
   - `"cannot_verify"` — the executor produced no screenshots, the files are unreadable, or the dev server failed to boot. Attribution: `"impl"` — it is the executor's responsibility to deliver the artifacts.
6. List the artifact paths you actually inspected in `reviewer_output.visual_smoke_artifacts`. Each entry needs a `description` summarizing your visual judgment.

If the screenshot artifacts the executor provided look suspect, use the Playwright MCP yourself to capture an independent screenshot before deciding.

## Testing adequacy
- Automated interaction tests exist (not just "looks right").
- Tests include: one resize regression, one unknown-input regression, one critical workflow E2E.
- For web UI, jsdom tests are necessary but not sufficient — visual smoke artifacts are the final acceptance signal.

## "Passes tests but fails in practice" traps
- Tests assert DOM structure but not rendered style → layout collapsed, invisible zones.
- Snapshot tests deterministic against broken output → concatenated-text output passes its snapshot.
- Only tested on one viewport size.
- Missing loading/error/empty states.
- TUI cleanup missing in panic paths.

## Attribution
- Plan: missing visual specifications (no token-to-renderable mapping, no modal pattern, no color rules), missing pass criteria for visual outcomes, missing edge-case requirements, unspecified lifecycle.
- Impl: incorrect implementation against clear plan; missing screenshot artifacts; visual output diverges from spec; CSS for declared classes is absent.
