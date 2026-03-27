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

## Common "looks fine" bugs
Unit tests pass but entrypoint fails due to: missing registration, mismatched payload schema, init order race, import-time side effects. "Fixes" that pass tests but break other code paths.

## Attribution
- Plan: interfaces underspecified, init order not specified, pass criteria didn't require end-to-end run.
- Impl: wiring differs from contract, imports/paths wrong despite clear plan, missing registration or cleanup.
