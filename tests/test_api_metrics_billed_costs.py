"""Dashboard side of OpenRouter billed-cost settlement (ui/server.py).

The orchestrator records, on each phase's metrics row, which OpenRouter
generations are still priced at list (``cost_pending``). The server settles
rows against a per-project cache of billed costs, reports what is still an
estimate (``cost_estimated``) so the dashboard can label it, and looks pending
generations up in the background at most once a minute per project.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import ui.server as server

client = TestClient(server.app)


def _ts(hours_ago=0):
    t = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(phase, pending, *, settled_cost=0.0, hours_ago=0):
    """A metrics row whose reviewer cost is ``pending`` (list price) plus a billed part."""
    est = sum(pending.values())
    reviewer = {
        "total_tokens": 1000,
        "cost_total": est + settled_cost,
        "cost_estimated": est,
    }
    if pending:
        reviewer["cost_pending"] = dict(pending)
    return {
        "ts": _ts(hours_ago),
        "phase": phase,
        "planner_tokens": {"total_tokens": 10, "cost_total": 0.0, "cost_estimated": 0.0},
        "executor_tokens": {"total_tokens": 10, "cost_total": 0.0, "cost_estimated": 0.0},
        "reviewer_tokens": reviewer,
        "cost_total": round(est + settled_cost, 6),
        "cost_estimated": est,
        "duration_seconds": 60,
    }


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    art = proj / ".autodev" / "pipeline"
    art.mkdir(parents=True)
    root = tmp_path / "root"
    root.mkdir()

    def write(rows, billed=None):
        (art / "metrics.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        if billed is not None:
            cache = root / "billed_costs" / "proj.json"
            cache.parent.mkdir(exist_ok=True)
            cache.write_text(json.dumps(billed))

    return str(proj), str(root), write


@pytest.fixture(autouse=True)
def _fresh_scheduler():
    server._billed_resolve_last.clear()
    server._billed_resolve_running.clear()
    yield
    server._billed_resolve_last.clear()
    server._billed_resolve_running.clear()


class TestSettleOnRead:
    def test_totals_settle_rows_and_report_the_estimated_part(self, project):
        proj, root, write = project
        write(
            [_row("CORE-E1", {"gen-1": 0.3, "gen-2": 0.2}, settled_cost=0.05)],
            billed={"gen-1": 0.03},
        )
        totals = server._project_metrics_totals(proj, root)
        assert totals["cost_total"] == pytest.approx(0.03 + 0.2 + 0.05)
        assert totals["cost_estimated"] == pytest.approx(0.2)
        assert totals["pending_generation_ids"] == ["gen-2"]
        (compact,) = server._compact_metrics_phases(totals)
        assert compact["cost_total"] == pytest.approx(0.28)
        assert compact["cost_estimated"] == pytest.approx(0.2)

    def test_without_a_pipeline_root_rows_stay_as_written(self, project):
        proj, root, write = project
        write([_row("CORE-E1", {"gen-1": 0.3})], billed={"gen-1": 0.03})
        totals = server._project_metrics_totals(proj)
        assert totals["cost_total"] == pytest.approx(0.3)
        assert totals["cost_estimated"] == pytest.approx(0.3)

    def test_rows_from_before_billed_costs_count_as_estimates(self, project):
        # Earlier releases recorded list-price costs with no marker at all.
        proj, root, write = project
        legacy = {"phase": "CORE-E1", "reviewer_tokens": {"total_tokens": 5, "cost_total": 0.4}, "cost_total": 0.4}
        write([legacy])
        totals = server._project_metrics_totals(proj, root)
        assert totals["cost_estimated"] == pytest.approx(0.4)
        assert server._phase_cost_estimated(legacy) == pytest.approx(0.4)

    def test_only_recent_rows_are_looked_up(self, project):
        proj, root, write = project
        write([_row("CORE-E1", {"gen-old": 0.1}, hours_ago=30), _row("CORE-E2", {"gen-new": 0.1})])
        totals = server._project_metrics_totals(proj, root)
        assert totals["pending_generation_ids"] == ["gen-new"]
        assert totals["cost_estimated"] == pytest.approx(0.2)

    def test_metrics_summary_carries_the_estimate(self, project):
        proj, root, write = project
        write([_row("CORE-E1", {"gen-1": 0.3})], billed={})
        with patch.object(server, "_schedule_billed_cost_resolution", return_value=False) as sched:
            summary = server._build_project_metrics_summary(proj, {"autodev_pipeline_root": root})
        assert summary["cost_estimated"] == pytest.approx(0.3)
        assert summary["phases"][0]["cost_estimated"] == pytest.approx(0.3)
        sched.assert_called_once()
        path, ids = sched.call_args[0]
        assert path == os.path.join(root, "billed_costs", "proj.json")
        assert ids == ["gen-1"]


class TestResolution:
    def test_resolved_costs_merge_into_the_cache(self, project, monkeypatch):
        proj, root, write = project
        write([], billed={"gen-0": 0.5})
        path = server._billed_costs_path(proj, root)
        monkeypatch.setattr(
            server._billed_costs, "lookup_generation_cost",
            lambda gid, key, **kw: {"gen-1": 0.01}.get(gid),
        )
        assert server._resolve_billed_costs(path, ["gen-1", "gen-2"], "sk-test") == 1
        assert json.loads(open(path).read()) == {"gen-0": 0.5, "gen-1": 0.01}

    def test_scheduling_needs_a_key(self, project, monkeypatch):
        proj, root, write = project
        monkeypatch.setattr(server, "_openrouter_api_key", lambda: "")
        ran = []
        path = server._billed_costs_path(proj, root)
        assert server._schedule_billed_cost_resolution(path, ["gen-1"], start=ran.append) is False
        assert ran == []

    def test_one_pass_per_project_per_minute(self, project, monkeypatch):
        proj, root, write = project
        monkeypatch.setattr(server, "_openrouter_api_key", lambda: "sk-test")
        calls = []
        monkeypatch.setattr(server, "_resolve_billed_costs", lambda p, ids, key: calls.append((p, ids, key)))
        path = server._billed_costs_path(proj, root)
        now = [1000.0]
        monkeypatch.setattr(server.time, "monotonic", lambda: now[0])

        def run(fn):
            fn()

        assert server._schedule_billed_cost_resolution(path, ["gen-1"], start=run) is True
        assert calls == [(path, ["gen-1"], "sk-test")]
        now[0] += 30
        assert server._schedule_billed_cost_resolution(path, ["gen-1"], start=run) is False
        now[0] += 31
        assert server._schedule_billed_cost_resolution(path, ["gen-1"], start=run) is True
        assert len(calls) == 2
        assert server._schedule_billed_cost_resolution(path, [], start=run) is False
        assert server._schedule_billed_cost_resolution(None, ["gen-1"], start=run) is False

    def test_api_key_from_the_environment_or_the_provider_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
        assert server._openrouter_api_key() == "sk-env"
        monkeypatch.delenv("OPENROUTER_API_KEY")
        key_file = tmp_path / "provider.env"
        key_file.write_text("LOCAL_MODEL_URL=http://x\nOPENROUTER_API_KEY=sk-file\n")
        monkeypatch.setattr(server, "load_config", lambda: {"provider_key_path": str(key_file)})
        assert server._openrouter_api_key() == "sk-file"
        monkeypatch.setattr(server, "load_config", lambda: {"provider_key_path": ""})
        assert server._openrouter_api_key() == ""


def test_live_phase_cost_reports_its_estimate(tmp_path):
    project_root = tmp_path / "pipeline_project"
    project_root.mkdir()
    phase_state = tmp_path / "phase_state.json"
    phase_state.write_text(json.dumps({
        "reviewer_tokens_acc": {"total_tokens": 100, "cost_total": 0.2, "cost_estimated": 0.15},
        "executor_tokens_acc": {"total_tokens": 100, "cost_total": 0.1},
    }))
    cfg = {
        "pipeline_state_path": str(tmp_path / "pipeline_state.json"),
        "phase_state_path": str(phase_state),
        "lock_path": str(tmp_path / "pipeline.lock"),
        "events_path": str(tmp_path / "pipeline_events.jsonl"),
        "project_dir_path": str(project_root),
    }
    with patch("ui.server.load_config", return_value=cfg):
        data = client.get("/api/state").json()
    # The executor accumulator predates the field: its cost is an estimate too.
    assert data["current_phase_tokens"]["cost_estimated"] == pytest.approx(0.25)
