"""UI REVIEW Phase 4 (finding 3-B) — /api/metrics-summary surfaces token counts.

The pipeline already records per-role token totals on every metrics.jsonl row
(``planner_tokens`` / ``executor_tokens`` / ``reviewer_tokens`` each carry a
``total_tokens`` integer alongside ``cost_total``), but ``/api/metrics-summary``
returned ONLY cost — so no token figure could render anywhere on the Pipeline
Monitor. These pin the additive token aggregation, mirroring the existing cost
aggregation: a run-total ``total_tokens``, per-role run totals, and a per-phase
``tokens_total``. Additive only — the cost keys/values must be unchanged
(``test_cost_keys_unchanged`` guards that).

Fixture pattern mirrors ``test_api_metrics_summary_cost_hold.py``.
"""
import json
import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ui.server import app

client = TestClient(app)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def _write_metrics_jsonl(project_dir, rows):
    art = os.path.join(project_dir, ".autodev", "pipeline")
    os.makedirs(art, exist_ok=True)
    path = os.path.join(art, "metrics.jsonl")
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return path


def _row(phase, *, planner_tok=0, executor_tok=0, reviewer_tok=0,
         planner_cost=0.0, executor_cost=0.0, reviewer_cost=0.0,
         duration=60, executor=1, with_token_field=True):
    """A metrics.jsonl row. When ``with_token_field`` is False the role dicts carry
    ONLY ``cost_total`` (no ``total_tokens``) — mirrors pre-token history rows so the
    default-0 guard is exercised."""
    def role(tok, cost):
        d = {"cost_total": cost}
        if with_token_field:
            d["total_tokens"] = tok
        return d
    return {
        "ts": "2026-06-01T00:00:00Z",
        "phase": phase,
        "goal": f"phase {phase}",
        "executor_attempts": executor,
        "reviewer_passes": 1,
        "escalations": 0,
        "skill_used": "core-logic",
        "planner_tokens": role(planner_tok, planner_cost),
        "executor_tokens": role(executor_tok, executor_cost),
        "reviewer_tokens": role(reviewer_tok, reviewer_cost),
        "cost_total": round(planner_cost + executor_cost + reviewer_cost, 6),
        "duration_seconds": duration,
    }


def _summary(temp_dir, rows):
    project_dir = os.path.join(temp_dir, "proj")
    os.makedirs(project_dir, exist_ok=True)
    _write_metrics_jsonl(project_dir, rows)
    fake_config = {
        "project_dir_path": project_dir,
        "events_path": os.path.join(temp_dir, "no-events.jsonl"),
    }
    with patch("ui.server.load_config", return_value=fake_config):
        resp = client.get("/api/metrics-summary")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_run_total_tokens_summed(temp_dir):
    """Run-total ``total_tokens`` is the sum of every role's ``total_tokens`` across
    all phases. Catches a missing or wrongly-scoped run aggregation."""
    body = _summary(temp_dir, [
        _row("CORE-E1", planner_tok=100, executor_tok=500, reviewer_tok=200),
        _row("UI-E1", planner_tok=50, executor_tok=300, reviewer_tok=150),
    ])
    # (100+500+200) + (50+300+150) = 800 + 500 = 1300
    assert body["total_tokens"] == 1300


def test_run_total_role_tokens(temp_dir):
    """Per-role run totals parallel the per-role cost totals. Catches a wrong
    role→field mapping (e.g. planner reading the executor dict)."""
    body = _summary(temp_dir, [
        _row("CORE-E1", planner_tok=100, executor_tok=500, reviewer_tok=200),
        _row("UI-E1", planner_tok=50, executor_tok=300, reviewer_tok=150),
    ])
    assert body["planner_tokens_total"] == 150
    assert body["executor_tokens_total"] == 800
    assert body["reviewer_tokens_total"] == 350


def test_per_phase_tokens_total(temp_dir):
    """Each per-phase entry carries ``tokens_total`` (the 3-role sum for that phase)
    for the completion table's Tokens column. Catches a per-phase omission."""
    body = _summary(temp_dir, [
        _row("CORE-E1", planner_tok=100, executor_tok=500, reviewer_tok=200),
        _row("UI-E1", planner_tok=50, executor_tok=300, reviewer_tok=150),
    ])
    phase_map = {p["phase"]: p for p in body["phases"]}
    assert phase_map["CORE-E1"]["tokens_total"] == 800
    assert phase_map["UI-E1"]["tokens_total"] == 500


def test_tokens_default_zero_when_missing(temp_dir):
    """Pre-token history rows (role dicts carry only ``cost_total``) must yield 0
    token totals — never KeyError/None. The endpoint defaults a missing
    ``total_tokens`` to 0, the same defence the cost helper uses."""
    body = _summary(temp_dir, [
        _row("LEGACY-E1", planner_cost=0.01, executor_cost=0.02, reviewer_cost=0.01,
             with_token_field=False),
    ])
    assert body["total_tokens"] == 0
    assert body["planner_tokens_total"] == 0
    assert body["executor_tokens_total"] == 0
    assert body["reviewer_tokens_total"] == 0
    assert body["phases"][0]["tokens_total"] == 0


def test_cost_keys_unchanged(temp_dir):
    """Regression guard: the additive token change must not disturb the existing
    cost aggregation (run totals, per-role totals, per-phase role cost)."""
    body = _summary(temp_dir, [
        _row("CORE-E1", planner_cost=0.01, executor_cost=0.02, reviewer_cost=0.01,
             planner_tok=100, executor_tok=500, reviewer_tok=200),
    ])
    assert body["total_cost"] == pytest.approx(0.04, abs=1e-6)
    assert body["planner_cost_total"] == pytest.approx(0.01, abs=1e-6)
    assert body["executor_cost_total"] == pytest.approx(0.02, abs=1e-6)
    assert body["reviewer_cost_total"] == pytest.approx(0.01, abs=1e-6)
    assert body["phases"][0]["executor_cost"] == pytest.approx(0.02, abs=1e-6)
