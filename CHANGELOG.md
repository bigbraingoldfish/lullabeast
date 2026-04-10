# Changelog

This project follows [Semantic Versioning](https://semver.org/). First public release will be tagged `0.1.0`.

All notable changes are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added

- `SECURITY.md` — security model, vulnerability reporting process, scope, and known limitations
- `CONTRIBUTING.md` — development setup, test commands, PR conventions, skill authoring guidance
- `CHANGELOG.md` — this file

### Changed

- Pipeline Monitor: removed the header liveness dot and the **Restart Orchestrator** control from the top bar; when the orchestrator is down during a mid-flight run (`RUNNING` / `WAITING_FOR_SENTINEL`), restart is offered in the **Current Phase** column in an escalation-style panel (same flow as queue/actions messaging). `WAITING_FOR_HUMAN` still uses the escalation panel’s restart affordance only.
- `install.sh` startup echo updated to recommend loopback binding (`127.0.0.1`) rather than `0.0.0.0`

### Fixed

- `repo_init_check.py` resolves `pipeline-project` with the same runtime-root rules as the orchestrator and `phase_resolver` (default: `$AUTODEV_REPO_PATH/.autodev/pipeline-project`; legacy OpenClaw hub layout: set `AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME=1`). Fixes false “symlink not found” checks against `~/.openclaw` when using repo-local runtime.
- Orchestrator atomic writes (`phase_state`, `failure_context`, related `mkstemp` paths) fall back to `AUTODEV_RUNTIME_ROOT` (with `makedirs`) instead of `AUTODEV_ROOT` when `pipeline-project` is missing, avoiding an uncaught `FileNotFoundError` if `~/.openclaw` was never created (e.g. Docker `AUTODEV_ROOT` not loaded from `.env`).
- `autodev/tests/test_repo_init_check_paths.py`: legacy-layout cases set `AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME`; added repo-local runtime coverage. Manual skill-mode symlink tests pass the same env when invoking `repo_init_check.py`.
- `AUTODEV_RUNTIME_ROOT` and `AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME` documented in `.env.example`
- `/home/pi/` author paths replaced with neutral placeholders across UI, tests, and docs
- `.claude/settings.json` excluded from git tracking
- `requests` pinned to exact version in dependencies

### Security

- `aiohttp` bumped from 3.13.3 to 3.13.4, resolving 10 CVEs
