"""METRICS read-side fix — the dashboard must read the orchestrator's canonical
rich history (``<pipeline_root>/metrics_history/<project>.jsonl``) and overlay it
onto the project-local ``metrics.jsonl``.

Background: the executor/reviewer is instructed to append a metrics row at
sentinel time, but LLM file tools frequently *overwrite* the project's
``metrics.jsonl`` down to a minimal hand-written row (``ts/phase/goal/
executor_attempts/reviewer_passes/escalations`` with ``duration_seconds: null``
and NO cost/token fields). The orchestrator's canonical row — written to the
agent-unreachable ``metrics_history/`` — carries the real cost/tokens/duration.
The read side must prefer the canonical row so completed/escalated runs show
their true metrics instead of zeros (the live GridBeastDemo symptom).

Isolation: the helper takes ``pipeline_root`` explicitly (threaded from config),
so a test project named like a real one cannot pick up the operator's history.
"""
import json
import os

from ui.server import _project_metrics_totals


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _minimal_row(phase):
    # The agent-clobbered shape: no cost, no token dicts, null duration.
    return {
        "ts": "2026-06-17T04:20:00Z",
        "phase": phase,
        "goal": f"phase {phase}",
        "executor_attempts": 1,
        "reviewer_passes": 1,
        "escalations": 0,
        "duration_seconds": None,
    }


def _rich_row(phase, *, duration, cost, ptok, etok, rtok):
    return {
        "ts": "2026-06-17T04:22:16Z",
        "phase": phase,
        "goal": f"phase {phase}",
        "executor_attempts": 1,
        "reviewer_passes": 1,
        "escalations": 0,
        "duration_seconds": duration,
        "cost_total": cost,
        "planner_tokens": {"total_tokens": ptok, "cost_total": 0.0},
        "executor_tokens": {"total_tokens": etok, "cost_total": 0.0},
        "reviewer_tokens": {"total_tokens": rtok, "cost_total": 0.0},
        "models_used": {"planner": "m", "executor": "m", "reviewer": "m"},
    }


def test_canonical_history_overlays_minimal_project_row(tmp_path):
    proj = tmp_path / "MyProj"
    _write_jsonl(str(proj / ".autodev" / "pipeline" / "metrics.jsonl"),
                 [_minimal_row("INFRA-E1")])
    root = tmp_path / "pipe"
    _write_jsonl(str(root / "metrics_history" / "MyProj.jsonl"),
                 [_rich_row("INFRA-E1", duration=385, cost=0.000109,
                            ptok=1000, etok=2000, rtok=3000)])

    totals = _project_metrics_totals(str(proj), str(root))
    assert totals is not None
    assert len(totals["phases"]) == 1
    # Rich canonical values win over the clobbered minimal row.
    assert totals["cost_total"] == 0.000109
    assert totals["duration_seconds"] == 385
    assert totals["tokens_total"] == 6000


def test_no_pipeline_root_keeps_pure_project_behavior(tmp_path):
    # pipeline_root=None (default) → no overlay; the minimal row's zeros stand.
    proj = tmp_path / "MyProj"
    _write_jsonl(str(proj / ".autodev" / "pipeline" / "metrics.jsonl"),
                 [_minimal_row("INFRA-E1")])
    root = tmp_path / "pipe"
    _write_jsonl(str(root / "metrics_history" / "MyProj.jsonl"),
                 [_rich_row("INFRA-E1", duration=385, cost=0.000109,
                            ptok=1000, etok=2000, rtok=3000)])

    totals = _project_metrics_totals(str(proj), None)
    assert totals is not None
    assert totals["cost_total"] == 0.0
    assert totals["duration_seconds"] == 0
    assert totals["tokens_total"] == 0


def test_project_only_phase_preserved_when_history_lacks_it(tmp_path):
    # A phase the agent wrote but that escalated before a canonical row existed
    # must survive the overlay (history-only wins, project-only is kept).
    proj = tmp_path / "P2"
    _write_jsonl(str(proj / ".autodev" / "pipeline" / "metrics.jsonl"),
                 [_minimal_row("INFRA-E1"), _minimal_row("CORE-E1")])
    root = tmp_path / "pipe"
    _write_jsonl(str(root / "metrics_history" / "P2.jsonl"),
                 [_rich_row("INFRA-E1", duration=385, cost=0.0001,
                            ptok=10, etok=20, rtok=30)])

    totals = _project_metrics_totals(str(proj), str(root))
    by_phase = {p["phase"]: p for p in totals["phases"]}
    assert set(by_phase) == {"INFRA-E1", "CORE-E1"}
    assert by_phase["INFRA-E1"]["duration_seconds"] == 385      # rich from history
    assert by_phase["CORE-E1"].get("cost_total") is None        # minimal from project
    assert totals["last_phase"] == "CORE-E1"


def test_history_drives_order_when_project_clobbered(tmp_path):
    """Regression — the exact scenario the overlay targets: the agent clobbered the
    project metrics.jsonl to a single minimal row for the escalated phase (CORE-E3),
    while history holds the completed earlier phases. Phase ORDER and last_phase must
    come from history (the chronological, append-only source of truth), with the
    project-only escalated phase appended LAST — not the clobbered project file's
    order. The buggy merge seeded order from the project file → order
    [CORE-E3, INFRA-E1, CORE-E1, CORE-E2] and last_phase 'CORE-E2' (wrong)."""
    proj = tmp_path / "Clob"
    _write_jsonl(str(proj / ".autodev" / "pipeline" / "metrics.jsonl"),
                 [_minimal_row("CORE-E3")])
    root = tmp_path / "pipe"
    _write_jsonl(str(root / "metrics_history" / "Clob.jsonl"), [
        _rich_row("INFRA-E1", duration=10, cost=0.0001, ptok=1, etok=1, rtok=1),
        _rich_row("CORE-E1", duration=20, cost=0.0001, ptok=1, etok=1, rtok=1),
        _rich_row("CORE-E2", duration=30, cost=0.0001, ptok=1, etok=1, rtok=1),
    ])
    totals = _project_metrics_totals(str(proj), str(root))
    assert [p["phase"] for p in totals["phases"]] == [
        "INFRA-E1", "CORE-E1", "CORE-E2", "CORE-E3"
    ], "phase order must follow history (chronological), project-only phase last"
    assert totals["last_phase"] == "CORE-E3", (
        "last_phase must be the escalated/in-flight CORE-E3, not a history phase"
    )


def test_missing_history_file_falls_back_to_project(tmp_path):
    proj = tmp_path / "NoHist"
    _write_jsonl(str(proj / ".autodev" / "pipeline" / "metrics.jsonl"),
                 [_minimal_row("INFRA-E1")])
    root = tmp_path / "pipe"  # no metrics_history/NoHist.jsonl written
    totals = _project_metrics_totals(str(proj), str(root))
    assert totals is not None
    assert len(totals["phases"]) == 1
    assert totals["cost_total"] == 0.0
