# INFRA-B1 — Phase INFRA-B1: Fix --project-path CLI argument mismatch in server.py, add prd-creator to OpenClaw config, and add all new config keys to server.py
**Completed:** 2026-03-19T15:16:00.000Z
**Duration:** unknown
**Executor attempts:** 1
**Reviewer passes:** 0

## What was built
Fixed the orchestrator spawn call in server.py to use `--project-path` instead of `--project`, added `prd-creator` to `hooks.allowedAgentIds` and `ideas:` to `hooks.allowedSessionKeyPrefixes` in `~/.openclaw/openclaw.json`, and added all four new config keys (`ideas_dir`, `hooks_url`, `hooks_token`, `conversion_prompt_path`) to both `server.py` DEFAULTS and `ui/config.json`.

## Tests
- `tests/test_infra_b1.py`: 5 tests covering all 5 pass criteria (all passing)

## Files changed
- `ui/server.py` (--project-path fix + DEFAULTS keys)
- `ui/config.json` (4 new config keys)
- `~/.openclaw/openclaw.json` (prd-creator + ideas: prefix)

## Files deleted
None.

## Lessons
None.
