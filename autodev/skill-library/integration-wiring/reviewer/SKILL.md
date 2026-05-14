---
name: integration-wiring-reviewer
description: Domain guidance for reviewing integration and wiring phases. Loaded when phase category is INTEGRATION or final milestones.
---

# Integration/Wiring Review Guidance

## What to verify
- Imports: correct paths, no circular imports, __init__.py changes minimal and intentional.
- Interface contracts: producer output matches consumer input, adapters exist where types differ, signature changes propagated.
- Init order: explicit composition root, no hidden side effects at import time, runtime starts after init completes.
- Main loop: correct structure, graceful shutdown, no infinite-loop or endless-read behavior.

## How to test (independently)
- Run the actual entrypoint command (not ad hoc module import).
- Run integration tests that start the composition root.
- Confirm at least one full happy-path cycle completes.
- Confirm shutdown works cleanly (no hung processes).

## End-to-end visual smoke (for projects with rendered UI)

For any integration phase that pulls together rendered output (web app, TUI, desktop app), tests passing is necessary but not sufficient. Tests confirm modules wire together; they do not confirm the wired system *looks and behaves like the PRD describes*.

On INT phases for rendered-UI projects, you must:

1. Read `executor_output.visual_smoke_artifacts` and load each screenshot directly. You are multimodal.
2. Compare the rendered output against the PRD's described user experience end-to-end. Initial load, primary user workflows, overlays/menus/dialogs, and terminal/success states — does what you see match what a user reading the PRD would expect?
3. Re-check that upstream visual phases are still healthy. The first integration phase is the last clean rejection point in the pipeline; if a previously-approved UI phase regressed because of integration wiring, this is where it must be caught. If a Done Criteria item approved in an earlier UI phase (e.g. "the primary view renders the documented zones with their content visible") is no longer visible in the integration screenshot, reject the integration phase with a blocking issue attributing the regression to `impl` and citing the upstream phase ID.
4. Set `visual_verification` to `pass`, `fail`, or `cannot_verify` as described in the ui-frontend reviewer skill.

If the executor did not capture screenshots for an integration phase that renders a UI, treat that as `cannot_verify` and reject — integration without a visual smoke is the precise omission that lets visually broken pipelines ship.

## Common "looks fine" bugs
Unit tests pass but entrypoint fails due to: missing registration, mismatched payload schema, init order race, import-time side effects. "Fixes" that pass tests but break other code paths. Wired modules whose rendered output bears no resemblance to the PRD.

## Attribution
- Plan: interfaces underspecified, init order not specified, pass criteria didn't require end-to-end run, no visual smoke required on a rendered-UI integration.
- Impl: wiring differs from contract, imports/paths wrong despite clear plan, missing registration or cleanup, screenshots not captured.
