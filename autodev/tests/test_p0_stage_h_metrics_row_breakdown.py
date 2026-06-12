"""P0 Stage H — canonical metrics row schema + invariant.

Stage H redefines the source of ``executor_attempts`` in the canonical
metrics row:

* Was: ``self.state.get("executor_retries", 0) + 1`` — the per-segment
  count, which under-reports total attempts when reviewer rejections
  resetted the executor budget.
* Now: ``self_failures + reviewer_rejections + 1`` from the new lifetime
  counters, so the invariant
  ``executor_attempts == executor_self_failures + executor_reviewer_rejections + 1``
  holds by construction.

Also adds two new top-level fields to the JSONL row:
``executor_self_failures``, ``executor_reviewer_rejections``.

Pattern: drive ``_write_canonical_metrics_row()`` with seeded
phase_state and inspect the written JSONL row. Mirrors the existing
``test_w1_metrics_row_counters.py`` runtime tests that drive the writer.
"""

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402


def _drive_writer(
    tmp_path,
    monkeypatch,
    *,
    executor_retries=0,
    reviewer_retries=0,
    self_failure_retries=0,
    rejection_retries=0,
):
    """Set up a bare orchestrator with the supplied counters seeded into
    both phase_state and self.state, run _write_canonical_metrics_row,
    and return the parsed JSON row written to metrics.jsonl."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pipeline_root = tmp_path / "pipeline_root"
    pipeline_root.mkdir()

    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(artifacts))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(pipeline_root))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(artifacts / "phase_state.json"))

    (artifacts / "phase_state.json").write_text(json.dumps({
        "executor_retries": executor_retries,
        "executor_self_failure_retries": self_failure_retries,
        "executor_reviewer_rejection_retries": rejection_retries,
        "reviewer_retries": reviewer_retries,
        "planner_tokens_acc": {},
        "executor_tokens_acc": {},
        "reviewer_tokens_acc": {},
        "escalations": 0,
        "skill_injected": "core-logic",
    }))
    (artifacts / "current_phase.json").write_text(json.dumps({
        "raw_id": "CORE-E1",
        "detail": "Phase CORE-E1: bring up tasks view",
    }))

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {
        "current_phase": 1,
        "current_phase_raw_id": "CORE-E1",
        "executor_retries": executor_retries,
        "reviewer_retries": reviewer_retries,
        "executor_self_failure_retries": self_failure_retries,
        "executor_reviewer_rejection_retries": rejection_retries,
        "phase_start_time": "2026-05-22T00:00:00+00:00",
    }
    orch.openclaw_config = {}
    orch.lock_fd = None

    orch._write_canonical_metrics_row()

    metrics_path = artifacts / "metrics.jsonl"
    assert metrics_path.exists(), "metrics.jsonl must be written"
    lines = [l for l in metrics_path.read_text().splitlines() if l.strip()]
    assert lines, "metrics.jsonl must contain at least one row"
    return json.loads(lines[-1]), artifacts, pipeline_root


def test_metrics_row_includes_new_counter_fields(tmp_path, monkeypatch):
    """Both ``executor_self_failures`` and ``executor_reviewer_rejections``
    must appear as top-level keys in the canonical metrics row."""
    row, _, _ = _drive_writer(
        tmp_path, monkeypatch,
        self_failure_retries=2, rejection_retries=3,
    )
    assert "executor_self_failures" in row, (
        'Canonical metrics row must include "executor_self_failures" so '
        "the dashboard can surface the breakdown of retry sources."
    )
    assert "executor_reviewer_rejections" in row, (
        'Canonical metrics row must include "executor_reviewer_rejections" '
        "for the same reason."
    )
    assert row["executor_self_failures"] == 2
    assert row["executor_reviewer_rejections"] == 3


def test_executor_attempts_recomputed_from_new_counters(tmp_path, monkeypatch):
    """The bug Stage H fixes: today, ``executor_attempts`` reads from
    ``executor_retries + 1``. After a reviewer rejection resets
    ``executor_retries`` to 0, that produces ``executor_attempts = 1``
    even though several attempts ran. Stage H re-sources it from the
    lifetime counters so the count is honest."""
    # Simulate: 2 self-failures + 1 rejection + 1 post-rejection segment
    # attempt that just succeeded. Total = 4 attempts. Legacy
    # executor_retries == 0 (the post-rejection segment).
    row, _, _ = _drive_writer(
        tmp_path, monkeypatch,
        executor_retries=0,
        self_failure_retries=2,
        rejection_retries=1,
    )
    assert row["executor_attempts"] == 4, (
        f"executor_attempts must be sourced from the lifetime counters: "
        f"self_failures(2) + rejections(1) + 1 = 4. Got {row['executor_attempts']}. "
        "If this fails, the writer is still reading the per-segment "
        "executor_retries field, which under-reports actual attempts "
        "across reviewer rejections."
    )


def test_invariant_holds_for_zero_retries(tmp_path, monkeypatch):
    """Fresh phase, no retries: executor_attempts == 1, both new counters 0."""
    row, _, _ = _drive_writer(
        tmp_path, monkeypatch,
        self_failure_retries=0, rejection_retries=0,
    )
    assert row["executor_attempts"] == 1
    assert row["executor_self_failures"] == 0
    assert row["executor_reviewer_rejections"] == 0
    # Sanity invariant.
    assert (row["executor_attempts"] ==
            row["executor_self_failures"] + row["executor_reviewer_rejections"] + 1)


def test_invariant_holds_for_self_failure_only(tmp_path, monkeypatch):
    row, _, _ = _drive_writer(
        tmp_path, monkeypatch,
        self_failure_retries=3, rejection_retries=0,
    )
    assert row["executor_attempts"] == 4
    assert (row["executor_attempts"] ==
            row["executor_self_failures"] + row["executor_reviewer_rejections"] + 1)


def test_invariant_holds_for_rejection_only(tmp_path, monkeypatch):
    row, _, _ = _drive_writer(
        tmp_path, monkeypatch,
        self_failure_retries=0, rejection_retries=2,
    )
    assert row["executor_attempts"] == 3
    assert (row["executor_attempts"] ==
            row["executor_self_failures"] + row["executor_reviewer_rejections"] + 1)


def test_invariant_holds_for_mixed(tmp_path, monkeypatch):
    """The general case: a mix of self-failures and reviewer rejections."""
    row, _, _ = _drive_writer(
        tmp_path, monkeypatch,
        self_failure_retries=2, rejection_retries=3,
    )
    assert row["executor_attempts"] == 6
    assert (row["executor_attempts"] ==
            row["executor_self_failures"] + row["executor_reviewer_rejections"] + 1)


def test_metrics_row_persists_in_both_files(tmp_path, monkeypatch):
    """Both ``metrics_history/<project>.jsonl`` (orchestrator-private) and
    the project's live ``metrics.jsonl`` must carry the new fields."""
    row, artifacts, pipeline_root = _drive_writer(
        tmp_path, monkeypatch,
        self_failure_retries=1, rejection_retries=1,
    )

    # Project-visible metrics.jsonl
    live_lines = [
        l for l in (artifacts / "metrics.jsonl").read_text().splitlines()
        if l.strip()
    ]
    live_row = json.loads(live_lines[-1])
    assert live_row.get("executor_self_failures") == 1
    assert live_row.get("executor_reviewer_rejections") == 1

    # Orchestrator-private history (path derived from project basename)
    project_name = os.path.basename(os.path.realpath(str(artifacts)))
    history_path = pipeline_root / "metrics_history" / f"{project_name}.jsonl"
    assert history_path.exists(), (
        f"Orchestrator-private history file must be written at "
        f"{history_path} — without it the history is lost when the "
        "executor (or anything else) overwrites the live metrics.jsonl."
    )
    hist_lines = [l for l in history_path.read_text().splitlines() if l.strip()]
    hist_row = json.loads(hist_lines[-1])
    assert hist_row.get("executor_self_failures") == 1
    assert hist_row.get("executor_reviewer_rejections") == 1
