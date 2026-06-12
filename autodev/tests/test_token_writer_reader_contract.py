"""Writer/reader key-contract guard — the ``_sum_session_tokens`` bug class.

The historic token bug was a silent key mismatch between what the orchestrator
writes and what a reader expects (zeroed cost, no error). The observability
roadmap's standing rule: token/cost key tests must read a fixture written by
the orchestrator's ACTUAL writer functions, not a hand-built dict.

This test drives the real end-to-end chain:

  OpenClaw-shaped session JSONL
    → ``_accumulate_role_tokens`` (live accumulator, real writer)
    → ``_write_canonical_metrics_row`` (durable row, real writer)
    → ``ui.server`` row-math helpers (``_role_token_total`` /
      ``_phase_token_total`` / ``_role_cost`` / ``_phase_token_breakdown``)
      — the exact readers behind ``/api/metrics-summary`` and the queue
      enrichment.

A renamed key on either side fails here even when both sides' own suites
(testing against their own fixtures) stay green.
"""
import importlib
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _openclaw_row(inp, out, cache_read, total, cost):
    return json.dumps({
        "id": "msg",
        "type": "message",
        "timestamp": "2026-06-12T00:00:00Z",
        "message": {
            "role": "assistant",
            "usage": {
                "input": inp, "output": out,
                "cacheRead": cache_read, "cacheWrite": 0,
                "totalTokens": total,
                "cost": {"total": cost},
            },
        },
    })


@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))

    import orchestrator as orch_mod
    importlib.reload(orch_mod)

    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.state = {
        "pipeline_status": "RUNNING",
        "current_agent": "reviewer",
        "current_phase": 2,
        "current_phase_raw_id": "CORE-E1",
        "reviewer_retries": 0,
        "phase_start_time": None,
        "last_action": "test",
    }
    inst.lock_fd = None
    inst.openclaw_config = {"hooks_url": "http://x", "hooks_token": "t", "pipeline": {}}
    inst.skill_manager = MagicMock()

    proj = tmp_path / "pipeline-project"
    artifacts = proj / ".autodev" / "pipeline"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "current_phase.json").write_text(json.dumps({
        "raw_id": "CORE-E1", "detail": "Phase CORE-E1",
    }))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(proj))
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(artifacts / "phase_state.json"))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path))
    monkeypatch.setattr(orch_mod, "_write_pipeline_event", MagicMock())

    return inst, proj, artifacts


def test_orchestrator_written_row_feeds_server_token_and_cost_math(orch, tmp_path):
    inst, proj, artifacts = orch

    # Real writer #1 — accumulate three roles from real-shaped session files.
    fixtures = {
        "planner": (100, 10, 1000, 1110, 0.01),
        "executor": (200, 20, 2000, 2220, 0.02),
        "reviewer": (300, 30, 3000, 3330, 0.03),
    }
    for role, (inp, out, cache, total, cost) in fixtures.items():
        jsonl = tmp_path / f"{role}.jsonl"
        jsonl.write_text(_openclaw_row(inp, out, cache, total, cost) + "\n")
        inst._accumulate_role_tokens(role, str(jsonl))

    # Real writer #2 — the canonical metrics row.
    inst._write_canonical_metrics_row()
    metrics_path = artifacts / "metrics.jsonl"
    assert metrics_path.exists()

    # Real readers — the server's row-math helpers and shared aggregator.
    from ui.server import (
        _project_metrics_totals,
        _role_token_total,
        _phase_token_total,
        _role_cost,
        _phase_token_breakdown,
    )

    totals = _project_metrics_totals(str(proj))
    assert totals is not None, "server aggregator could not read the orchestrator's row"
    (row,) = totals["phases"]

    assert _role_token_total(row, "planner_tokens") == 1110
    assert _role_token_total(row, "executor_tokens") == 2220
    assert _role_token_total(row, "reviewer_tokens") == 3330
    assert _phase_token_total(row) == 6660
    assert totals["tokens_total"] == 6660  # METRICS-E3 aggregate (queue surfaces)
    assert _role_cost(row, "planner_tokens") == pytest.approx(0.01)
    assert totals["cost_total"] == pytest.approx(0.06)
    assert _phase_token_breakdown(row) == {
        "input": 600, "output": 60, "cache_read": 6000, "cache_write": 0,
    }
