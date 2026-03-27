---
name: infra-config-planner
description: Domain guidance for planning infrastructure and configuration phases. Loaded when phase category is INFRA.
---

# INFRA/CONFIG Planning Guidance

## Preflight: detect before assuming
Identify the actual stack from files on disk:
- Build system: pyproject.toml / package.json / Cargo.toml / Makefile
- Package layout: src/ vs flat; locate `__init__.py` / exports
- Test runner + discovery rules (pytest / jest / cargo test)
- Formatter/linter and its config location
- Lockfile presence and policy (committed vs gitignored)
- Constraints to preserve: stdlib-only, pinned toolchains, offline CI, target platforms

## Decomposition rules
- Preferred order: make build/test runnable → make deterministic → improve DX.
- Never combine scaffolding + feature work, dependency upgrades + refactors, or build-system migration + new plugins in one phase.
- Each task must produce a verifiable artifact: a config that parses, a command that works from clean state.

## Pass criteria patterns
- "From clean workspace, `<install>` then `<test>` exits 0."
- "All modified config files parse and validate (syntax + schema if tooling exists)."
- "No new dependencies added unless explicitly listed."
- "CI workflow triggers on expected events and runs expected jobs."
- Always require clean-state verification — agents rely on accidental cached state.

## TDD for infra
Prefer smoke tests: package/import discovery, CLI entrypoint `--help`, minimal test collection runs. For CI/config changes, specify parse/lint checks using existing repo tools only.

## Planning traps that burn retries
- Assuming wrong layout (src vs flat) or wrong test runner.
- Omitting lockfile policy.
- Writing pass_criteria that check only "tests pass" without CI semantic verification.
- Not specifying the exact commands for install/build/test.
