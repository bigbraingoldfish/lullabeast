---
name: integration-wiring-planner
description: Domain guidance for planning integration and wiring phases. Loaded when phase category is INTEGRATION or final milestones.
---

# Integration/Wiring Planning Guidance

## Before you plan
Enumerate ALL components to wire: file path, exported symbol, real signature (args + return type), side effects (startup, threads, I/O). Identify the true runtime entrypoint and exact command to run it.

## Interface contracts (specify explicitly)
For every boundary (A → B): input type/schema, output type/schema, error contract (exceptions vs Result), ownership (who constructs/closes what).

If event-driven: canonical event names (constants), payload schema per event, delivery semantics (ordering, at-least-once, idempotency).

## Initialization ordering
Write an explicit init graph: constructors/factories in topological order, required prerequisites (env, config, filesystem), singletons vs per-run objects. Ban "hidden init" at import time — initialization in explicit boot code only.

## Main loop specification
Choose loop pattern and write pseudocode: read → validate → route → execute → persist → emit → sleep/yield. Include stop criteria, signal handling, cleanup ordering (reverse of init).

## Pass criteria (must be end-to-end)
- "Running real entrypoint succeeds and performs full happy-path cycle."
- "Integration test asserts module A output consumed by module B."
- "Graceful shutdown closes resources and exits cleanly."
- Unit tests alone are insufficient for integration phases.
