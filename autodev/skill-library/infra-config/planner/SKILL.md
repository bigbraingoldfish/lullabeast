---
name: infra-config-planner
description: Domain guidance for planning infrastructure and configuration phases. Loaded when phase category is INFRA.
---

# INFRA/CONFIG Planning Guidance

## Decomposition checklist
Detect the actual stack before assuming. Identify on disk:
- Build system (`pyproject.toml` / `package.json` / `Cargo.toml` / `Makefile`).
- Package layout (`src/` vs flat; locate `__init__.py` / exports).
- Test runner and discovery rules (pytest / jest / cargo test).
- Formatter/linter and its config location.
- Lockfile presence and policy (committed vs gitignored).
- Constraints to preserve (stdlib-only, pinned toolchains, offline CI, target platforms).

Preferred phase order: make build/test runnable → make deterministic → improve DX. Never combine scaffolding + feature work, dependency upgrades + refactors, or build-system migration + new plugins in one phase.

## Interfaces & contracts to specify
For every config change name: the file path, the key, the old value, the new value, and what code or tool reads it. For every command introduced: the exact invocation, the working directory, and the expected exit code.

## Edge cases — must enumerate, not generalise
Clean checkout (no cached state), missing lockfile, conflicting versions across manifests, CI runner missing a tool that local dev has, offline mode, an env var the config depends on being unset.

## Pass criteria patterns
- "From clean workspace, `<install>` then `<test>` exits 0."
- All modified config files parse and validate (syntax + schema where tooling exists).
- "No new dependencies added unless explicitly listed in this phase."
- CI workflow triggers on the expected events and runs the expected jobs.
- Each task produces a verifiable artifact: a config that parses, a command that works from a clean state.

## Anti-patterns to avoid
- Assuming wrong layout (`src/` vs flat) or wrong test runner.
- Omitting lockfile policy.
- Pass criteria that check only "tests pass" without CI-semantic verification.
- Not specifying the exact install/build/test commands.
- Relying on accidental cached state on the dev machine.

## TDD test structure
Prefer smoke tests: package/import discovery, CLI entrypoint `--help`, minimal test-collection runs. For CI/config changes, specify parse/lint checks using repo-resident tools only.
