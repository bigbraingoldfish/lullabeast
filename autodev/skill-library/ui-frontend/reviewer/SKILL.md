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

## Testing adequacy
- Automated interaction tests exist (not just "looks right").
- Tests include: one resize regression, one unknown-input regression, one critical workflow E2E.
- No manual-only verification claims accepted.

## "Passes tests but fails in practice" traps
- Only tested on one viewport size.
- Missing loading/error/empty states.
- TUI cleanup missing in panic paths.

## Attribution
- Plan: missing pass criteria, missing edge-case requirements, unspecified lifecycle.
- Impl: incorrect implementation against clear plan.
