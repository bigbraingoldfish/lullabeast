"""METRICS-E4 — GET /api/queue/{entry_id}/report: durable completion report.

The Pipeline Monitor's completion view reads the ACTIVE project's metrics
(``/api/metrics-summary``) and ``completion_report.md`` (``/api/completion-report``)
— both go dark once the queue advances to the next project. This endpoint
rebuilds the same payload for ANY queue entry's project so a COMPLETED row's
report stays recallable:

- ``metrics_summary`` — the full /api/metrics-summary shape, built against the
  entry's project via the shared ``_build_project_metrics_summary`` (the
  refactored body behind the active-project endpoint, so the two cannot drift).
- ``completion_report`` — {found, content, mtime}, same shape as
  /api/completion-report.
- ``phases_total`` / ``phases_complete`` — roadmap checkbox stats.

Read-only; 404 on unknown entry. Fixtures mirror tests/test_queue_metrics_tokens.py.
"""
import json
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from unittest.mock import patch

from ui.server import app

client = TestClient(app)

ROADMAP_2_OF_3 = (
    "- [x] `CORE-E1` | LOW | Phase one\n  > Test: ok.\n"
    "- [x] `CORE-E2` | MEDIUM | Phase two\n  > Test: ok.\n"
    "- [ ] `CORE-E3` | HIGH | Phase three\n  > Test: ok.\n"
)


def _metrics_row(phase, duration=100, cost=1.0, tok=100):
    return {
        "ts": "2026-06-01T00:00:00Z",
        "phase": phase,
        "goal": f"goal for {phase}",
        "duration_seconds": duration,
        "executor_attempts": 1,
        "reviewer_passes": 1,
        "escalations": 0,
        "skill_used": None,
        "cost_total": cost,
        "planner_tokens": {"total_tokens": tok, "cost_total": cost / 2},
        "executor_tokens": {"total_tokens": tok, "cost_total": cost / 4},
        "reviewer_tokens": {"total_tokens": tok, "cost_total": cost / 4},
    }


def _seed_project(proj, rows=None, roadmap=None, completion_report=None):
    proj.mkdir(parents=True, exist_ok=True)
    if rows is not None:
        art = proj / ".autodev" / "pipeline"
        art.mkdir(parents=True, exist_ok=True)
        with open(art / "metrics.jsonl", "w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
    if roadmap is not None:
        (proj / "roadmap.md").write_text(roadmap)
    if completion_report is not None:
        (proj / "completion_report.md").write_text(completion_report)


def _make_entry(proj_path, state="COMPLETED", entry_id=None, position=1):
    return {
        "id": entry_id or str(uuid.uuid4()),
        "project_path": str(proj_path),
        "idea_id": None,
        "name": "test-project",
        "state": state,
        "position": position,
        "parent_id": None,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "completed_at": None,
        "blocked_at": None,
        "skip_count": 0,
        "preflight_validated_at": datetime.now(timezone.utc).isoformat(),
        "notes": "",
    }


def _make_cfg(tmp_path):
    return {
        "pipeline_state_path": str(tmp_path / "pipeline_state.json"),
        "phase_state_path": str(tmp_path / "phase_state.json"),
        "lock_path": str(tmp_path / "pipeline.lock"),
        "events_path": str(tmp_path / "pipeline_events.jsonl"),
        "project_dir_path": str(tmp_path / "other-active-proj"),
        "pipeline_queue_path": str(tmp_path / "pipeline_queue.json"),
        "autodev_pipeline_root": str(tmp_path),
    }


def _write_queue(cfg, entries):
    with open(cfg["pipeline_queue_path"], "w") as f:
        json.dump({"queue": entries, "queue_mode": "auto",
                   "last_updated": datetime.now(timezone.utc).isoformat()}, f)


def _get(cfg, url, expect=200):
    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get(url)
    assert resp.status_code == expect, resp.text
    return resp.json() if expect == 200 else resp


def test_unknown_entry_404(tmp_path):
    cfg = _make_cfg(tmp_path)
    _write_queue(cfg, [])
    _get(cfg, "/api/queue/nope/report", expect=404)


def test_report_full_payload(tmp_path):
    proj = tmp_path / "proj-done"
    _seed_project(
        proj,
        rows=[_metrics_row("CORE-E1"), _metrics_row("CORE-E2", cost=2.0, tok=1000)],
        roadmap=ROADMAP_2_OF_3,
        completion_report="# Done\n\nShipped.",
    )
    cfg = _make_cfg(tmp_path)
    _write_queue(cfg, [_make_entry(proj, entry_id="rep-1")])

    data = _get(cfg, "/api/queue/rep-1/report")
    assert data["id"] == "rep-1"
    assert data["name"] == "test-project"
    assert data["state"] == "COMPLETED"

    ms = data["metrics_summary"]
    # Same shape as /api/metrics-summary — totals + per-phase rows.
    assert ms["total_phases"] == 2
    assert ms["total_cost"] == 3.0
    assert ms["total_tokens"] == 300 + 3000
    assert {p["phase"] for p in ms["phases"]} == {"CORE-E1", "CORE-E2"}
    assert ms["phases"][0]["tokens_total"] == 300

    cr = data["completion_report"]
    assert cr["found"] is True
    assert "Shipped." in cr["content"]
    assert cr["mtime"] is not None

    assert data["phases_total"] == 3
    assert data["phases_complete"] == 2


def test_report_targets_entry_project_not_active(tmp_path):
    """The report must read the ENTRY's project — never the globally-active
    project_dir_path (the exact staleness this endpoint exists to fix)."""
    active = tmp_path / "other-active-proj"
    _seed_project(active, rows=[_metrics_row("ACTIVE-E1", cost=99.0)])
    proj = tmp_path / "proj-mine"
    _seed_project(proj, rows=[_metrics_row("MINE-E1", cost=1.0)])
    cfg = _make_cfg(tmp_path)
    _write_queue(cfg, [_make_entry(proj, entry_id="rep-2")])

    ms = _get(cfg, "/api/queue/rep-2/report")["metrics_summary"]
    assert {p["phase"] for p in ms["phases"]} == {"MINE-E1"}
    assert ms["total_cost"] == 1.0


def test_report_degrades_without_artifacts(tmp_path):
    """No metrics / no report / no roadmap → empty summary + found:False +
    None counts; never a 500."""
    proj = tmp_path / "proj-bare"
    proj.mkdir()
    cfg = _make_cfg(tmp_path)
    _write_queue(cfg, [_make_entry(proj, state="READY", entry_id="rep-3")])

    data = _get(cfg, "/api/queue/rep-3/report")
    assert data["metrics_summary"]["total_phases"] == 0
    assert data["metrics_summary"]["phases"] == []
    assert data["completion_report"]["found"] is False
    assert data["phases_total"] is None
    assert data["phases_complete"] is None


def test_active_endpoint_unchanged_by_refactor(tmp_path):
    """Golden guard — /api/metrics-summary keeps its exact behavior across the
    _build_project_metrics_summary extraction."""
    proj = tmp_path / "active"
    _seed_project(proj, rows=[_metrics_row("CORE-E1", duration=60, cost=1.5, tok=10)])
    cfg = _make_cfg(tmp_path)
    cfg["project_dir_path"] = str(proj)

    data = _get(cfg, "/api/metrics-summary")
    assert data["total_phases"] == 1
    assert data["total_cost"] == 1.5
    assert data["total_tokens"] == 30
    assert data["total_duration_seconds"] == 60
    (p,) = data["phases"]
    assert p["phase"] == "CORE-E1"
    assert p["planner_tokens"] == 10
