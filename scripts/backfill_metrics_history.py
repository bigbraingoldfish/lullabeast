#!/usr/bin/env python3
"""Backfill ``metrics_history/<project>.jsonl`` from audit archives.

Section 6.0 introduced an orchestrator-private history file at
``$AUTODEV_PIPELINE_ROOT/metrics_history/<project_name>.jsonl`` and a
first-run bootstrap that seeds it from the live ``metrics.jsonl``.  But
when the live file has *already* been truncated by an earlier executor
overwrite, the bootstrap restores only what is left — the rows lost
between snapshots are not recovered.

This one-off script reads the per-phase audit-archive directories at
``$OPENCLAW_ROOT/pipeline-audit/<project>/<phase-raw-id>/`` and
reconstructs the canonical metrics rows for any phase that is in the
archive but missing from the history file.  Output is merged
non-destructively: existing rows are preserved, only missing phases
are appended.

Usage::

    OPENCLAW_ROOT=~/.openclaw AUTODEV_PIPELINE_ROOT=/path/to/.autodev \\
    python3 scripts/backfill_metrics_history.py <project_name>

If ``--dry-run`` is passed the script prints what it would write
without modifying any files.

Idempotent: re-running after a backfill is a no-op.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone


def _open_jsonl(path: str) -> list[dict]:
    rows: list[dict] = []
    if not os.path.exists(path):
        return rows
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # Skip malformed lines — defensive, audit archives can
                # contain rows from older schemas.
                continue
    return rows


def _phases_in_history(history_path: str) -> set[str]:
    return {r.get("phase") for r in _open_jsonl(history_path) if r.get("phase")}


def _row_from_audit(audit_phase_dir: str, phase_raw_id: str) -> dict | None:
    """Reconstruct a canonical metrics row from an audit-archive
    directory.  Returns ``None`` when phase_state.json is unreadable
    (without it we cannot compute attempt counts)."""
    ps_path = os.path.join(audit_phase_dir, "phase_state.json")
    if not os.path.exists(ps_path):
        return None
    try:
        with open(ps_path) as f:
            ps = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    # Prefer pulling from the archive's own metrics.jsonl if present —
    # it has the original duration_seconds and goal text.
    archived_metrics = os.path.join(audit_phase_dir, "metrics.jsonl")
    archived_rows = _open_jsonl(archived_metrics)
    for row in archived_rows:
        if row.get("phase") == phase_raw_id:
            return row

    # Fall back to a synthesized row.
    cp_path = os.path.join(audit_phase_dir, "current_phase.json")
    goal = ""
    if os.path.exists(cp_path):
        try:
            with open(cp_path) as f:
                goal = json.load(f).get("detail", "")
        except (OSError, json.JSONDecodeError):
            pass

    return {
        "ts": datetime.fromtimestamp(
            os.path.getmtime(ps_path), tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "phase": phase_raw_id,
        "goal": goal,
        "executor_attempts": ps.get("executor_retries", 0) + 1,
        "reviewer_passes": ps.get("reviewer_retries", 0) + 1,
        "escalations": ps.get("escalations", 0),
        "skill_used": ps.get("skill_injected"),
        "planner_tokens": ps.get("planner_tokens_acc", {}) or {},
        "executor_tokens": ps.get("executor_tokens_acc", {}) or {},
        "reviewer_tokens": ps.get("reviewer_tokens_acc", {}) or {},
        "cost_total": 0.0,
        "duration_seconds": None,
        "source": "backfill",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_name", help="basename of the project directory")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written; do not modify any files.",
    )
    args = parser.parse_args()

    openclaw_root = os.path.expanduser(os.environ.get("OPENCLAW_ROOT", "~/.openclaw"))
    pipeline_root = os.path.expanduser(
        os.environ.get("AUTODEV_PIPELINE_ROOT", "")
    )
    if not pipeline_root:
        print(
            "[ERROR] AUTODEV_PIPELINE_ROOT must be set so the history "
            "file path can be resolved.",
            file=sys.stderr,
        )
        return 2

    audit_root = os.path.join(openclaw_root, "pipeline-audit", args.project_name)
    if not os.path.isdir(audit_root):
        print(f"[ERROR] No audit archive at {audit_root}", file=sys.stderr)
        return 2

    history_path = os.path.join(
        pipeline_root, "metrics_history", f"{args.project_name}.jsonl"
    )
    existing_phases = _phases_in_history(history_path)

    new_rows: list[dict] = []
    for phase_dir_name in sorted(os.listdir(audit_root)):
        audit_phase_dir = os.path.join(audit_root, phase_dir_name)
        if not os.path.isdir(audit_phase_dir):
            continue
        # Audit dirs are lowercase (per orchestrator.py:4210 archive
        # naming).  Reconstruct the canonical phase_raw_id from
        # current_phase.json or phase_state.json fields, falling back
        # to the upper-cased directory name.
        phase_raw_id = phase_dir_name.upper()
        cp_path = os.path.join(audit_phase_dir, "current_phase.json")
        if os.path.exists(cp_path):
            try:
                with open(cp_path) as f:
                    cp = json.load(f)
                phase_raw_id = cp.get("raw_id", cp.get("phase_raw_id", phase_raw_id))
            except (OSError, json.JSONDecodeError):
                pass
        if phase_raw_id in existing_phases:
            continue
        row = _row_from_audit(audit_phase_dir, phase_raw_id)
        if row is None:
            print(f"[SKIP] {phase_raw_id}: no readable phase_state.json")
            continue
        new_rows.append(row)
        print(
            f"[ADD]  {phase_raw_id}: executor_attempts="
            f"{row.get('executor_attempts')}, reviewer_passes="
            f"{row.get('reviewer_passes')}"
        )

    if not new_rows:
        print("[INFO] Nothing to backfill — history file is already complete.")
        return 0

    if args.dry_run:
        print(f"[DRY-RUN] Would append {len(new_rows)} row(s) to {history_path}")
        return 0

    os.makedirs(os.path.dirname(history_path), exist_ok=True)
    with open(history_path, "a") as f:
        for row in new_rows:
            f.write(json.dumps(row) + "\n")
    print(f"[DONE] Appended {len(new_rows)} row(s) to {history_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
