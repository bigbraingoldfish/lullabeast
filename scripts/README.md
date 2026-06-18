# scripts/

## `backfill_metrics_history.py`

One-off operator recovery tool. Rebuilds `metrics_history/<project>.jsonl` from the
per-phase audit archives under `$OPENCLAW_ROOT/pipeline-audit/<project>/` — recovering
canonical metrics rows lost when the live `metrics.jsonl` was truncated by an executor
overwrite. The merge is non-destructive (existing rows preserved, only missing phases
appended) and idempotent, so re-running after a backfill is a no-op.

```bash
OPENCLAW_ROOT=~/.openclaw AUTODEV_PIPELINE_ROOT=/path/to/.autodev \
  python3 scripts/backfill_metrics_history.py <project_name> [--dry-run]
```
