# AutoDev runtime migration (repo-local `.autodev/`)

## What changed

Pipeline runtime files that were previously defaulted under `~/.openclaw/` now default under the git repository:

- `pipeline_state.json`
- `pipeline.lock`
- `pipeline_queue.json`
- `pipeline_events.jsonl`
- `ideas/` (idea sessions, PRD drafts, conversion outputs)
- `pipeline-project` (symlink to the active target project)

Default directory: `<AUTODEV_REPO_PATH>/.autodev/`

OpenClaw-owned files remain under `openclaw_root` (typically `~/.openclaw/`):

- `openclaw.json`
- `workspace-{agent}/` (agent identity + skills injection targets)
- `agents/*/sessions/` (OpenClaw session store)

## Rollback / legacy mode

Set **either**:

- Environment: `AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME=1`, or
- `ui/config.json`: `"use_legacy_openclaw_runtime": true`

Then runtime paths default back to `openclaw_root` (same layout as before migration).

You can still override individual paths (`pipeline_state_path`, `lock_path`, etc.) in `ui/config.json`; explicit keys are never overwritten by defaults.

## Orchestrator environment

When launched from the UI, the server passes:

- `AUTODEV_ROOT` — OpenClaw root (config `openclaw_root`)
- `AUTODEV_REPO_PATH` — repository root
- `AUTODEV_RUNTIME_ROOT` — resolved runtime directory (usually `<repo>/.autodev`)

CLI runs should `source .env` after `./install.sh` so the same variables are set.
