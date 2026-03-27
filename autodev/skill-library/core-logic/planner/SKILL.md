---
name: core-logic-planner
description: Domain guidance for planning core logic and algorithm phases. Loaded when phase category is CORE.
---

# Core Logic Planning Guidance

## Scope the logic surface
- Define inputs, outputs, invariants, and failure behavior for each function.
- Mark functions as PURE (no mutation, no I/O) or STATEFUL (explicit state object). Agents default to mutation unless told otherwise.

## Edge cases are planner-owned
Enumerate explicitly — do not delegate discovery to the executor:
- Numeric: 0, 1, -1, min/max bounds, overflow if applicable.
- Collections: empty, single-element, duplicates, already-sorted/reversed.
- Nullable: None/null, missing keys, empty strings, whitespace.

## State machines: plan must be complete
Provide a transition table: current_state × event → next_state + outputs. Call out illegal transitions and expected error behavior. Incomplete tables cause executor guesswork.

## Pass criteria for logic completeness
- All branches in conditionals are exercised by tests.
- Match/switch is exhaustive (explicit default or compiler-checked).
- Illegal states are rejected deterministically.
- "Done" = all pass criteria met + no extra APIs/abstractions beyond plan.

## Prevent over-engineering
- "No new abstractions unless required by pass criteria."
- "No generalization beyond documented cases."
- "Minimize diff: touch only specified files."
- Add complexity budget if relevant (expected big-O, performance guardrails).
