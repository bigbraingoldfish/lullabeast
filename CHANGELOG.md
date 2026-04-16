# Changelog

This project follows [Semantic Versioning](https://semver.org/). First public release will be tagged `0.1.0`.

All notable changes are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added

- `SECURITY.md` — security model, vulnerability reporting process, scope, and known limitations
- `CONTRIBUTING.md` — development setup, test commands, PR conventions, skill authoring guidance
- `CHANGELOG.md` — this file
- `autodev/pipeline/env_resolvers.py` — shared resolvers (`resolve_openclaw_root`, `resolve_pipeline_root`) used by orchestrator, heartbeat cron, gate scripts, skill/session/sentinel managers, and `ui/server.py`. Canonical env names are `OPENCLAW_ROOT` and `AUTODEV_PIPELINE_ROOT`.
- UI JSON key `autodev_pipeline_root` in `ui/config.json`.
- Startup INFO log in `orchestrator.py` and `heartbeat_cron.py`: a single line emitting the resolved `OPENCLAW_ROOT`, `AUTODEV_PIPELINE_ROOT`, and `STATE_FILE` so an operator can self-diagnose misconfigured roots without grepping env dumps.

### Changed

- Environment variable rename (hard cut — legacy aliases removed):
  - `AUTODEV_ROOT` → `OPENCLAW_ROOT`
  - `AUTODEV_RUNTIME_ROOT` → `AUTODEV_PIPELINE_ROOT`
  - `install.sh` writes only the canonical names to `.env`.
  - `.env.example` rewritten to document canonical names only.
- `RUNTIME-MIGRATION.md`, `SETUP.md`, `CLAUDE.md`, `SECURITY.md`, pipeline docs, and helper shell scripts (`scripts/queue-e2e-*.sh`, `scripts/README.md`) updated to reference canonical names only.
- Pipeline Monitor: removed the header liveness dot and the **Restart Orchestrator** control from the top bar; when the orchestrator is down during a mid-flight run (`RUNNING` / `WAITING_FOR_SENTINEL`), restart is offered in the **Current Phase** column in an escalation-style panel (same flow as queue/actions messaging). `WAITING_FOR_HUMAN` still uses the escalation panel’s restart affordance only.
- `install.sh` startup echo updated to recommend loopback binding (`127.0.0.1`) rather than `0.0.0.0`

### Removed

- `AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME` environment flag and `use_legacy_openclaw_runtime` UI config key. Both are silently ignored if they remain in stale `.env` / `config.json`. To reproduce the old behaviour, set `AUTODEV_PIPELINE_ROOT=$OPENCLAW_ROOT` explicitly. The flag's branching logic has been removed from `orchestrator.py`, `heartbeat_cron.py`, gate scripts, and `ui/server.py`.
- Legacy env-var aliases `AUTODEV_ROOT` and `AUTODEV_RUNTIME_ROOT` and the UI config alias `autodev_runtime_root`. The resolver functions in `autodev/pipeline/env_resolvers.py`, the `load_config()` layer in `ui/server.py`, the `_spawn_orchestrator` env builder (which now actively scrubs inherited legacy aliases), the `.env` writer in `install.sh`, and the pipeline module constants (`orchestrator.py`, `sentinel_poller.py`, `skill_manager.py`, `session_cleanup.py`, `heartbeat_cron.py`) have all been updated to read and emit only canonical names. Stale aliases in an inherited environment or an existing `.env` / `ui/config.json` are silently ignored. To migrate, replace any `AUTODEV_ROOT=` / `AUTODEV_RUNTIME_ROOT=` entries with `OPENCLAW_ROOT=` / `AUTODEV_PIPELINE_ROOT=`.

### Fixed

- `load_config()` / `ui/config.json`: keys present with empty-string values (as in `config.example.json`) no longer count as user overrides, so `_finalize_autodev_config_paths` still derives `pipeline_state_path`, `project_dir_path`, and related runtime paths. Fixes “pipeline_state_path is not configured” after install when placeholders were left in the file.
- `repo_init_check.py` resolves `pipeline-project` with the same runtime-root rules as the orchestrator and `phase_resolver` (default: `$AUTODEV_REPO_PATH/.autodev/pipeline-project`). Fixes false “symlink not found” checks against `~/.openclaw` when using repo-local runtime.
- Orchestrator atomic writes (`phase_state`, `failure_context`, related `mkstemp` paths) fall back to the pipeline root (with `makedirs`) instead of the OpenClaw root when `pipeline-project` is missing, avoiding an uncaught `FileNotFoundError` if `~/.openclaw` was never created (e.g. Docker `OPENCLAW_ROOT` not loaded from `.env`).
- `_spawn_orchestrator` no longer clobbers `AUTODEV_PIPELINE_ROOT` in the child process environment when the UI config value is empty — the parent environment is preserved instead. Same fix applied for `OPENCLAW_ROOT` propagation.
- `/home/pi/` author paths replaced with neutral placeholders across UI, tests, and docs
- `.claude/settings.json` excluded from git tracking
- `requests` pinned to exact version in dependencies

### Security

- `aiohttp` bumped from 3.13.3 to 3.13.4, resolving 10 CVEs
