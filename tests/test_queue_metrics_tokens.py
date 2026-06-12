"""METRICS-E3 — token totals + per-phase metrics breakout on the Queue surfaces.

The Queue row gets compact cost/token metric chips and the row expansion gets
a per-phase cost/token table, so the server now carries:

- ``GET /api/queue`` per-entry summary block: additive ``tokens_total``
  (aggregated by the shared ``_project_metrics_totals`` helper, exactly like
  ``cost_total``; None when the project has no metrics).
- ``GET /api/queue/{id}/snapshot``: additive ``tokens_total`` plus
  ``metrics_phases`` — a compact per-phase projection using the SAME keys the
  Pipeline Monitor's completion table consumes ({phase, duration_seconds,
  executor_attempts, escalations, cost_total, tokens_total, tokens_breakdown})
  so the queue breakout and the monitor render from one shape.

Pattern mirrors tests/test_queue_summary_enrichment.py (cfg dict +
patch("ui.server.load_config")).
"""
import json
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from unittest.mock import patch

from ui.server import app

client = TestClient(app)


def _metrics_row(phase, duration=100, cost=1.25, planner_tok=10, executor_tok=20, reviewer_tok=5):
    return {
        "ts": "2026-06-01T00:00:00Z",
        "phase": phase,
        "goal": f"goal for {phase}",
        "duration_seconds": duration,
        "executor_attempts": 1,
        "reviewer_passes": 1,
        "blame_fires": 0,
        "escalations": 0,
        "skill_used": None,
        "cost_total": cost,
        "planner_tokens": {"total_tokens": planner_tok, "cost_total": cost / 2,
                           "input": planner_tok, "output": 0, "cache_read": 0, "cache_write": 0},
        "executor_tokens": {"total_tokens": executor_tok, "cost_total": cost / 4,
                            "input": executor_tok, "output": 0, "cache_read": 0, "cache_write": 0},
        "reviewer_tokens": {"total_tokens": reviewer_tok, "cost_total": cost / 4,
                            "input": reviewer_tok, "output": 0, "cache_read": 0, "cache_write": 0},
    }


def _write_metrics(proj_path, rows):
    art = proj_path / ".autodev" / "pipeline"
    art.mkdir(parents=True, exist_ok=True)
    with open(art / "metrics.jsonl", "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _make_entry(proj_path, state="READY", entry_id=None, position=1, **extra):
    entry = {
        "id": entry_id or str(uuid.uuid4()),
        "project_path": str(proj_path),
        "idea_id": None,
        "name": "test-project",
        "state": state,
        "position": position,
        "parent_id": None,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "started_at": "2026-04-30T08:00:00Z" if state == "ACTIVE" else None,
        "completed_at": None,
        "blocked_at": None,
        "skip_count": 0,
        "preflight_validated_at": datetime.now(timezone.utc).isoformat(),
        "notes": "",
    }
    entry.update(extra)
    return entry


def _make_cfg(tmp_path):
    return {
        "pipeline_state_path": str(tmp_path / "pipeline_state.json"),
        "phase_state_path": str(tmp_path / "phase_state.json"),
        "lock_path": str(tmp_path / "pipeline.lock"),
        "events_path": str(tmp_path / "pipeline_events.jsonl"),
        "project_dir_path": str(tmp_path / "proj"),
        "pipeline_queue_path": str(tmp_path / "pipeline_queue.json"),
        "autodev_pipeline_root": str(tmp_path),
    }


def _write_queue(cfg, entries):
    data = {
        "queue": entries,
        "queue_mode": "auto",
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    with open(cfg["pipeline_queue_path"], "w") as f:
        json.dump(data, f)


def _get(cfg, url):
    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get(url)
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# GET /api/queue — per-entry tokens_total
# ---------------------------------------------------------------------------

class TestQueueEntryTokens:

    def test_entry_carries_tokens_total(self, tmp_path):
        proj = tmp_path / "proj-a"
        proj.mkdir()
        _write_metrics(proj, [
            _metrics_row("CORE-E1", planner_tok=100, executor_tok=200, reviewer_tok=50),
            _metrics_row("CORE-E2", planner_tok=1000, executor_tok=2000, reviewer_tok=500),
        ])
        cfg = _make_cfg(tmp_path)
        entry = _make_entry(proj, state="COMPLETED")
        _write_queue(cfg, [entry])

        data = _get(cfg, "/api/queue")
        (row,) = data["queue"]
        assert row["tokens_total"] == 3850
        assert row["cost_total"] == 2.5  # additive — cost unchanged

    def test_entry_without_metrics_reads_none(self, tmp_path):
        proj = tmp_path / "proj-b"
        proj.mkdir()
        cfg = _make_cfg(tmp_path)
        _write_queue(cfg, [_make_entry(proj, state="READY")])

        data = _get(cfg, "/api/queue")
        (row,) = data["queue"]
        assert row["tokens_total"] is None
        assert row["cost_total"] is None


# ---------------------------------------------------------------------------
# GET /api/queue/{id}/snapshot — tokens_total + metrics_phases breakout
# ---------------------------------------------------------------------------

class TestSnapshotBreakout:

    def test_snapshot_carries_tokens_and_phase_breakout(self, tmp_path):
        proj = tmp_path / "proj-c"
        proj.mkdir()
        _write_metrics(proj, [
            _metrics_row("CORE-E1", duration=60, cost=1.0,
                         planner_tok=100, executor_tok=200, reviewer_tok=50),
            _metrics_row("UI-E1", duration=120, cost=2.0,
                         planner_tok=10, executor_tok=20, reviewer_tok=5),
        ])
        cfg = _make_cfg(tmp_path)
        entry = _make_entry(proj, state="COMPLETED", entry_id="snap-1")
        _write_queue(cfg, [entry])

        snap = _get(cfg, "/api/queue/snap-1/snapshot")
        assert snap["tokens_total"] == 385
        p1, p2 = snap["metrics_phases"]
        # Same keys the Pipeline Monitor completion table consumes.
        assert p1["phase"] == "CORE-E1"
        assert p1["duration_seconds"] == 60
        assert p1["executor_attempts"] == 1
        assert p1["escalations"] == 0
        assert p1["cost_total"] == 1.0
        assert p1["tokens_total"] == 350
        assert p1["tokens_breakdown"] == {
            "input": 350, "output": 0, "cache_read": 0, "cache_write": 0,
        }
        assert p2["phase"] == "UI-E1"
        assert p2["tokens_total"] == 35

    def test_snapshot_without_metrics_degrades(self, tmp_path):
        proj = tmp_path / "proj-d"
        proj.mkdir()
        cfg = _make_cfg(tmp_path)
        _write_queue(cfg, [_make_entry(proj, state="READY", entry_id="snap-2")])

        snap = _get(cfg, "/api/queue/snap-2/snapshot")
        assert snap["tokens_total"] is None
        assert snap["metrics_phases"] == []

    def test_breakout_dedups_keep_last_per_phase(self, tmp_path):
        """A reset re-run's later row replaces the earlier one — the breakout
        applies the same keep-last rule as every other metrics reader."""
        proj = tmp_path / "proj-e"
        proj.mkdir()
        _write_metrics(proj, [
            _metrics_row("CORE-E1", executor_tok=100),
            _metrics_row("CORE-E1", executor_tok=999),
        ])
        cfg = _make_cfg(tmp_path)
        _write_queue(cfg, [_make_entry(proj, state="COMPLETED", entry_id="snap-3")])

        snap = _get(cfg, "/api/queue/snap-3/snapshot")
        (p,) = snap["metrics_phases"]
        assert p["tokens_total"] == 999 + 10 + 5
