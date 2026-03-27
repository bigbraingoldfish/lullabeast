---
name: testing-quality-planner
description: Domain guidance for planning E2E testing and test infrastructure phases. Loaded when phase category is TEST or E2E.
---

# Testing/Quality Planning Guidance

## Planning outputs
- Identify real entry point(s) under test (CLI, HTTP, UI flow). No helper-only tests.
- Define system boundary and allowed doubles: OK to fake external network, clock, third-party APIs. NOT OK to mock internal domain logic, validation, persistence (unless phase is explicitly unit-only).
- Specify test layers (integration vs E2E); keep E2E count small.

## Test infrastructure as first-class deliverables
Required files: conftest.py (shared fixtures + cleanup), test utilities (helpers for starting app, making requests, asserting), test data factories (schema-valid), cleanup mechanisms (DB truncate, tmp dirs, env reset).

For each fixture: specify scope (default function), cleanup ownership, what state it may mutate.

## E2E test scope
Each test validates a full user-visible flow: input → system boundary → observable output. Assert on observable outputs, not internal state or mock call counts.

## Pass criteria for the test infrastructure itself
- "Tests can fail": include deliberate negative control (break behavior, confirm suite catches it).
- "Tests are deterministic": N-repeat run with zero flakes.
- "Tests are isolated": order-randomized run without failures.

## Anti-patterns to block
Plans that use sleep as synchronization, hardcode ports/paths, omit cleanup ownership, rely on live network, or grow E2E count instead of building shared fixtures.
