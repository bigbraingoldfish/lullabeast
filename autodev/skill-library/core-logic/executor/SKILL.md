---
name: core-logic-executor
description: Domain guidance for implementing core logic and algorithm phases. Loaded when phase category is CORE.
---

# Core Logic Implementation Guidance

## TDD order for logic
1. Edge cases + invalid inputs
2. Boundary conditions (off-by-one zones)
3. Happy path
4. Scale/performance sanity (if plan specifies)

## Pure function rules (when plan says PURE)
- No mutation of input arguments — return new objects.
- No I/O, logging, time, randomness, or environment reads.
- No module-level globals for shared state; inject explicitly.

## State machine discipline
- Implement transition function as single source of truth.
- Test every valid transition AND every illegal transition.
- If plan requires immutability: return NEW state object, do not mutate input.

## Algorithm correctness checklist
Before finalizing, verify:
- Index math at boundaries (0, len-1, empty).
- Inclusive vs exclusive ranges.
- Stable behavior on duplicates/ties if relevant.
- Big-O target from plan met (don't brute-force unless allowed).

## Avoid over-engineering
- Implement minimum that satisfies pass_criteria.
- No extra layers, no new frameworks, no "future-proofing."
- If tempted to refactor: stop and re-read plan constraints.

## Do NOT weaken assertions
If tests fail, fix the root cause. Do not "fix" by loosening assertions unless plan explicitly allows it.
