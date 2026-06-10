"""Queue redesign — uniform per-entry summary block on GET /api/queue.

Every queue entry (not just ACTIVE) now carries six summary keys so the
flat-table Project Queue screen can render progress / cost / elapsed per row:

- ``phases_total`` / ``phases_complete`` — roadmap checkbox stats (None when
  no roadmap is readable; 0 for an empty roadmap, preserving W3-B semantics)
- ``cost_total`` / ``duration_seconds`` — aggregated from the project's
  ``.autodev/pipeline/metrics.jsonl`` via the shared ``_project_metrics_totals``
  helper (None when the file is absent/unreadable/empty)
- ``current_phase_raw_id`` — live entry: from pipeline_state; parked entry:
  from ``parked_state_snapshot``; COMPLETED/FAILED: last deduped metrics phase
- ``parked_agent`` — parked entry: ``parked_state_snapshot.current_agent``
  (None for pre-existing snapshots written before the key existed)

Also covered: the ``_project_metrics_totals`` helper itself (dedup keep-last
per phase — the same rule /api/metrics-summary applies), the two additive
snapshot-endpoint fields, and a golden guard pinning /api/metrics-summary
output across the helper-extraction refactor.

Pattern mirrors tests/test_w3b_queue_phases_progress.py (cfg dict +
patch("ui.server.load_config")).
"""
import json
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from ui.server import app

client = TestClient(app)

ROADMAP_4_PHASES_2_DONE = (
    "- [x] `CORE-E1` | LOW | Phase one\n  > Test: ok.\n"
    "- [x] `CORE-E2` | MEDIUM | Phase two\n  > Test: ok.\n"
    "- [ ] `CORE-E3` | HIGH | Phase three\n  > Test: ok.\n"
    "- [ ] `CORE-E4` | CRITICAL | Phase four\n  > Test: ok.\n"
)

SUMMARY_KEYS = (
    "phases_total",
    "phases_complete",
    "cost_total",
    "duration_seconds",
    "current_phase_raw_id",
    "parked_agent",
)


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
        "planner_tokens": {"total_tokens": planner_tok, "cost_total": cost / 2},
        "executor_tokens": {"total_tokens": executor_tok, "cost_total": cost / 4},
        "reviewer_tokens": {"total_tokens": reviewer_tok, "cost_total": cost / 4},
    }


def _write_metrics(proj_path, rows, raw_lines=None):
    """Seed {proj}/.autodev/pipeline/metrics.jsonl. ``raw_lines`` are written
    verbatim (for malformed-line cases); ``rows`` are JSON-encoded."""
    art = proj_path / ".autodev" / "pipeline"
    art.mkdir(parents=True, exist_ok=True)
    with open(art / "metrics.jsonl", "w") as f:
        for line in raw_lines or []:
            f.write(line + "\n")
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


def _write_queue(cfg, entries, ps=None):
    data = {
        "queue": entries,
        "queue_mode": "auto",
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    with open(cfg["pipeline_queue_path"], "w") as f:
        json.dump(data, f)
    if ps:
        with open(cfg["pipeline_state_path"], "w") as f:
            json.dump(ps, f)


def _get_queue(cfg):
    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/queue")
    assert resp.status_code == 200
    return resp.json()


def _entry_by_id(queue_json, eid):
    return next((e for e in queue_json["queue"] if e["id"] == eid), None)


# ---------------------------------------------------------------------------
# _project_metrics_totals — the shared aggregation helper
# ---------------------------------------------------------------------------

def test_metrics_totals_none_when_file_absent(tmp_path):
    """Missing metrics.jsonl (and falsy paths) -> None, mirroring the cases
    where /api/metrics-summary returns _empty_metrics_summary()."""
    from ui.server import _project_metrics_totals
    proj = tmp_path / "proj"
    proj.mkdir()
    assert _project_metrics_totals(str(proj)) is None
    assert _project_metrics_totals("") is None
    assert _project_metrics_totals(None) is None


def test_metrics_totals_sums_cost_and_duration(tmp_path):
    from ui.server import _project_metrics_totals
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_metrics(proj, [
        _metrics_row("CORE-E1", duration=100, cost=1.25),
        _metrics_row("CORE-E2", duration=50, cost=0.75),
    ])
    totals = _project_metrics_totals(str(proj))
    assert totals is not None
    assert totals["cost_total"] == 2.0
    assert totals["duration_seconds"] == 150
    assert totals["last_phase"] == "CORE-E2"
    assert len(totals["phases"]) == 2


def test_metrics_totals_dedup_keeps_last_row_per_phase(tmp_path):
    """Same rule as /api/metrics-summary: keep the LAST row per phase (a reset
    re-run replaces the earlier row), first-seen phase order preserved — so
    last_phase is the last DISTINCT phase, not the last appended line."""
    from ui.server import _project_metrics_totals
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_metrics(proj, [
        _metrics_row("CORE-E1", duration=10, cost=1.0),
        _metrics_row("CORE-E2", duration=20, cost=2.0),
        _metrics_row("CORE-E1", duration=30, cost=3.0),  # replaces the first row
    ])
    totals = _project_metrics_totals(str(proj))
    assert totals["duration_seconds"] == 50  # 30 (CORE-E1') + 20, not 10 + 20
    assert totals["cost_total"] == 5.0
    assert totals["last_phase"] == "CORE-E2"  # first-seen order, keep-last value


def test_metrics_totals_skips_blank_and_malformed_lines(tmp_path):
    from ui.server import _project_metrics_totals
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_metrics(
        proj,
        [_metrics_row("CORE-E1", duration=40, cost=1.5)],
        raw_lines=["not json {{{", ""],
    )
    totals = _project_metrics_totals(str(proj))
    assert totals["cost_total"] == 1.5
    assert totals["duration_seconds"] == 40
    assert totals["last_phase"] == "CORE-E1"


def test_metrics_totals_rows_without_phase_yield_zeroed_dict(tmp_path):
    """Rows that parse but lack a 'phase' key dedupe away — the helper must
    return a ZEROED dict (not None) so the refactored /api/metrics-summary
    keeps its current behavior for this edge (it proceeds with empty phases,
    it does not return _empty_metrics_summary())."""
    from ui.server import _project_metrics_totals
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_metrics(proj, [{"duration_seconds": 99, "cost_total": 9.9}])
    totals = _project_metrics_totals(str(proj))
    assert totals is not None
    assert totals["phases"] == []
    assert totals["cost_total"] == 0.0
    assert totals["duration_seconds"] == 0
    assert totals["last_phase"] is None


# ---------------------------------------------------------------------------
# _roadmap_phase_checkbox_stats — canonical ID grammar (E2E finding 2026-06-09)
# ---------------------------------------------------------------------------

ROADMAP_PLAIN_NUMBER_IDS = (
    "- [x] `INFRA-1` | LOW | Bootstrap the package\n  > Test: pytest passes.\n"
    "- [ ] `CORE-1` | LOW | Implement greet()\n  > Test: greet works.\n"
)


def test_phase_checkbox_stats_accepts_plain_number_ids():
    """`INFRA-1` / `CORE-1` (no letter before the number) are canonical —
    phase_resolver.py and ui/roadmap_parser.py accept any backticked ID, and the
    live smoke project completed a full pipeline run with them — yet the stats
    regex demanded `[A-Z]\\d+` and read phases_total=0, blanking PROGRESS
    everywhere (live E2E finding)."""
    from ui.server import _roadmap_phase_checkbox_stats
    total, completed = _roadmap_phase_checkbox_stats(ROADMAP_PLAIN_NUMBER_IDS)
    assert total == 2
    assert completed == 1


def test_phase_checkbox_stats_completed_counts_only_phase_lines():
    """`completed` must be the [x] subset of MATCHED phase lines — a stray
    checked task bullet elsewhere in the doc must not inflate it (previously it
    counted every `- [x] ` line, so completed could exceed total)."""
    from ui.server import _roadmap_phase_checkbox_stats
    content = ROADMAP_PLAIN_NUMBER_IDS + "- [x] remember to write docs\n"
    total, completed = _roadmap_phase_checkbox_stats(content)
    assert total == 2
    assert completed == 1


def test_queue_phase_counts_for_plain_number_ids(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_PLAIN_NUMBER_IDS)
    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(proj, state="READY", entry_id=eid)])

    found = _entry_by_id(_get_queue(cfg), eid)
    assert found["phases_total"] == 2
    assert found["phases_complete"] == 1


# ---------------------------------------------------------------------------
# GET /api/queue — uniform summary block on every entry
# ---------------------------------------------------------------------------

def test_queue_every_entry_has_uniform_summary_keys(tmp_path):
    cfg = _make_cfg(tmp_path)
    entries = []
    for i, state in enumerate(("READY", "ACTIVE", "BLOCKED", "COMPLETED"), start=1):
        proj = tmp_path / f"proj_{state.lower()}"
        proj.mkdir()
        entries.append(_make_entry(proj, state=state, position=i))
    ps = {"project_path": entries[1]["project_path"], "pipeline_status": "RUNNING"}
    _write_queue(cfg, entries, ps=ps)

    data = _get_queue(cfg)
    assert len(data["queue"]) == 4
    for entry in data["queue"]:
        for key in SUMMARY_KEYS:
            assert key in entry, f"{entry['state']} entry missing summary key {key!r}"


def test_queue_ready_entry_gets_phase_counts(tmp_path):
    """Inverts the old W3-B ACTIVE-only gate: a READY entry's roadmap counts
    are now computed too, so queued rows can render 0/N progress."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_4_PHASES_2_DONE)
    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(proj, state="READY", entry_id=eid)])

    found = _entry_by_id(_get_queue(cfg), eid)
    assert found["phases_total"] == 4
    assert found["phases_complete"] == 2


def test_queue_phases_none_when_roadmap_missing(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()  # no roadmap.md
    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(proj, state="READY", entry_id=eid)])

    found = _entry_by_id(_get_queue(cfg), eid)
    assert found["phases_total"] is None
    assert found["phases_complete"] is None


def test_queue_cost_duration_from_metrics(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_metrics(proj, [
        _metrics_row("CORE-E1", duration=100, cost=1.25),
        _metrics_row("CORE-E2", duration=50, cost=0.75),
    ])
    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(proj, state="COMPLETED", entry_id=eid)])

    found = _entry_by_id(_get_queue(cfg), eid)
    assert found["cost_total"] == 2.0
    assert found["duration_seconds"] == 150


def test_queue_cost_duration_none_when_metrics_absent(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(proj, state="READY", entry_id=eid)])

    found = _entry_by_id(_get_queue(cfg), eid)
    assert found["cost_total"] is None
    assert found["duration_seconds"] is None


def test_queue_live_active_entry_raw_id_from_pipeline_state(tmp_path):
    """The realpath-matched live entry surfaces pipeline_state's
    current_phase_raw_id so the table's PHASE cell needs no /api/state merge."""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    ps = {
        "project_path": str(proj),
        "pipeline_status": "RUNNING",
        "current_phase_raw_id": "CORE-E2",
        "current_agent": "executor",
    }
    _write_queue(cfg, [_make_entry(proj, state="ACTIVE", entry_id=eid)], ps=ps)

    found = _entry_by_id(_get_queue(cfg), eid)
    assert found["current_phase_raw_id"] == "CORE-E2"


def test_queue_stale_active_entry_raw_id_none(tmp_path):
    """An ACTIVE entry whose project does NOT match pipeline_state (stale row)
    must not inherit the live project's phase."""
    proj = tmp_path / "proj"
    proj.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    ps = {
        "project_path": str(other),
        "pipeline_status": "RUNNING",
        "current_phase_raw_id": "UI-E9",
    }
    _write_queue(cfg, [_make_entry(proj, state="ACTIVE", entry_id=eid)], ps=ps)

    found = _entry_by_id(_get_queue(cfg), eid)
    assert found["current_phase_raw_id"] is None


@pytest.mark.parametrize("state", ["ESCALATION", "ESCALATION_ANSWERED", "BLOCKED"])
def test_queue_parked_entry_raw_id_and_agent_from_snapshot(tmp_path, state):
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    entry = _make_entry(
        proj, state=state, entry_id=eid,
        parked_state_snapshot={"current_phase_raw_id": "UI-E2", "current_agent": "reviewer"},
        parked_at="2026-06-01T00:00:00Z",
        parked_reason="escalation",
        parked_pipeline_status="WAITING_FOR_HUMAN",
    )
    _write_queue(cfg, [entry])

    found = _entry_by_id(_get_queue(cfg), eid)
    assert found["current_phase_raw_id"] == "UI-E2"
    assert found["parked_agent"] == "reviewer"


def test_queue_parked_old_snapshot_without_agent_tolerated(tmp_path):
    """Snapshots written before current_agent existed must degrade to None,
    not 500."""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    entry = _make_entry(
        proj, state="ESCALATION", entry_id=eid,
        parked_state_snapshot={"current_phase_raw_id": "UI-E2"},
        parked_at="2026-06-01T00:00:00Z",
        parked_reason="escalation",
        parked_pipeline_status="WAITING_FOR_HUMAN",
    )
    _write_queue(cfg, [entry])

    found = _entry_by_id(_get_queue(cfg), eid)
    assert found["current_phase_raw_id"] == "UI-E2"
    assert found["parked_agent"] is None


@pytest.mark.parametrize("state", ["COMPLETED", "FAILED"])
def test_queue_completed_and_failed_raw_id_from_last_metrics_phase(tmp_path, state):
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_metrics(proj, [
        _metrics_row("CORE-E1"),
        _metrics_row("CORE-E2"),
    ])
    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(proj, state=state, entry_id=eid)])

    found = _entry_by_id(_get_queue(cfg), eid)
    assert found["current_phase_raw_id"] == "CORE-E2"

    # Without metrics there is no last phase to report.
    bare = tmp_path / "bare"
    bare.mkdir()
    eid2 = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(bare, state=state, entry_id=eid2)])
    found2 = _entry_by_id(_get_queue(cfg), eid2)
    assert found2["current_phase_raw_id"] is None


def test_queue_ingested_synthetic_row_carries_summary_keys(tmp_path):
    """The synthetic ingest-* row (active project missing from the queue file)
    goes through the same enrichment loop — keys present, endpoint stays 200."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_4_PHASES_2_DONE)
    cfg = _make_cfg(tmp_path)
    ps = {"project_path": str(proj), "pipeline_status": "RUNNING", "current_phase_raw_id": "CORE-E3"}
    _write_queue(cfg, [], ps=ps)

    data = _get_queue(cfg)
    assert len(data["queue"]) == 1
    row = data["queue"][0]
    assert row["id"].startswith("ingest-")
    for key in SUMMARY_KEYS:
        assert key in row, f"ingested row missing summary key {key!r}"
    assert row["phases_total"] == 4
    assert row["current_phase_raw_id"] == "CORE-E3"


# ---------------------------------------------------------------------------
# GET /api/queue/{id}/snapshot — additive cost_total + duration_seconds
# ---------------------------------------------------------------------------

def test_snapshot_includes_cost_total_and_duration(tmp_path):
    """Revives the dashboard's snapshot cost line (index.html guards on
    snap.cost_total > 0; the endpoint never returned the field before)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "roadmap.md").write_text(ROADMAP_4_PHASES_2_DONE)
    _write_metrics(proj, [_metrics_row("CORE-E1", duration=100, cost=1.25)])
    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(proj, state="READY", entry_id=eid)])

    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get(f"/api/queue/{eid}/snapshot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cost_total"] == 1.25
    assert data["duration_seconds"] == 100


def test_snapshot_cost_duration_none_when_metrics_absent(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _make_cfg(tmp_path)
    eid = str(uuid.uuid4())
    _write_queue(cfg, [_make_entry(proj, state="READY", entry_id=eid)])

    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get(f"/api/queue/{eid}/snapshot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cost_total"] is None
    assert data["duration_seconds"] is None


# ---------------------------------------------------------------------------
# Refactor guard — /api/metrics-summary behavior pinned across the extraction
# ---------------------------------------------------------------------------

def test_metrics_summary_response_identical_before_after_helper(tmp_path):
    """Golden guard for the helper extraction: hand-computed expectations for a
    seeded metrics.jsonl (duplicate phase rows, a malformed line, missing role
    dicts). Written GREEN against the pre-refactor endpoint; must stay green
    byte-for-byte after get_metrics_summary delegates to
    _project_metrics_totals."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_metrics(
        proj,
        [
            _metrics_row("CORE-E1", duration=100, cost=1.25, planner_tok=10, executor_tok=20, reviewer_tok=5),
            _metrics_row("CORE-E2", duration=50, cost=0.75, planner_tok=1, executor_tok=2, reviewer_tok=3),
            # Re-run of CORE-E1 replaces the first row (dedup keep-last)
            _metrics_row("CORE-E1", duration=200, cost=2.25, planner_tok=100, executor_tok=200, reviewer_tok=50),
            # Row without role dicts — _role_cost/_role_token_total default to 0
            {"phase": "CORE-E3", "duration_seconds": 25, "cost_total": 0.5},
        ],
        raw_lines=["this is not json"],
    )
    cfg = _make_cfg(tmp_path)
    cfg["project_dir_path"] = str(proj)

    with patch("ui.server.load_config", return_value=cfg):
        resp = client.get("/api/metrics-summary")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_phases"] == 3
    assert data["total_duration_seconds"] == 275  # 200 + 50 + 25
    assert data["total_cost"] == 3.5  # 2.25 + 0.75 + 0.5
    # Role costs come from the per-role dicts: cost/2, cost/4, cost/4
    assert data["planner_cost_total"] == round(2.25 / 2 + 0.75 / 2, 6)
    assert data["executor_cost_total"] == round(2.25 / 4 + 0.75 / 4, 6)
    assert data["reviewer_cost_total"] == round(2.25 / 4 + 0.75 / 4, 6)
    assert data["total_tokens"] == (100 + 200 + 50) + (1 + 2 + 3)
    assert data["total_hold_seconds"] == 0
    assert data["total_active_seconds"] == 275
    assert [p["phase"] for p in data["phases"]] == ["CORE-E1", "CORE-E2", "CORE-E3"]
    by_phase = {p["phase"]: p for p in data["phases"]}
    assert by_phase["CORE-E1"]["duration_seconds"] == 200
    assert by_phase["CORE-E1"]["cost_total"] == 2.25
    assert by_phase["CORE-E3"]["cost_total"] == 0.5
    assert by_phase["CORE-E3"]["tokens_total"] == 0
