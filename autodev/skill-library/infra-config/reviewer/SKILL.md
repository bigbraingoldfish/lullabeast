---
name: infra-config-reviewer
description: Domain guidance for reviewing infrastructure and configuration phases. Loaded when phase category is INFRA.
---

# INFRA/CONFIG Review Guidance

## Beyond "tests pass"
Infra success = reproducibility + correct semantics. Verify:
- Clean-state reproducibility (fresh env → install → build → test).
- Determinism (toolchain pins, lockfile consistency).
- Minimality (matches plan; small surface area; no extra tooling).

## Config validation
- Syntax: YAML indentation + key correctness, TOML nesting + key spelling, JSON no trailing commas.
- Completeness: required env vars documented, new scripts referenced and executable.
- Silent failure checks: CI triggers unchanged, required jobs not removed, matrix not narrowed.

## Common false positives (don't block)
- Lockfile diffs that are necessary and consistent with manifest changes.
- Whitespace changes from repo formatter.
- CI refactors that preserve semantics.

## Attribution rubric
- Plan error: wrong build system assumptions, missing constraints, unverifiable pass_criteria.
- Impl error: invalid config syntax/schema, hardcoded paths, lockfile drift, scope creep.
- Infra noise: sandbox/resource differences, flaky upstreams, nondeterministic failures — still block but route to orchestrator with evidence.
