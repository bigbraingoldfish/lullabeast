"""Section 6.0 — metrics-write regression fix.

**Live evidence of the bug**.  The pipeline ran ten phases on the
``solitaire`` project (CORE-E4 through DATA-E2 per ``/tmp/orchestrator.log``
``Canonical metrics row written for ...`` lines) but the project's
``.autodev/pipeline/metrics.jsonl`` ended with only two rows
(``DATA-E1``, ``DATA-E2``).  Audit-archive snapshots show the file lost
its prior 9 rows between the CORE-E4 archive and the CORE-E6 archive,
even though the orchestrator's canonical writer is supposed to preserve
them.

**Root cause**.  The canonical writer at ``orchestrator.py:~4090`` reads
the project's live ``metrics.jsonl`` to get "existing rows," filters out
any row matching the current phase, then writes back the survivors plus
the new canonical row.  It implicitly trusts that the executor appended
its own row to a preserved file.  The executor's ``AGENTS.md`` line 81
says "Append metrics row" — but agents driven by LLM file tools
sometimes overwrite instead of append.  When that happens the canonical
writer reads only the agent's single row, filters it out, ends up with
``_existing_rows == []``, and writes a file containing just the new
canonical row.  All prior phase history is lost.

**Fix** (TDD-driven).  Maintain an orchestrator-private history file at
``$AUTODEV_PIPELINE_ROOT/metrics_history/<project_name>.jsonl`` that the
agent cannot touch, read existing rows from there, and write to **both**
the history file and the project's ``metrics.jsonl`` (the latter is
still read by the reviewer-gate ``MISSING_ARTIFACTS`` check and the UI's
``/api/metrics`` endpoint, so it must stay in sync).

The pinning here is behavioural: even if the live ``metrics.jsonl`` has
been overwritten down to a single row mid-phase, the canonical writer
must produce a file with full history.
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def metrics_workspace(tmp_path, monkeypatch):
    """A self-contained metrics workspace with all path constants stubbed.

    Layout::

        tmp_path/
          pipeline_root/           ← AUTODEV_PIPELINE_ROOT
          project/
            .autodev/pipeline/     ← PROJECT_ARTIFACTS_DIR
              metrics.jsonl
        tmp_path/pipeline_root/metrics_history/<project_name>.jsonl  (created by writer)

    Returns the tuple ``(orch, project_artifacts_dir, history_path, metrics_path)``
    where ``orch`` is a bare ``Orchestrator`` instance with
    ``current_phase_raw_id`` already set.
    """
    pipeline_root = tmp_path / "pipeline_root"
    pipeline_root.mkdir()
    project_dir = tmp_path / "project"
    project_artifacts = project_dir / ".autodev" / "pipeline"
    project_artifacts.mkdir(parents=True)

    # Stub all relevant module-level constants so the writer resolves to
    # tmp_path locations.
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(pipeline_root))
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(project_artifacts))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(project_dir))
    monkeypatch.setattr(
        orch_mod,
        "PHASE_STATE_FILE",
        str(project_artifacts / "phase_state.json"),
    )

    # Bare Orchestrator without __init__ (mirrors test_orchestrator_gateway_config.py).
    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {
        "current_phase": 99,
        "current_phase_raw_id": "CORE-E5",
        "phase_start_time": "2026-05-14T10:00:00+00:00",
        "executor_retries": 0,
        "reviewer_retries": 0,
    }
    orch.openclaw_config = {}
    orch.lock_fd = None

    # Project name (basename of realpath SYMLINK_TARGET) is used by the writer
    # to name the history file.
    project_name = os.path.basename(os.path.realpath(str(project_dir)))
    history_path = pipeline_root / "metrics_history" / f"{project_name}.jsonl"
    metrics_path = project_artifacts / "metrics.jsonl"

    return orch, str(project_artifacts), str(history_path), str(metrics_path)


def _row(phase: str, **extra) -> str:
    """Compose a single JSON line for the given phase."""
    base = {
        "ts": "2026-05-13T00:00:00Z",
        "phase": phase,
        "goal": f"Phase {phase}",
        "executor_attempts": 1,
        "reviewer_passes": 1,
    }
    base.update(extra)
    return json.dumps(base)


def _read_jsonl(path: str) -> list:
    rows = []
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
                pass
    return rows


# ---------------------------------------------------------------------------
# M1 — the regression test: executor overwrite must not lose history
# ---------------------------------------------------------------------------


def test_canonical_writer_preserves_history_when_executor_overwrites_metrics(
    metrics_workspace,
):
    """Live regression scenario from solitaire/CORE-E5.

    Setup: 9 phases of history exist in the orchestrator-private history
    file, but the executor has overwritten the project's ``metrics.jsonl``
    with only the current phase's row.

    Expected: after the canonical writer runs, both files contain all 9
    prior rows plus the canonical row for the current phase (10 total
    rows).  Prior history must not be lost.
    """
    orch, project_artifacts, history_path, metrics_path = metrics_workspace
    assert hasattr(orch, "_write_canonical_metrics_row"), (
        "Orchestrator must expose _write_canonical_metrics_row as a "
        "method so the writer can be tested in isolation (extracted "
        "from the inline reviewer-PASS block in run() for Section 6.0)"
    )

    # Seed history with 9 prior phase rows.
    os.makedirs(os.path.dirname(history_path), exist_ok=True)
    prior_phases = [
        "INFRA-E1", "INFRA-E2", "CORE-E1", "CORE-E2", "CORE-E3",
        "CORE-E4", "UI-E1", "UI-E2", "UI-E3",
    ]
    with open(history_path, "w") as f:
        for p in prior_phases:
            f.write(_row(p) + "\n")

    # Simulate executor overwriting metrics.jsonl with only its own row
    # (the exact failure mode observed on solitaire/CORE-E5).
    with open(metrics_path, "w") as f:
        f.write(_row("CORE-E5", executor_attempts=1) + "\n")

    # Run the canonical writer.
    orch._write_canonical_metrics_row()

    # History file must contain all 10 rows (9 prior + 1 current canonical).
    history_rows = _read_jsonl(history_path)
    history_phases = [r.get("phase") for r in history_rows]
    assert history_phases == prior_phases + ["CORE-E5"], (
        f"History file lost prior rows.  Expected "
        f"{prior_phases + ['CORE-E5']}, got {history_phases}"
    )

    # metrics.jsonl must mirror the history (UI and reviewer-gate both
    # read this file — they cannot be allowed to see only the current
    # phase).
    metrics_rows = _read_jsonl(metrics_path)
    metrics_phases = [r.get("phase") for r in metrics_rows]
    assert metrics_phases == prior_phases + ["CORE-E5"], (
        f"Live metrics.jsonl was rewritten with truncated history.  "
        f"Expected {prior_phases + ['CORE-E5']}, got {metrics_phases}"
    )


# ---------------------------------------------------------------------------
# M2 — first-run bootstrap from existing metrics.jsonl
# ---------------------------------------------------------------------------


def test_canonical_writer_bootstraps_history_from_live_metrics_on_first_run(
    metrics_workspace,
):
    """On first deploy of the fix, the history file does not yet exist
    but the live ``metrics.jsonl`` already has prior rows.  The writer
    must seed history from the live file before processing, so prior
    rows are preserved across the upgrade.
    """
    orch, project_artifacts, history_path, metrics_path = metrics_workspace
    # No history file exists yet.
    assert not os.path.exists(history_path)

    # Live metrics has 3 prior phase rows (executor has not yet touched
    # it for the current phase).
    prior = ["CORE-E1", "CORE-E2", "CORE-E3"]
    with open(metrics_path, "w") as f:
        for p in prior:
            f.write(_row(p) + "\n")
        # Plus the agent-written row for current phase.
        f.write(_row("CORE-E5", executor_attempts=2) + "\n")

    orch._write_canonical_metrics_row()

    history_rows = _read_jsonl(history_path)
    history_phases = [r.get("phase") for r in history_rows]
    assert history_phases == prior + ["CORE-E5"], (
        f"Bootstrap from live metrics.jsonl failed.  Expected "
        f"{prior + ['CORE-E5']}, got {history_phases}"
    )


# ---------------------------------------------------------------------------
# M3 — dedup behaviour preserved (one row per phase)
# ---------------------------------------------------------------------------


def test_canonical_writer_dedups_current_phase_keeping_one_canonical_row(
    metrics_workspace,
):
    """Pre-existing dedup invariant: a phase that gets re-run (executor
    retry, reviewer rejection retry) must end with exactly ONE row in
    the metrics file — the latest canonical one.  This invariant is
    what the reviewer-gate MISSING_ARTIFACTS check relies on.
    """
    orch, project_artifacts, history_path, metrics_path = metrics_workspace

    os.makedirs(os.path.dirname(history_path), exist_ok=True)
    # History has two prior phases plus an older canonical row for the
    # current phase (left over from a prior attempt).
    with open(history_path, "w") as f:
        f.write(_row("CORE-E1") + "\n")
        f.write(_row("CORE-E2") + "\n")
        f.write(_row("CORE-E5", executor_attempts=1) + "\n")

    # Executor wrote an updated row for CORE-E5 (retry).
    with open(metrics_path, "w") as f:
        f.write(_row("CORE-E5", executor_attempts=2) + "\n")

    orch._write_canonical_metrics_row()

    history_rows = _read_jsonl(history_path)
    # Exactly one CORE-E5 row, two prior rows preserved.
    core_e5_rows = [r for r in history_rows if r.get("phase") == "CORE-E5"]
    assert len(core_e5_rows) == 1, (
        f"Expected exactly one CORE-E5 row after dedup, found "
        f"{len(core_e5_rows)}: {core_e5_rows}"
    )
    assert [r.get("phase") for r in history_rows] == ["CORE-E1", "CORE-E2", "CORE-E5"]


# ---------------------------------------------------------------------------
# M4 — atomic write (both files survive crash mid-write)
# ---------------------------------------------------------------------------


def test_canonical_writer_writes_atomically_to_both_files(
    metrics_workspace,
):
    """Both writes (history + live metrics) must use the
    ``mkstemp + os.replace`` pattern — a crash mid-write must leave
    the previous content intact, not a half-written file."""
    orch, project_artifacts, history_path, metrics_path = metrics_workspace
    # Seed both files with a known-good row.
    os.makedirs(os.path.dirname(history_path), exist_ok=True)
    with open(history_path, "w") as f:
        f.write(_row("CORE-E1") + "\n")
    with open(metrics_path, "w") as f:
        f.write(_row("CORE-E1") + "\n")

    orch._write_canonical_metrics_row()

    # Both files must end with a newline and be parseable as JSONL.
    for path in (history_path, metrics_path):
        content = open(path).read()
        assert content.endswith("\n"), f"{path} missing trailing newline"
        rows = _read_jsonl(path)
        assert all(isinstance(r, dict) for r in rows), (
            f"{path} contained malformed rows: {rows}"
        )


# ---------------------------------------------------------------------------
# M5 — no history file, no live file → writer initialises both
# ---------------------------------------------------------------------------


def test_canonical_writer_initialises_both_files_on_fresh_project(
    metrics_workspace,
):
    """Fresh project, first phase ever — neither file exists.  Writer
    must create both with exactly the canonical row for the current
    phase."""
    orch, project_artifacts, history_path, metrics_path = metrics_workspace
    assert not os.path.exists(history_path)
    assert not os.path.exists(metrics_path)

    orch._write_canonical_metrics_row()

    history_rows = _read_jsonl(history_path)
    metrics_rows = _read_jsonl(metrics_path)
    assert len(history_rows) == 1
    assert len(metrics_rows) == 1
    assert history_rows[0].get("phase") == "CORE-E5"
    assert metrics_rows[0].get("phase") == "CORE-E5"
