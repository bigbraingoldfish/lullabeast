---
name: testing-quality-executor
description: Domain guidance for implementing E2E testing and test infrastructure phases. Loaded when phase category is TEST or E2E.
---

# Testing/Quality Implementation Guidance

## Golden rule
Tests must fail if the system is broken. No "it runs" checks.

## Fixtures and data
- Shared fixtures in conftest.py. Default scope: function. Session scope only for immutable, expensive resources.
- Factories generate schema-valid, constraint-valid data.
- Never share mutable objects across tests unless deep-copied.

## Mock discipline
- Mock at boundaries only (network, clock, filesystem, third-party).
- Strict mocks that enforce interface contracts (no permissive "anything goes").
- Reset/clear mocks between tests (especially global monkeypatches).

## Isolation
- Filesystem: per-test temp dirs. Never hardcode /tmp paths.
- Env vars: safe patch helpers; always restore.
- Ports: ephemeral/dynamic; never hardcode.
- Databases: transaction rollback or truncate between tests.

## E2E construction
- Start the SUT the way a user does (real CLI command, real HTTP surface).
- Assert on observable outputs only (responses, persisted state, emitted messages).
- Avoid asserting internal steps or mock call counts unless required.

## Flake avoidance
Never add sleep() to "fix" flake. Use framework-native waits. If intermittent: reproduce, identify unstable dependency, fix root cause.
