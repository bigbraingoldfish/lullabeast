"""
W2-B: run_summary.json written at every terminal pipeline exit.
      runs_index.jsonl appended for cross-run history (W3-D source).

Tests verify:
- _write_run_summary helper exists and is called before each terminal transition_state
- run_summary.json contains full aggregated schema (phases, tokens, blame, skills)
- runs_index.jsonl is appended (O_APPEND), not overwritten, at AUTODEV_PIPELINE_ROOT
- Graceful on missing metrics.jsonl (writes summary with zero aggregates)
- Atomic write for run_summary.json; no temp files left behind

Pattern: source-text presence tests (fast) + runtime tests via tmp_path.
"""
import json
import pathlib

import autodev.pipeline.orchestrator as _orch_mod

_ORCH = pathlib.Path(__file__).parent.parent / "pipeline" / "orchestrator.py"
_SRC = _ORCH.read_text()
_LINES = _SRC.splitlines()


# ---------------------------------------------------------------------------
# Source-text presence tests (fast, no filesystem)
# ---------------------------------------------------------------------------

def test_write_run_summary_helper_defined():
    assert "def _write_run_summary(" in _SRC, (
        "_write_run_summary helper not found in orchestrator.py. "
        "Add a module-level helper called before every terminal transition_state."
    )


def _assert_called_before_transition(state_str, min_occurrences=1):
    """Assert _write_run_summary is called within 10 lines before every transition to state_str."""
    search = f'transition_state("{state_str}"'
    transition_lines = [i for i, l in enumerate(_LINES, 1) if search in l]
    assert len(transition_lines) >= min_occurrences, (
        f"Expected >= {min_occurrences} '{state_str}' transition(s), found {len(transition_lines)}"
    )
    for tc in transition_lines:
        window = _LINES[max(0, tc - 11) : tc]
        assert any("_write_run_summary(" in l for l in window), (
            f"No _write_run_summary call within 10 lines before "
            f'transition_state("{state_str}") at line {tc}. '
            "Insert _write_run_summary(outcome, detail) immediately before each terminal transition."
        )


def test_run_summary_called_before_pipeline_complete():
    _assert_called_before_transition("PIPELINE_COMPLETE", min_occurrences=2)


def test_run_summary_called_before_halted_silent():
    _assert_called_before_transition("HALTED_SILENT", min_occurrences=2)


def test_run_summary_called_before_blocked():
    _assert_called_before_transition("BLOCKED", min_occurrences=2)


def test_run_summary_called_before_stopped():
    _assert_called_before_transition("STOPPED", min_occurrences=1)


def test_run_summary_schema_version_present():
    assert '"schema_version"' in _SRC or "'schema_version'" in _SRC, (
        "schema_version not found in orchestrator source. "
        "Include it in the run_summary dict."
    )


def test_runs_index_written():
    assert "runs_index.jsonl" in _SRC, (
        "runs_index.jsonl not referenced in orchestrator. "
        "_write_run_summary must append to AUTODEV_PIPELINE_ROOT/runs_index.jsonl."
    )


def test_runs_index_path_uses_pipeline_root():
    """runs_index.jsonl must live at AUTODEV_PIPELINE_ROOT, not PROJECT_ARTIFACTS_DIR."""
    index_lines = [l for l in _LINES if "runs_index.jsonl" in l]
    assert index_lines
    assert any("AUTODEV_PIPELINE_ROOT" in l for l in index_lines), (
        "runs_index.jsonl must be constructed from AUTODEV_PIPELINE_ROOT (host-level path), "
        "not PROJECT_ARTIFACTS_DIR (project-level path). "
        "This ensures run history survives projects being removed from the queue."
    )


# ---------------------------------------------------------------------------
# Runtime helpers
# ---------------------------------------------------------------------------

def _make_metrics_rows(phases):
    return "\n".join(json.dumps(p) for p in phases) + "\n"


def _make_manifest(tmp_path, *, name="TestProj", started_at="2026-04-30T10:00:00Z"):
    return {
        "schema_version": 1,
        "project_path": str(tmp_path),
        "project_name": name,
        "queue_entry_id": "eid-1",
        "idea_id": "idea-1",
        "started_at": started_at,
        "phase_count": 2,
        "subsystem_set": ["CORE", "UI"],
        "total_goals_chars": 50,
    }


def _patch(tmp_art, tmp_root, fn):
    orig_pad = _orch_mod.PROJECT_ARTIFACTS_DIR
    orig_plr = _orch_mod.AUTODEV_PIPELINE_ROOT
    _orch_mod.PROJECT_ARTIFACTS_DIR = str(tmp_art)
    _orch_mod.AUTODEV_PIPELINE_ROOT = str(tmp_root)
    try:
        fn()
    finally:
        _orch_mod.PROJECT_ARTIFACTS_DIR = orig_pad
        _orch_mod.AUTODEV_PIPELINE_ROOT = orig_plr


# ---------------------------------------------------------------------------
# Runtime tests
# ---------------------------------------------------------------------------

def test_write_run_summary_complete_case(tmp_path):
    """PIPELINE_COMPLETE run: all fields aggregated correctly."""
    art = tmp_path / ".autodev" / "pipeline"
    art.mkdir(parents=True)

    rows = [
        {
            "phase": "CORE-E1", "executor_attempts": 2, "blame_fires": 1, "escalations": 0,
            "skill_used": "core-logic", "blame_verdict": "impl",
            "planner_tokens": {"input": 50, "output": 30, "cache_read": 10, "cache_write": 10,
                                "total_tokens": 100, "cost_total": 0.001},
            "executor_tokens": {"input": 200, "output": 200, "cache_read": 50, "cache_write": 50,
                                 "total_tokens": 500, "cost_total": 0.005},
            "reviewer_tokens": {"input": 80, "output": 80, "cache_read": 20, "cache_write": 20,
                                 "total_tokens": 200, "cost_total": 0.002},
            "cost_total": 0.008, "duration_seconds": 120,
        },
        {
            "phase": "UI-E1", "executor_attempts": 1, "blame_fires": 0, "escalations": 1,
            "skill_used": None, "blame_verdict": None,
            "planner_tokens": {}, "executor_tokens": {}, "reviewer_tokens": {},
            "cost_total": 0.0, "duration_seconds": 90,
        },
    ]
    (art / "metrics.jsonl").write_text(_make_metrics_rows(rows))
    (art / "run_manifest.json").write_text(json.dumps(_make_manifest(tmp_path)))

    _patch(art, tmp_path,
           lambda: _orch_mod._write_run_summary("PIPELINE_COMPLETE", "Pipeline fully complete"))

    summary_path = art / "run_summary.json"
    assert summary_path.exists(), "run_summary.json not written to PROJECT_ARTIFACTS_DIR"
    data = json.loads(summary_path.read_text())

    # Top-level identity fields
    assert data["schema_version"] == 1
    assert data["outcome"] == "PIPELINE_COMPLETE"
    assert data["outcome_detail"] == "Pipeline fully complete"
    assert data["project_name"] == "TestProj"
    assert data["idea_id"] == "idea-1"

    # Aggregated counters
    assert data["phases_attempted"] == 2
    assert data["executor_attempts_total"] == 3        # 2 + 1
    assert data["escalations_total"] == 1
    assert data["blame_fires_total"] == 1

    # phases array
    assert len(data["phases"]) == 2
    core_phase = next(p for p in data["phases"] if p["phase"] == "CORE-E1")
    assert core_phase["executor_attempts"] == 2
    assert core_phase["blame"] == "impl"
    assert core_phase["skill_used"] == "core-logic"

    # blame_attributions: only rows with non-null blame_verdict
    assert len(data["blame_attributions"]) == 1
    assert data["blame_attributions"][0] == {"phase": "CORE-E1", "blame": "impl"}

    # skills_injected: only rows with non-null skill_used
    assert len(data["skills_injected"]) == 1
    assert data["skills_injected"][0]["phase"] == "CORE-E1"
    assert data["skills_injected"][0]["discipline"] == "core-logic"

    # token_usage: summed across roles and phases
    tok = data["token_usage"]
    assert tok["total_tokens"] == 800    # 100 + 500 + 200

    # runs_index.jsonl appended at AUTODEV_PIPELINE_ROOT
    index_path = tmp_path / "runs_index.jsonl"
    assert index_path.exists(), "runs_index.jsonl not written"
    row = json.loads(index_path.read_text().strip().splitlines()[-1])
    assert row["outcome"] == "PIPELINE_COMPLETE"
    assert row["project_name"] == "TestProj"
    assert "ts" in row
    assert "run_end" in row


def test_write_run_summary_graceful_on_missing_metrics(tmp_path):
    """No metrics.jsonl — summary written with zero aggregates, no crash."""
    art = tmp_path / ".autodev" / "pipeline"
    art.mkdir(parents=True)

    _patch(art, tmp_path,
           lambda: _orch_mod._write_run_summary("BLOCKED", "Roadmap blocked"))

    summary_path = art / "run_summary.json"
    assert summary_path.exists()
    data = json.loads(summary_path.read_text())
    assert data["outcome"] == "BLOCKED"
    assert data["executor_attempts_total"] == 0
    assert data["escalations_total"] == 0
    assert data["phases"] == []
    assert data["blame_attributions"] == []
    assert data["skills_injected"] == []
    assert data["token_usage"]["total_tokens"] == 0


def test_write_run_summary_graceful_on_corrupt_metrics(tmp_path):
    """Corrupt lines in metrics.jsonl are skipped — remaining rows aggregated."""
    art = tmp_path / ".autodev" / "pipeline"
    art.mkdir(parents=True)
    (art / "metrics.jsonl").write_text(
        '{"phase": "CORE-E1", "executor_attempts": 1, "blame_fires": 0, "escalations": 0, '
        '"skill_used": null, "blame_verdict": null, "planner_tokens": {}, '
        '"executor_tokens": {}, "reviewer_tokens": {}, "cost_total": 0}\n'
        'THIS IS NOT JSON\n'
    )

    _patch(art, tmp_path,
           lambda: _orch_mod._write_run_summary("PIPELINE_COMPLETE", "Done"))

    data = json.loads((art / "run_summary.json").read_text())
    assert data["phases_attempted"] == 1   # corrupt line ignored


def test_runs_index_appends_on_multiple_runs(tmp_path):
    """Each call appends a new line — prior lines preserved (O_APPEND)."""
    art = tmp_path / ".autodev" / "pipeline"
    art.mkdir(parents=True)

    _patch(art, tmp_path,
           lambda: _orch_mod._write_run_summary("STOPPED", "Stop sentinel"))
    _patch(art, tmp_path,
           lambda: _orch_mod._write_run_summary("PIPELINE_COMPLETE", "Done"))

    lines = (tmp_path / "runs_index.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2, (
        f"Expected 2 lines in runs_index.jsonl after two calls, got {len(lines)}. "
        "Use O_APPEND — do not overwrite the index file."
    )
    assert json.loads(lines[0])["outcome"] == "STOPPED"
    assert json.loads(lines[1])["outcome"] == "PIPELINE_COMPLETE"


def test_run_summary_uses_atomic_write(tmp_path):
    """run_summary.json is written atomically — no temp file left behind."""
    art = tmp_path / ".autodev" / "pipeline"
    art.mkdir(parents=True)

    _patch(art, tmp_path,
           lambda: _orch_mod._write_run_summary("HALTED_SILENT", "Escalation failed"))

    files = {f.name for f in art.iterdir()}
    assert "run_summary.json" in files
    leftover_temps = {f for f in files if f.startswith(".run_summary_")}
    assert not leftover_temps, (
        f"Temp files left behind after atomic write: {leftover_temps}. "
        "Use mkstemp + os.replace."
    )


def test_run_summary_overwrites_on_second_call(tmp_path):
    """Calling _write_run_summary twice overwrites run_summary.json (not appends)."""
    art = tmp_path / ".autodev" / "pipeline"
    art.mkdir(parents=True)

    _patch(art, tmp_path,
           lambda: _orch_mod._write_run_summary("STOPPED", "First call"))
    _patch(art, tmp_path,
           lambda: _orch_mod._write_run_summary("PIPELINE_COMPLETE", "Second call"))

    data = json.loads((art / "run_summary.json").read_text())
    assert data["outcome"] == "PIPELINE_COMPLETE", (
        "run_summary.json should be overwritten on each call (latest terminal state wins). "
        "Got outcome from first call instead of second."
    )


def test_run_summary_all_terminal_outcomes(tmp_path):
    """Helper must accept all 4 terminal outcome strings without error."""
    art = tmp_path / ".autodev" / "pipeline"
    art.mkdir(parents=True)
    for outcome in ("PIPELINE_COMPLETE", "HALTED_SILENT", "BLOCKED", "STOPPED"):
        _patch(art, tmp_path,
               lambda o=outcome: _orch_mod._write_run_summary(o, f"test {o}"))
        data = json.loads((art / "run_summary.json").read_text())
        assert data["outcome"] == outcome


def test_run_summary_phases_deduped_by_phase_id(tmp_path):
    """Duplicate phase rows (same phase id) are deduplicated — last row wins."""
    art = tmp_path / ".autodev" / "pipeline"
    art.mkdir(parents=True)
    rows = [
        {"phase": "CORE-E1", "executor_attempts": 1, "blame_fires": 0, "escalations": 0,
         "skill_used": None, "blame_verdict": None,
         "planner_tokens": {}, "executor_tokens": {}, "reviewer_tokens": {}, "cost_total": 0},
        # Same phase, later row is canonical (dedup logic matches orchestrator metrics write)
        {"phase": "CORE-E1", "executor_attempts": 3, "blame_fires": 1, "escalations": 0,
         "skill_used": "core-logic", "blame_verdict": "impl",
         "planner_tokens": {}, "executor_tokens": {}, "reviewer_tokens": {}, "cost_total": 0},
    ]
    (art / "metrics.jsonl").write_text(_make_metrics_rows(rows))

    _patch(art, tmp_path,
           lambda: _orch_mod._write_run_summary("PIPELINE_COMPLETE", "Done"))

    data = json.loads((art / "run_summary.json").read_text())
    assert data["phases_attempted"] == 1, (
        "Duplicate phase rows must be deduplicated — expected 1 unique phase, "
        f"got {data['phases_attempted']}."
    )
    assert data["executor_attempts_total"] == 3   # last row wins
