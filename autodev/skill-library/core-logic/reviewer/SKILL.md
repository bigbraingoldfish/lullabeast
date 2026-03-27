---
name: core-logic-reviewer
description: Domain guidance for reviewing core logic and algorithm phases. Loaded when phase category is CORE.
---

# Core Logic Review Guidance

## What to audit
- State: mutated when should be immutable? Transitions complete? Illegal transitions handled?
- Purity: "pure" functions mutate inputs or touch globals? Hidden time/random/env deps?
- Completeness: all branches reachable and covered? Silent fallthrough in switch/match?
- Edge cases: empty/single-element, null/None, zero/negative, large inputs.

## Catch false negatives
- Tests that only assert "not None" or "no crash."
- Tests that replicate implementation logic (tautological).
- Tests that never hit error branches.
- Performance regressions hidden by tiny fixtures.

## Independent validation
Add 1-3 reviewer-only checks: a missing-edge-case test, a state-transition test, a boundary-condition test. Ensure assertions verify outputs/state, not just "no exception."

## Attribution
- Plan: requirements ambiguous, missing edge cases, pass criteria allow multiple interpretations, state/purity constraints not specified.
- Impl: plan clear but code violates it — mutation, missing branches, wrong boundaries.

## Scope enforcement
If pass criteria are met, reject extra refactors/abstractions not in scope.
