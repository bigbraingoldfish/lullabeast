---
name: testing-quality-reviewer
description: Domain guidance for reviewing E2E testing and test infrastructure phases. Loaded when phase category is TEST or E2E.
---

# Testing/Quality Review Guidance

## Shallow test checks (blockers)
- Tests that only assert True, check imports, or check "no exception."
- Tests that mirror implementation logic (copy-paste of production code).
- Assertions only on mocks/stubs rather than system outputs.
- Integration/E2E tests that mock core internals.

## Mock/fixture quality (blockers)
- Mocks without interface constraints (accept any attribute).
- Fixtures with toy data that ignores schemas/invariants.
- Shared mutable fixture state (cross-test coupling).
- Missing teardown/cleanup ownership.

## Isolation verification
Require evidence of: shuffled/random order run, repeated E2E reruns (5x) for flake detection. Look for env var leakage, filesystem leakage, hardcoded ports/paths, external network reliance.

## "Do tests catch bugs?"
Require at least one negative control: temporarily break key behavior, confirm test fails. If none exists, request one before approving.

## Attribution
- Plan: missing infrastructure utilities, fixtures, or CI config.
- Impl: plan sound but tests are shallow, flaky, or leaky.
