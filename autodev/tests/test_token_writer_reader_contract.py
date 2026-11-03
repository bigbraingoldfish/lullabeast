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


def _openrouter_row(gen_id, inp, out, cost_parts, total=None):
    """An OpenRouter assistant row as OpenClaw 2026.6.11 writes it: list-price
    cost parts and their sum as the total, unless its billed-cost lookup
    replaced the total."""
    parts = dict(zip(("input", "output", "cacheRead", "cacheWrite"), cost_parts))
    if total is None:
        total = parts["input"] + parts["output"] + parts["cacheRead"] + parts["cacheWrite"]
    return json.dumps({
        "id": "msg",
        "type": "message",
        "timestamp": "2026-11-03T00:00:00Z",
        "message": {
            "role": "assistant",
            "provider": "openrouter",
            "model": "deepseek/deepseek-v4.1-flash-1017",
            "api": "openai-completions",
            "responseId": gen_id,
            "usage": {
                "input": inp, "output": out, "cacheRead": 0, "cacheWrite": 0,
                "totalTokens": inp + out,
                "cost": dict(parts, total=total),
            },
        },
    })


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


def test_list_priced_openrouter_cost_settles_to_the_billed_cost(orch, tmp_path):
    inst, proj, artifacts = orch

    # Two reviewer responses priced at list (OpenClaw's lookup ran before
    # OpenRouter published them) and one whose total OpenClaw already replaced
    # with the billed cost.
    jsonl = tmp_path / "reviewer.jsonl"
    jsonl.write_text("\n".join([
        _openrouter_row("gen-1-a", 1000, 100, (0.0003, 0.00012, 0.0, 0.0)),
        _openrouter_row("gen-1-b", 2000, 100, (0.0006, 0.00012, 0.0, 0.0)),
        _openrouter_row("gen-1-c", 1000, 100, (0.0003, 0.00012, 0.0, 0.0), total=0.00005),
    ]) + "\n")
    inst._accumulate_role_tokens("reviewer", str(jsonl))

    # The live accumulator counts the estimate but stays lean: no per-call map
    # in phase_state, which the dashboard polls every few seconds.
    ps = json.loads((artifacts / "phase_state.json").read_text())
    acc = ps["reviewer_tokens_acc"]
    assert acc["cost_estimated"] == pytest.approx(0.00042 + 0.00072)
    assert "cost_pending" not in acc
    assert all("cost_pending" not in s for s in ps["reviewer_tokens_sessions"].values())

    # The durable row names each generation still at list price.
    inst._write_canonical_metrics_row()
    row = json.loads((artifacts / "metrics.jsonl").read_text().splitlines()[-1])
    assert row["reviewer_tokens"]["cost_pending"] == pytest.approx(
        {"gen-1-a": 0.00042, "gen-1-b": 0.00072}
    )
    assert row["cost_estimated"] == pytest.approx(0.00114)
    assert row["cost_total"] == pytest.approx(0.00119, abs=1e-6)

    # The dashboard's reader settles the row against the billed costs
    # OpenRouter has published so far (here one of the two).
    from ui.server import _project_metrics_totals

    cache = tmp_path / "billed_costs" / "pipeline-project.json"
    cache.parent.mkdir()
    cache.write_text(json.dumps({"gen-1-a": 0.00004}))
    totals = _project_metrics_totals(str(proj), str(tmp_path))
    (settled,) = totals["phases"]
    assert settled["cost_total"] == pytest.approx(0.00004 + 0.00072 + 0.00005, abs=1e-6)
    assert totals["cost_total"] == pytest.approx(0.00081, abs=1e-6)
    assert totals["cost_estimated"] == pytest.approx(0.00072)
    assert totals["pending_generation_ids"] == ["gen-1-b"]


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
