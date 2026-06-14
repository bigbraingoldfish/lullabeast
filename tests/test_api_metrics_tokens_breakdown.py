"""Token-class breakdown — /api/metrics-summary surfaces input vs output vs cache.

Every metrics.jsonl row's per-role token dicts (``planner_tokens`` /
``executor_tokens`` / ``reviewer_tokens``, written by the orchestrator's
``_sum_session_tokens``) carry the full class split — ``input`` / ``output`` /
``cache_read`` / ``cache_write`` — alongside ``total_tokens``, but the endpoint
previously aggregated only the headline total. Not all tokens cost the same:
cache reads dominate the total (95% of a typical phase) yet bill at a fraction
of fresh input, and output is the most expensive class — so the dashboard needs
the split, not just the sum. These pin the additive aggregation: a run-level
``tokens_breakdown`` dict and a per-phase ``tokens_breakdown`` dict, each with
the four class keys summed across roles. Additive only — the existing token and
cost keys must be unchanged.

Fixture pattern mirrors ``test_api_phase4_metrics_tokens.py`` (3-B).
"""
import json
import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ui.server import app

client = TestClient(app)

BREAKDOWN_KEYS = ("input", "output", "cache_read", "cache_write")


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


def _role(input_=0, output=0, cache_read=0, cache_write=0, with_breakdown=True):
    """A per-role token dict as the orchestrator writes it. When
    ``with_breakdown`` is False, only total_tokens/cost_total are present —
    mirrors pre-breakdown history rows so the default-0 guard is exercised."""
    total = input_ + output + cache_read + cache_write
    d = {"total_tokens": total, "cost_total": 0.0}
    if with_breakdown:
        d.update({"input": input_, "output": output,
                  "cache_read": cache_read, "cache_write": cache_write})
    return d


def _row(phase, planner, executor, reviewer, duration=60):
    return {
        "ts": "2026-06-12T00:00:00Z",
        "phase": phase,
        "goal": f"phase {phase}",
        "executor_attempts": 1,
        "reviewer_passes": 1,
        "escalations": 0,
        "skill_used": "core-logic",
        "planner_tokens": planner,
        "executor_tokens": executor,
        "reviewer_tokens": reviewer,
        "cost_total": 0.0,
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
    assert resp.status_code == 200
    return resp.json()


def test_run_level_breakdown_sums_roles_and_phases(temp_dir):
    rows = [
        _row("CORE-E1",
             _role(100, 10, 1000, 5),
             _role(200, 20, 2000, 0),
             _role(300, 30, 3000, 0)),
        _row("CORE-E2",
             _role(1000, 100, 10000, 0),
             _role(2000, 200, 20000, 0),
             _role(3000, 300, 30000, 7)),
    ]
    data = _summary(temp_dir, rows)
    assert data["tokens_breakdown"] == {
        "input": 6600, "output": 660, "cache_read": 66000, "cache_write": 12,
    }
    # Headline total still equals the per-role total_tokens sum.
    assert data["total_tokens"] == 6600 + 660 + 66000 + 12


def test_per_phase_breakdown_present_and_correct(temp_dir):
    rows = [
        _row("CORE-E2",
             _role(23684, 2835, 290396, 0),
             _role(52233, 12873, 1624903, 0),
             _role(56665, 17131, 1112285, 0)),
    ]
    data = _summary(temp_dir, rows)
    (phase,) = data["phases"]
    assert phase["tokens_breakdown"] == {
        "input": 132582, "output": 32839, "cache_read": 3027584, "cache_write": 0,
    }
    assert phase["tokens_total"] == 3193005


def test_pre_breakdown_history_rows_read_as_zero(temp_dir):
    """Role dicts carrying only total_tokens (pre-breakdown history) must not
    crash and must yield an all-zero breakdown — the frontend hides the line."""
    rows = [
        _row("OLD-1",
             {"total_tokens": 500, "cost_total": 0.0},
             {"total_tokens": 600, "cost_total": 0.0},
             {"total_tokens": 700, "cost_total": 0.0}),
    ]
    data = _summary(temp_dir, rows)
    zero = {k: 0 for k in BREAKDOWN_KEYS}
    assert data["tokens_breakdown"] == zero
    assert data["phases"][0]["tokens_breakdown"] == zero
    # Headline total is untouched by the missing breakdown fields.
    assert data["total_tokens"] == 1800


def test_non_dict_and_malformed_role_objects_read_as_zero(temp_dir):
    rows = [
        _row("BAD-1",
             None,                                       # role key null
             {"input": "garbage", "output": None,        # non-numeric fields
              "cache_read": 5, "total_tokens": 5, "cost_total": 0.0},
             "not-a-dict"),
    ]
    data = _summary(temp_dir, rows)
    assert data["tokens_breakdown"] == {
        "input": 0, "output": 0, "cache_read": 5, "cache_write": 0,
    }


def test_existing_token_and_cost_keys_unchanged(temp_dir):
    """Additive guard — the 3-B token keys and cost keys keep their values."""
    rows = [
        _row("CORE-E1",
             _role(100, 10, 1000, 0),
             _role(200, 20, 2000, 0),
             _role(300, 30, 3000, 0)),
    ]
    data = _summary(temp_dir, rows)
    assert data["planner_tokens_total"] == 1110
    assert data["executor_tokens_total"] == 2220
    assert data["reviewer_tokens_total"] == 3330
    assert data["total_tokens"] == 6660
    assert data["total_cost"] == 0.0
    assert data["phases"][0]["tokens_total"] == 6660
