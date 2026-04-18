# Changelog

This project follows [Semantic Versioning](https://semver.org/). First public release will be tagged `0.1.0`.

All notable changes are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added

- **B-04 — Orchestrator crash context:** `GET /api/state` includes `orchestrator_spawn_log_tail` (up to five lines) when `orchestrator_alive` is false and `pipeline_status` is `RUNNING` or `WAITING_FOR_SENTINEL`, read from the same path the UI uses for spawned orchestrator stdout/stderr (`ORCHESTRATOR_SPAWN_LOG_PATH`, default `/tmp/orchestrator.log`). Pipeline Monitor **Orchestrator stopped** panel renders those lines in a `<pre>` (`data-testid="pipeline-orchestrator-spawn-log-tail"`). `POST /api/resume-orchestrator` behavior is unchanged (still returns 200 after a successful spawn without waiting for liveness). See `tests/test_api_state.py`.
- `SECURITY.md` — security model, vulnerability reporting process, scope, and known limitations
- `CONTRIBUTING.md` — development setup, test commands, PR conventions, skill authoring guidance
- `CHANGELOG.md` — this file
- `autodev/pipeline/env_resolvers.py` — shared resolvers (`resolve_openclaw_root`, `resolve_pipeline_root`) used by orchestrator, heartbeat cron, gate scripts, skill/session/sentinel managers, and `ui/server.py`. Canonical env names are `OPENCLAW_ROOT` and `AUTODEV_PIPELINE_ROOT`.
- UI JSON key `autodev_pipeline_root` in `ui/config.json`.
- Startup INFO log in `orchestrator.py` and `heartbeat_cron.py`: a single line emitting the resolved `OPENCLAW_ROOT`, `AUTODEV_PIPELINE_ROOT`, and `STATE_FILE` so an operator can self-diagnose misconfigured roots without grepping env dumps.

### Changed

- **P0-02 — Orchestrator recovery banner & escalation gating:** Pipeline Monitor shows a full-width header **recovery banner** (`data-testid="pipeline-orchestrator-recovery-banner"`) with **Restart Orchestrator** when `orchestrator_alive` is false and `pipeline_status` is **`WAITING_FOR_HUMAN`**, **`STOPPED`**, or **`QUEUE_HALTED`** with escalation context (`escalation_trigger_reason` / `escalation_message`). Mid-flight **`RUNNING`** / **`WAITING_FOR_SENTINEL`** + dead orchestrator remains in the left-column **Orchestrator stopped** panel (B-04). **EscalationCommandPanel** hides the command grid until the orchestrator is alive (`orchestratorDownBlocksCommands`); copy points to the banner. Plain **`IDLE`** + dead does not show the banner (normal idle). See `ui/index.html` and `tests/test_ui_p0_02_orchestrator_recovery_banner.py`.
- **P0-01 — Cold-start default screen:** After setup is complete, the UI runs a one-time bootstrap (`Promise.all` on `GET /api/state` and `GET /api/queue`). If `pipeline_status` is **`IDLE`** or **`UNKNOWN`** (e.g. missing `pipeline_state.json`) and no queue entry has a busy `live_pipeline_status` (`RUNNING`, `WAITING_FOR_SENTINEL`, `WAITING_FOR_HUMAN`), the app navigates to **Project Ideas** instead of landing on an empty Pipeline Monitor. **`PIPELINE_COMPLETE` and other statuses do not trigger this redirect** — a completed pipeline with an empty queue stays on the Pipeline Monitor (completion view) after refresh. Skips redirect if the user already left Pipeline before the fetch completes (`currentScreenRef`). See `ui/index.html` (`shouldOpenIdeasOnColdBootstrap`, `App` bootstrap `useEffect`) and `tests/test_ui_cold_start_default_screen.py`.
- **`POST /api/resume-orchestrator` (Policy A — B-01):** When the configured pipeline-project symlink realpath disagrees with `pipeline_state.json` `project_path`, the UI server **repoints the symlink** to match state instead of returning **409** `symlink_project_mismatch`. Successful responses include `reconciled`, `reconcile_action`, `previous_symlink_real`, and `canonical_project_real`. **422** if the link path cannot be safely replaced (e.g. real directory or file at `project_dir_path`). **503** returns a JSON body with `reconciled: true` if spawn fails after a successful repoint (operator can retry **Restart**). **409** is reserved for `pipeline.lock` / orchestrator already running. Pipeline Monitor shows a single amber header line for reconcile / spawn-after-reconcile failures (`ui/index.html`). See `autodev/docs/PIPELINE-SPEC.md` and `tests/test_api_resume_orchestrator.py`.
- Environment variable rename (hard cut — legacy aliases removed):
  - `AUTODEV_ROOT` → `OPENCLAW_ROOT`
  - `AUTODEV_RUNTIME_ROOT` → `AUTODEV_PIPELINE_ROOT`
  - `install.sh` writes only the canonical names to `.env`.
  - `.env.example` rewritten to document canonical names only.
- `RUNTIME-MIGRATION.md`, `SETUP.md`, `CLAUDE.md`, `SECURITY.md`, pipeline docs, and helper shell scripts (`scripts/queue-e2e-*.sh`, `scripts/README.md`) updated to reference canonical names only.
- Pipeline Monitor: removed the header liveness dot and the **Restart Orchestrator** control from the primary header **button row** (Resume / Stop cluster). When the orchestrator is down during a mid-flight run (`RUNNING` / `WAITING_FOR_SENTINEL`), restart is offered in the **Current Phase** column (**Orchestrator stopped** panel, B-04). When down in **`WAITING_FOR_HUMAN`**, **`STOPPED`**, or **`QUEUE_HALTED`** with escalation context, restart is offered in the **recovery banner** under the header (P0-02); the escalation command grid stays hidden until `orchestrator_alive` is true.
- `install.sh` startup echo updated to recommend loopback binding (`127.0.0.1`) rather than `0.0.0.0`

### Removed

- `AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME` environment flag and `use_legacy_openclaw_runtime` UI config key. Both are silently ignored if they remain in stale `.env` / `config.json`. To reproduce the old behaviour, set `AUTODEV_PIPELINE_ROOT=$OPENCLAW_ROOT` explicitly. The flag's branching logic has been removed from `orchestrator.py`, `heartbeat_cron.py`, gate scripts, and `ui/server.py`.
- Legacy env-var aliases `AUTODEV_ROOT` and `AUTODEV_RUNTIME_ROOT` and the UI config alias `autodev_runtime_root`. The resolver functions in `autodev/pipeline/env_resolvers.py`, the `load_config()` layer in `ui/server.py`, the `_spawn_orchestrator` env builder (which now actively scrubs inherited legacy aliases), the `.env` writer in `install.sh`, and the pipeline module constants (`orchestrator.py`, `sentinel_poller.py`, `skill_manager.py`, `session_cleanup.py`, `heartbeat_cron.py`) have all been updated to read and emit only canonical names. Stale aliases in an inherited environment or an existing `.env` / `ui/config.json` are silently ignored. To migrate, replace any `AUTODEV_ROOT=` / `AUTODEV_RUNTIME_ROOT=` entries with `OPENCLAW_ROOT=` / `AUTODEV_PIPELINE_ROOT=`.

### Fixed

- `test_idea_watch_dir_resets_idle_despite_stale_jsonl`: mock `_idea_workspace_activity_mtime` instead of scanning real files under `idea/` so the Ideas idle poller unit test stays fast and deterministic under patched `time.monotonic` / `asyncio.sleep` (avoids FS hot-loop on slow hosts).
- `load_config()` / `ui/config.json`: keys present with empty-string values (as in `config.example.json`) no longer count as user overrides, so `_finalize_autodev_config_paths` still derives `pipeline_state_path`, `project_dir_path`, and related runtime paths. Fixes “pipeline_state_path is not configured” after install when placeholders were left in the file.
- `repo_init_check.py` resolves `pipeline-project` with the same runtime-root rules as the orchestrator and `phase_resolver` (default: `$AUTODEV_REPO_PATH/.autodev/pipeline-project`). Fixes false “symlink not found” checks against `~/.openclaw` when using repo-local runtime.
- Orchestrator atomic writes (`phase_state`, `failure_context`, related `mkstemp` paths) fall back to the pipeline root (with `makedirs`) instead of the OpenClaw root when `pipeline-project` is missing, avoiding an uncaught `FileNotFoundError` if `~/.openclaw` was never created (e.g. Docker `OPENCLAW_ROOT` not loaded from `.env`).
- `_spawn_orchestrator` no longer clobbers `AUTODEV_PIPELINE_ROOT` in the child process environment when the UI config value is empty — the parent environment is preserved instead. Same fix applied for `OPENCLAW_ROOT` propagation.
- `/home/pi/` author paths replaced with neutral placeholders across UI, tests, and docs
- `.claude/settings.json` excluded from git tracking
- `requests` pinned to exact version in dependencies

### Security

- `aiohttp` bumped from 3.13.3 to 3.13.4, resolving 10 CVEs
