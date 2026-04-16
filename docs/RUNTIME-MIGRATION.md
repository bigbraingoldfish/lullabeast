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

## Environment variable names (canonical only)

Two root paths steer everything. Each has a single canonical name — legacy
aliases have been removed.

| Concept                          | Env                     | UI JSON                 |
| -------------------------------- | ----------------------- | ----------------------- |
| OpenClaw install root            | `OPENCLAW_ROOT`         | `openclaw_root`         |
| AutoDev pipeline state directory | `AUTODEV_PIPELINE_ROOT` | `autodev_pipeline_root` |

Resolution order at every read site: env var → UI JSON key → built-in default
(`<repo>/.autodev`). An empty value is treated as "unset".

`install.sh` writes **only** the canonical names to `.env` on every install.

## Removed legacy names

The following were removed during the "hard cut" and are now **silently
ignored** everywhere (env var, `.env`, `ui/config.json`, and child-process
environments passed to `orchestrator.py`). If you see them in a stale file, you
can delete them — nothing reads them.

- `AUTODEV_ROOT` (legacy alias of `OPENCLAW_ROOT`)
- `AUTODEV_RUNTIME_ROOT` (legacy alias of `AUTODEV_PIPELINE_ROOT`)
- `AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME` (legacy switch)
- `autodev_runtime_root` (UI config key alias for `autodev_pipeline_root`)
- `use_legacy_openclaw_runtime` (UI config key)

## Pinning the pipeline state directory

If you want pipeline state to live outside `<repo>/.autodev` — for example in a
Docker bind-mount shared with the OpenClaw container — set the pipeline root
explicitly:

```bash
export AUTODEV_PIPELINE_ROOT=/path/to/shared/.openclaw
```

Equivalent UI configuration (`ui/config.json`):

```json
{
  "autodev_pipeline_root": "/path/to/shared/.openclaw"
}
```

To reproduce the previous behaviour where pipeline state collapsed onto the
OpenClaw root, set `AUTODEV_PIPELINE_ROOT=$OPENCLAW_ROOT` explicitly.

You can still override individual paths (`pipeline_state_path`, `lock_path`,
etc.) in `ui/config.json`; explicit keys are never overwritten by defaults.

## Orchestrator environment

When launched from the UI, the server passes only the canonical names to the
orchestrator subprocess. Any legacy aliases inherited from the parent
environment are actively scrubbed before `execve` so they cannot leak through:

- `OPENCLAW_ROOT` — OpenClaw root (config `openclaw_root`)
- `AUTODEV_REPO_PATH` — repository root
- `AUTODEV_PIPELINE_ROOT` — pipeline state dir (only emitted when the UI config
  explicitly sets a value; when the config value is blank the parent process's
  environment is preserved rather than clobbered)

CLI runs should `source .env` after `./install.sh` so the same variables are
set.
