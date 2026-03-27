---
name: infra-config-executor
description: Domain guidance for implementing infrastructure and configuration phases. Loaded when phase category is INFRA.
---

# INFRA/CONFIG Implementation Guidance

## Validate early, validate often
After every config edit: re-parse the file, run repo validator if one exists, then proceed. Never batch config edits without intermediate validation.

## Scaffolding discipline
- Match existing project structure — do not introduce a new layout style.
- Python: add `__init__.py` where packages are intended; keep imports consistent with layout.
- Create entrypoints aligned to repo patterns (console scripts, `__main__.py`).
- Add the smallest smoke test proving the infra change works.

## Config file pitfalls
- TOML/YAML/JSON are fragile — don't hand-wave indentation or commas; parse immediately after edits.
- Keep dependency version constraints consistent with repo style.
- Don't add a dependency unless the plan explicitly allows it.
- If manifests change, ensure lockfiles follow the repo's lockfile policy.

## Environment setup
- No hardcoded absolute paths — use repo-relative paths and env vars with defaults.
- Make env vars explicit: `.env.example` + runtime validation with clear errors.
- Scripts must be idempotent (safe to rerun on retry — no partial state).

## Retry killers to avoid
- Changing multiple config files "just in case."
- Adding new tooling the repo doesn't use.
- Leaving debug artifacts or temp scripts behind.
- Stopping after one run without verifying from clean state.
