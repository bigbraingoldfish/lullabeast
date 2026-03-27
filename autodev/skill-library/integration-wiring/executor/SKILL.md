---
name: integration-wiring-executor
description: Domain guidance for implementing integration and wiring phases. Loaded when phase category is INTEGRATION or final milestones.
---

# Integration/Wiring Implementation Guidance

## Hard rule
Do not write wiring code until you have read: the current file tree, the real interfaces of every module you will connect, and the current entrypoint invocation.

## Import safety
- Verify imports against actual paths on disk (no guessing).
- Prefer absolute imports; avoid ambiguous relative imports.
- Confirm __init__.py exists where expected; keep it minimal with no import-time side effects.

## Wiring pattern
- Build a single composition root (one place where objects are constructed).
- Pass dependencies explicitly (constructor args or factory params).
- Avoid globals for cross-component references.

## Initialization ordering
- Initialize in topological order: config/env → logging → core services → adapters → main loop.
- Ensure each dependency available before constructing dependents.
- No work starts (threads, loops, network) until after init completes.

## Main loop discipline
- Choose canonical loop pattern; avoid blocking calls inside non-blocking loops.
- Implement stop criteria, SIGINT/SIGTERM handling, cleanup in reverse init order.

## Testing
- Always run the real entrypoint after wiring changes.
- Add at least one integration test: construct composition root, run full happy-path, assert cross-component effects.
- If failure occurs, reproduce via entrypoint first, then isolate.

## Anti-patterns
- "Each component works in isolation" without end-to-end run.
- Fixing symptoms (extra imports) without confirming runtime path.
- Adding code instead of adding validation/adapters at boundaries.
