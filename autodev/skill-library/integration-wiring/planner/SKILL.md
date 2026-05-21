---
name: integration-wiring-planner
description: Domain guidance for planning integration and wiring phases. Loaded when phase category is INTEGRATION or final milestones.
---

# Integration/Wiring Planning Guidance

## Decomposition checklist
- Enumerate ALL components to wire: file path, exported symbol, real signature (args + return type), side effects (startup, threads, I/O).
- Identify the true runtime entrypoint and the exact command to run it.
- Construct an explicit init graph in topological order; ban hidden init at import time.
- Specify the main loop pattern, signal handling, and cleanup ordering (reverse of init).

## Interfaces & contracts to specify
For every boundary (A → B): input type/schema, output type/schema, error contract (exceptions vs Result), ownership (who constructs and who closes what), and failure semantics at the boundary (retry / fall through / fail closed).

If event-driven: canonical event name constants, payload schema per event, and delivery semantics (ordering, at-least-once, idempotency).

Write the main loop in pseudocode the executor can implement literally: read → validate → route → execute → persist → emit → sleep/yield, with stop criteria named.

## Edge cases — must enumerate, not generalise
Component A produces but B is not yet started; A fails to construct; B raises mid-loop; SIGTERM during a request; resource leak on shutdown path; replay of the same event; configuration missing for one wired component.

## Pass criteria patterns
- "Running the real entrypoint succeeds and performs a full happy-path cycle."
- Integration test asserts module A's output is consumed by module B (not just that both ran).
- Graceful shutdown closes resources in the documented reverse-init order and exits cleanly.
- Unit tests alone are insufficient — at least one end-to-end test per wired boundary.

## Anti-patterns to avoid
- Initialisation happening as a side effect of `import`.
- Singletons created in two places.
- Listing components that are wired but not naming each one's failure semantics.
- Pass criteria that only verify each component in isolation.

## TDD test structure
Minimum: one end-to-end happy-path test through the real entrypoint, one boundary-contract test per wired pair, one shutdown-cleanup test.
