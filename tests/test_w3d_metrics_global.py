"""W3-D: GET /api/metrics-global — cross-project analytics from runs_index.jsonl.

Tests verify:
- Empty/missing index returns zero-filled structure
- Single project, single run: per-project and cross-project stats correct
- skill_injection_rate calculated from phases array
- Multiple runs on same project: aggregated correctly
- Multiple projects: separate entries + cross-project aggregated
- skill_vs_no_skill comparison uses phase-level executor_attempts
- Malformed JSON lines skipped silently
- Missing run_summary.json skipped silently
"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from ui.server import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(offset_sec=0):
    from datetime import timedelta
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_sec)).isoformat()


def _write_runs_index(pipeline_root: Path, entries: list):
    index = pipeline_root / "runs_index.jsonl"
    with open(index, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _write_run_summary(project_path: Path, summary: dict):
    art = project_path / ".autodev" / "pipeline"
    art.mkdir(parents=True, exist_ok=True)
    with open(art / "run_summary.json", "w") as f:
        json.dump(summary, f)


def _make_run_summary(
    project_path,
    project_name="test-project",
    outcome="PIPELINE_COMPLETE",
    phases_attempted=5,
    phases_complete=5,
    executor_attempts_total=7,
    escalations_total=0,
    blame_fires_total=0,
    skills_injected=None,
    blame_attributions=None,
    phases=None,
    run_start=None,
    run_end=None,
    cost_total=0.0,
):
    return {
        "schema_version": 1,
        "generated_at": _ts(),
        "outcome": outcome,
        "outcome_detail": "",
        "project_path": str(project_path),
        "project_name": project_name,
        "idea_id": None,
        "run_start": run_start or _ts(-3600),
        "run_end": run_end or _ts(),
        "total_duration_seconds": 3600,
        "phases_attempted": phases_attempted,
        "phases_complete": phases_complete,
        "executor_attempts_total": executor_attempts_total,
        "escalations_total": escalations_total,
        "blame_fires_total": blame_fires_total,
        "skills_injected": skills_injected or [],
        "blame_attributions": blame_attributions or [],
        "token_usage": {
            "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
            "total_tokens": 0, "cost_total": cost_total,
        },
        "phases": phases or [
            {"phase": f"CORE-E{i+1}", "executor_attempts": 1, "blame": None,
             "skill_used": None, "last_error_code": None, "escalation_trigger_reason": None}
            for i in range(phases_attempted)
        ],
    }


def _make_index_entry(project_path, project_name="test-project", outcome="PIPELINE_COMPLETE",
                      run_start=None, run_end=None):
    return {
        "ts": _ts(),
        "outcome": outcome,
        "project_path": str(project_path),
        "project_name": project_name,
        "run_start": run_start or _ts(-3600),
        "run_end": run_end or _ts(),
    }


def _cfg(tmp_path):
    return {
        "pipeline_state_path": str(tmp_path / "pipeline_state.json"),
        "phase_state_path": str(tmp_path / "phase_state.json"),
        "lock_path": str(tmp_path / "pipeline.lock"),
        "events_path": str(tmp_path / "pipeline_events.jsonl"),
        "project_dir_path": str(tmp_path / "proj"),
        "pipeline_queue_path": str(tmp_path / "pipeline_queue.json"),
        "autodev_pipeline_root": str(tmp_path),
    }


# ---------------------------------------------------------------------------
# Empty and missing cases
# ---------------------------------------------------------------------------

class TestEmptyAndMissing:
    def test_returns_empty_when_no_runs_index(self, tmp_path):
        cfg = _cfg(tmp_path)
        # runs_index.jsonl not created

        with patch("ui.server.load_config", return_value=cfg):
            resp = client.get("/api/metrics-global")

        assert resp.status_code == 200
        data = resp.json()
        assert data["projects"] == []
        assert data["cross_project"]["total_runs"] == 0
        assert data["cross_project"]["avg_executor_attempts"] == 0.0
        assert data["cross_project"]["escalation_rate"] == 0.0
        assert data["cross_project"]["skill_injection_rate"] == 0.0
        assert "skill_vs_no_skill_executor_attempts" in data["cross_project"]

    def test_returns_empty_when_runs_index_is_empty(self, tmp_path):
        cfg = _cfg(tmp_path)
        (tmp_path / "runs_index.jsonl").write_text("")

        with patch("ui.server.load_config", return_value=cfg):
            resp = client.get("/api/metrics-global")

        assert resp.status_code == 200
        assert resp.json()["projects"] == []

    def test_skips_when_run_summary_missing(self, tmp_path):
        cfg = _cfg(tmp_path)
        proj = tmp_path / "proj"
        proj.mkdir()
        # Index entry but no run_summary.json
        _write_runs_index(tmp_path, [_make_index_entry(proj)])

        with patch("ui.server.load_config", return_value=cfg):
            resp = client.get("/api/metrics-global")

        assert resp.status_code == 200
        assert resp.json()["projects"] == []

    def test_returns_empty_when_no_pipeline_root_configured(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg["autodev_pipeline_root"] = ""

        with patch("ui.server.load_config", return_value=cfg):
            resp = client.get("/api/metrics-global")

        assert resp.status_code == 200
        assert resp.json()["projects"] == []


# ---------------------------------------------------------------------------
# Single project, single run
# ---------------------------------------------------------------------------

class TestSingleProjectSingleRun:
    def test_returns_project_stats(self, tmp_path):
        cfg = _cfg(tmp_path)
        proj = tmp_path / "alpha"
        proj.mkdir()

        summary = _make_run_summary(
            proj, project_name="alpha",
            executor_attempts_total=7,
            escalations_total=1,
            phases_attempted=5,
        )
        _write_run_summary(proj, summary)
        _write_runs_index(tmp_path, [_make_index_entry(proj, project_name="alpha")])

        with patch("ui.server.load_config", return_value=cfg):
            resp = client.get("/api/metrics-global")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["projects"]) == 1

        p = data["projects"][0]
        assert p["project_name"] == "alpha"
        assert p["runs"] == 1
        assert p["last_outcome"] == "PIPELINE_COMPLETE"
        assert p["avg_executor_attempts"] == 7.0
        assert p["escalation_rate"] == 1.0

    def test_cross_project_totals(self, tmp_path):
        cfg = _cfg(tmp_path)
        proj = tmp_path / "proj"
        proj.mkdir()

        summary = _make_run_summary(proj, executor_attempts_total=6)
        _write_run_summary(proj, summary)
        _write_runs_index(tmp_path, [_make_index_entry(proj)])

        with patch("ui.server.load_config", return_value=cfg):
            resp = client.get("/api/metrics-global")

        cross = resp.json()["cross_project"]
        assert cross["total_runs"] == 1
        assert cross["avg_executor_attempts"] == 6.0

    def test_skill_injection_rate_from_phases(self, tmp_path):
        cfg = _cfg(tmp_path)
        proj = tmp_path / "proj"
        proj.mkdir()

        phases = [
            {"phase": "CORE-E1", "executor_attempts": 2, "blame": None,
             "skill_used": "core-logic", "last_error_code": None, "escalation_trigger_reason": None},
            {"phase": "CORE-E2", "executor_attempts": 1, "blame": None,
             "skill_used": None, "last_error_code": None, "escalation_trigger_reason": None},
            {"phase": "CORE-E3", "executor_attempts": 1, "blame": None,
             "skill_used": None, "last_error_code": None, "escalation_trigger_reason": None},
            {"phase": "CORE-E4", "executor_attempts": 3, "blame": None,
             "skill_used": "core-logic", "last_error_code": None, "escalation_trigger_reason": None},
        ]
        summary = _make_run_summary(
            proj, phases_attempted=4,
            skills_injected=[{"phase": "CORE-E1", "discipline": "core-logic"},
                              {"phase": "CORE-E4", "discipline": "core-logic"}],
            phases=phases,
        )
        _write_run_summary(proj, summary)
        _write_runs_index(tmp_path, [_make_index_entry(proj)])

        with patch("ui.server.load_config", return_value=cfg):
            resp = client.get("/api/metrics-global")

        p = resp.json()["projects"][0]
        # 2 out of 4 phases had skill → rate = 0.5
        assert p["skill_injection_rate"] == 0.5


# ---------------------------------------------------------------------------
# Multiple runs on same project
# ---------------------------------------------------------------------------

class TestMultipleRunsSameProject:
    def test_runs_counted_correctly(self, tmp_path):
        cfg = _cfg(tmp_path)
        proj = tmp_path / "proj"
        proj.mkdir()

        summaries = [
            _make_run_summary(proj, executor_attempts_total=4, run_end=_ts(-7200)),
            _make_run_summary(proj, executor_attempts_total=6, run_end=_ts(-3600)),
            _make_run_summary(proj, executor_attempts_total=8, run_end=_ts()),
        ]
        # Write only the latest run_summary.json (orchestrator overwrites)
        _write_run_summary(proj, summaries[-1])

        index_entries = [
            _make_index_entry(proj, run_end=_ts(-7200)),
            _make_index_entry(proj, run_end=_ts(-3600)),
            _make_index_entry(proj, run_end=_ts()),
        ]
        _write_runs_index(tmp_path, index_entries)

        with patch("ui.server.load_config", return_value=cfg):
            resp = client.get("/api/metrics-global")

        assert resp.status_code == 200
        data = resp.json()
        # Three index entries for same project_path → one project with runs=3
        assert len(data["projects"]) == 1
        assert data["projects"][0]["runs"] == 3
        assert data["cross_project"]["total_runs"] == 3

    def test_avg_executor_attempts_across_runs(self, tmp_path):
        cfg = _cfg(tmp_path)
        proj = tmp_path / "proj"
        proj.mkdir()

        # 3 runs × same run_summary (we only have one on disk): avg = 8 total attempts / 3 runs
        # Because we read same run_summary 3 times, each has executor_attempts_total=8
        # → total = 8+8+8 = 24 / 3 = 8.0
        _write_run_summary(proj, _make_run_summary(proj, executor_attempts_total=8))
        _write_runs_index(tmp_path, [_make_index_entry(proj)] * 3)

        with patch("ui.server.load_config", return_value=cfg):
            resp = client.get("/api/metrics-global")

        assert resp.json()["projects"][0]["avg_executor_attempts"] == 8.0

    def test_last_outcome_is_latest(self, tmp_path):
        cfg = _cfg(tmp_path)
        proj = tmp_path / "proj"
        proj.mkdir()

        # Latest run_summary on disk is BLOCKED
        _write_run_summary(proj, _make_run_summary(proj, outcome="BLOCKED"))
        _write_runs_index(tmp_path, [
            _make_index_entry(proj, outcome="PIPELINE_COMPLETE", run_end=_ts(-7200)),
            _make_index_entry(proj, outcome="BLOCKED", run_end=_ts()),
        ])

        with patch("ui.server.load_config", return_value=cfg):
            resp = client.get("/api/metrics-global")

        p = resp.json()["projects"][0]
        # last_outcome comes from run_summary loaded for the latest index entry
        assert p["last_outcome"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Multiple projects
# ---------------------------------------------------------------------------

class TestMultipleProjects:
    def test_multiple_projects_separate_entries(self, tmp_path):
        cfg = _cfg(tmp_path)
        proj_a = tmp_path / "alpha"
        proj_a.mkdir()
        proj_b = tmp_path / "beta"
        proj_b.mkdir()

        _write_run_summary(proj_a, _make_run_summary(proj_a, project_name="alpha"))
        _write_run_summary(proj_b, _make_run_summary(proj_b, project_name="beta"))
        _write_runs_index(tmp_path, [
            _make_index_entry(proj_a, project_name="alpha"),
            _make_index_entry(proj_b, project_name="beta"),
        ])

        with patch("ui.server.load_config", return_value=cfg):
            resp = client.get("/api/metrics-global")

        data = resp.json()
        assert len(data["projects"]) == 2
        names = {p["project_name"] for p in data["projects"]}
        assert "alpha" in names
        assert "beta" in names

    def test_cross_project_aggregates_across_projects(self, tmp_path):
        cfg = _cfg(tmp_path)
        proj_a = tmp_path / "alpha"
        proj_a.mkdir()
        proj_b = tmp_path / "beta"
        proj_b.mkdir()

        # Alpha: 3 runs, 6 total executor attempts  (avg per run = 2)
        _write_run_summary(proj_a, _make_run_summary(proj_a, executor_attempts_total=6))
        # Beta: 1 run, 2 executor attempts
        _write_run_summary(proj_b, _make_run_summary(proj_b, executor_attempts_total=2))

        _write_runs_index(tmp_path, [
            _make_index_entry(proj_a),
            _make_index_entry(proj_a),
            _make_index_entry(proj_a),
            _make_index_entry(proj_b),
        ])

        with patch("ui.server.load_config", return_value=cfg):
            resp = client.get("/api/metrics-global")

        cross = resp.json()["cross_project"]
        assert cross["total_runs"] == 4
        # (6+6+6+2) / 4 = 5.0  (we read same run_summary 3× for alpha)
        assert cross["avg_executor_attempts"] == 5.0


# ---------------------------------------------------------------------------
# Skill vs no-skill comparison
# ---------------------------------------------------------------------------

class TestSkillVsNoSkillComparison:
    def test_skill_vs_no_skill_in_cross_project(self, tmp_path):
        cfg = _cfg(tmp_path)
        proj = tmp_path / "proj"
        proj.mkdir()

        phases = [
            # with skill
            {"phase": "CORE-E1", "executor_attempts": 3, "blame": None,
             "skill_used": "core-logic", "last_error_code": None, "escalation_trigger_reason": None},
            {"phase": "CORE-E2", "executor_attempts": 2, "blame": None,
             "skill_used": "core-logic", "last_error_code": None, "escalation_trigger_reason": None},
            # without skill
            {"phase": "CORE-E3", "executor_attempts": 1, "blame": None,
             "skill_used": None, "last_error_code": None, "escalation_trigger_reason": None},
            {"phase": "CORE-E4", "executor_attempts": 1, "blame": None,
             "skill_used": None, "last_error_code": None, "escalation_trigger_reason": None},
            {"phase": "CORE-E5", "executor_attempts": 2, "blame": None,
             "skill_used": None, "last_error_code": None, "escalation_trigger_reason": None},
        ]
        _write_run_summary(proj, _make_run_summary(proj, phases=phases, phases_attempted=5))
        _write_runs_index(tmp_path, [_make_index_entry(proj)])

        with patch("ui.server.load_config", return_value=cfg):
            resp = client.get("/api/metrics-global")

        svns = resp.json()["cross_project"]["skill_vs_no_skill_executor_attempts"]
        # with_skill: (3+2)/2 = 2.5
        assert svns["with_skill"] == 2.5
        # without_skill: (1+1+2)/3 = 1.33
        assert svns["without_skill"] == round((1 + 1 + 2) / 3, 2)

    def test_skill_vs_no_skill_zeros_when_no_phases(self, tmp_path):
        cfg = _cfg(tmp_path)
        _write_runs_index(tmp_path, [])

        with patch("ui.server.load_config", return_value=cfg):
            resp = client.get("/api/metrics-global")

        svns = resp.json()["cross_project"]["skill_vs_no_skill_executor_attempts"]
        assert svns["with_skill"] == 0.0
        assert svns["without_skill"] == 0.0


# ---------------------------------------------------------------------------
# Malformed data
# ---------------------------------------------------------------------------

class TestMalformedData:
    def test_malformed_json_line_in_runs_index_skipped(self, tmp_path):
        cfg = _cfg(tmp_path)
        proj_a = tmp_path / "alpha"
        proj_a.mkdir()
        proj_b = tmp_path / "beta"
        proj_b.mkdir()

        _write_run_summary(proj_a, _make_run_summary(proj_a, project_name="alpha"))
        _write_run_summary(proj_b, _make_run_summary(proj_b, project_name="beta"))

        index = tmp_path / "runs_index.jsonl"
        with open(index, "w") as f:
            f.write(json.dumps(_make_index_entry(proj_a, project_name="alpha")) + "\n")
            f.write("NOTJSON{{{BAD\n")  # malformed line
            f.write(json.dumps(_make_index_entry(proj_b, project_name="beta")) + "\n")

        with patch("ui.server.load_config", return_value=cfg):
            resp = client.get("/api/metrics-global")

        assert resp.status_code == 200
        # Only 2 valid entries processed
        data = resp.json()
        assert len(data["projects"]) == 2

    def test_malformed_run_summary_json_skipped(self, tmp_path):
        cfg = _cfg(tmp_path)
        proj = tmp_path / "proj"
        proj.mkdir()

        # Write invalid JSON as run_summary.json
        art = proj / ".autodev" / "pipeline"
        art.mkdir(parents=True)
        (art / "run_summary.json").write_text("INVALID JSON {{")

        _write_runs_index(tmp_path, [_make_index_entry(proj)])

        with patch("ui.server.load_config", return_value=cfg):
            resp = client.get("/api/metrics-global")

        assert resp.status_code == 200
        # Invalid run_summary → project skipped, no crash
        assert resp.json()["projects"] == []
