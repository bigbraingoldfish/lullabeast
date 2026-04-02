# scripts/

## `queue-e2e-reset-test-projects.sh`

Resets Pi **queue-test** repos used for strict queue E2E validation:

- Backs up `~/.openclaw/pipeline_queue.json` and `pipeline_state.json` with a timestamp suffix.
- For `queue-test1` … `queue-test5` under `/home/pi/projects` (override with `PROJECTS_ROOT`): removes common pipeline artifact files, normalizes **git** on **`main`** with at least one commit, writes default **roadmap A** (single pending `CORE-E1`) and **`roadmap.B.v1-complete.md`** for **V1** startup-complete tests.
- Optional **`--create-phaseonly`**: creates `/home/pi/projects/queue-test-phaseonly` with only branch **`phase/demo`** (no `main`/`master`) for **V4** preflight **warn** path.

See [plans/Active/queue-e2e-manual-validation/00-source-of-truth.md](../plans/Active/queue-e2e-manual-validation/00-source-of-truth.md) (symlink / git invariants).

```bash
# From autodev-ui repo root
./scripts/queue-e2e-reset-test-projects.sh
./scripts/queue-e2e-reset-test-projects.sh --create-phaseonly
```

## `queue-e2e-strict-freeze.sh`

Timestamped copies of `pipeline_queue.json`, `pipeline_state.json`, and a `readlink` snapshot of `pipeline-project` under `$AUTODEV_ROOT` (default `~/.openclaw`). Use before/after strict dual validations; restore with `cp` + `ln -sfn "$(cat …readlink)"`.
