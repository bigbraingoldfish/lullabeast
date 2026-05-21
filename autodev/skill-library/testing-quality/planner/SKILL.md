---
name: testing-quality-planner
description: Domain guidance for planning E2E testing and test infrastructure phases. Loaded when phase category is TEST or E2E.
---

# Testing/Quality Planning Guidance

## Decomposition checklist
- Identify the real entry point(s) under test (CLI, HTTP, UI flow). No helper-only tests.
- Define the system boundary and the allowed doubles: it is OK to fake external network, the clock, and third-party APIs; it is NOT OK to mock internal domain logic, validation, or persistence (unless the phase is explicitly unit-only).
- Treat test infrastructure as first-class deliverables: `conftest.py` (shared fixtures + cleanup), test utilities, test data factories (schema-valid), cleanup mechanisms (DB truncate, tmp dirs, env reset).
- Keep the E2E test count small; lean on shared fixtures rather than copy-paste tests.

## Interfaces & contracts to specify
Pin the test runner command (exact invocation), the coverage target if any, the fixture scope (default function), fixture cleanup ownership, and what state each fixture may mutate. Specify per-test layer (unit / integration / E2E) and what each layer is allowed to touch.

## Edge cases — must enumerate, not generalise
Test run on a cold machine (no caches), test order randomised, two tests sharing a tmp dir, a flake reproducer (run-N times), network unavailable, time-dependent assertion crossing midnight UTC.

## Pass criteria patterns
- "Tests can fail": include a deliberate negative control (break behaviour, confirm the suite catches it).
- "Tests are deterministic": N-repeat run with zero flakes.
- "Tests are isolated": order-randomised run with no failures.
- Coverage threshold (if used) is enforced in the test command itself, not as a manual check.

## Anti-patterns to avoid
- Sleep used as synchronisation.
- Hard-coded ports or paths.
- Cleanup ownership omitted (a fixture that mutates state and does not restore it).
- Tests that rely on live network.
- Growing the E2E count instead of building shared fixtures.
- Assertions on internal state or mock call counts when an observable output is available.

## TDD test structure
Minimum: one E2E test per user-visible flow asserting on observable outputs, one negative-control test, one order-randomised CI invocation.
