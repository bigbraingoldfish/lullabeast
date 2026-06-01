"""Phase 3 — ``last_phase_outcome`` records how a phase terminated.

``_record_phase_outcome`` (the generic merge helper) gains a new field,
``last_phase_outcome``, with a terminal-only value set:
``completed`` / ``escalated`` / ``nuclear_reset`` (absent while in-progress).

Reachability is deliberately NOT an outcome value — it is non-terminal (the
phase still completes after a reachability advisory), so it is captured
orthogonally by the metrics row's ``reachability_summary`` field instead.

Two test styles:

* **behavioural** — the helper merges the new field like any other (reuses the
  ``bare_orch`` idiom from ``test_phase_state_outcome_fields.py``). These pin
  the helper contract; a future "optimisation" that whitelisted specific
  fields would break them.
* **source-text guards** — the four wire-points sit deep inside ``run()`` and
  the reset paths and cannot be unit-driven without a full loop harness, so we
  assert on the source text + ordering. These are the failing-first tests for
  the new wiring (the behavioural ones already pass because the helper is
  generic).
"""

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402

_ORCH_SRC = open(os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8").read()


@pytest.fixture
def bare_orch(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json"))
    (tmp_path / "phase_state.json").write_text(
        json.dumps({"reviewer_retries": 1, "executor_retries": 2})
    )
    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {"current_phase_raw_id": "CORE-E5"}
    orch.openclaw_config = {}
    orch.lock_fd = None
    return orch, tmp_path


# ---------------------------------------------------------------------------
# Behavioural — the helper merges last_phase_outcome
# ---------------------------------------------------------------------------


def test_record_phase_outcome_accepts_last_phase_outcome(bare_orch):
    orch, tmp_path = bare_orch
    orch._record_phase_outcome(last_phase_outcome="completed")
    ps = json.loads((tmp_path / "phase_state.json").read_text())
    assert ps.get("last_phase_outcome") == "completed"
    # Sibling keys preserved.
    assert ps.get("reviewer_retries") == 1
    assert ps.get("executor_retries") == 2


def test_last_phase_outcome_last_write_wins(bare_orch):
    orch, tmp_path = bare_orch
    orch._record_phase_outcome(last_phase_outcome="escalated")
    orch._record_phase_outcome(last_phase_outcome="completed")
    ps = json.loads((tmp_path / "phase_state.json").read_text())
    assert ps.get("last_phase_outcome") == "completed"


# ---------------------------------------------------------------------------
# Source-text guards — the four wire-points (4a main, 4a repo-init, 4b, 4c)
# ---------------------------------------------------------------------------


def test_escalated_recorded_at_main_chokepoint():
    """The single main-loop escalation chokepoint (``elif current_agent ==
    "escalation":`` → ``_should_invoke_escalation_agent`` → ``escalations += 1``)
    must set ``last_phase_outcome="escalated"`` once for all ~15 routes."""
    idx = _ORCH_SRC.find('elif current_agent == "escalation":')
    assert idx != -1, "main-loop escalation chokepoint not found"
    window = _ORCH_SRC[idx: idx + 4000]
    assert "_should_invoke_escalation_agent" in window
    assert "last_phase_outcome" in window and '"escalated"' in window, (
        'main escalation chokepoint must set last_phase_outcome="escalated"'
    )


def test_escalated_recorded_at_repo_init_block():
    """The early repo-init escalation block escalates and returns WITHOUT
    entering the main loop, so it needs its own ``last_phase_outcome`` set."""
    idx = _ORCH_SRC.find("Repository setup needs your attention")
    assert idx != -1, "repo-init escalation block not found"
    window = _ORCH_SRC[idx: idx + 800]
    assert "last_phase_outcome" in window and '"escalated"' in window, (
        'repo-init escalation block must set last_phase_outcome="escalated"'
    )


def test_completed_recorded_between_metrics_and_archive():
    """``last_phase_outcome="completed"`` must be written AFTER the metrics row
    and BEFORE the audit archive copies ``phase_state.json`` — phase_state is
    deleted ~50 lines later on advance, so the archive is the only place that
    captures the completed marker."""
    metrics_idx = _ORCH_SRC.find("self._write_canonical_metrics_row()")
    archive_idx = _ORCH_SRC.find("files_to_archive")
    completed_idx = _ORCH_SRC.find('last_phase_outcome="completed"')
    assert metrics_idx != -1, "metrics-row call site not found"
    assert archive_idx != -1, "audit-archive block not found"
    assert completed_idx != -1, (
        'run() must record last_phase_outcome="completed" on the reviewer-PASS path'
    )
    assert metrics_idx < completed_idx < archive_idx, (
        'last_phase_outcome="completed" must be written after the metrics row '
        "and before the audit archive copies phase_state.json"
    )


def test_nuclear_reset_records_outcome():
    """``nuclear_reset_phase`` must set ``last_phase_outcome="nuclear_reset"``,
    and ``reset_phase`` must preserve ``last_phase_outcome`` across its re-init
    dict — otherwise the value nuclear_reset_phase sets is wiped by the very
    ``reset_phase()`` call it makes."""
    nuke_idx = _ORCH_SRC.find("def nuclear_reset_phase")
    assert nuke_idx != -1
    nuke_window = _ORCH_SRC[nuke_idx: nuke_idx + 2000]
    assert "last_phase_outcome" in nuke_window and '"nuclear_reset"' in nuke_window, (
        'nuclear_reset_phase must set last_phase_outcome="nuclear_reset"'
    )
    reset_idx = _ORCH_SRC.find("def reset_phase")
    assert reset_idx != -1
    reset_window = _ORCH_SRC[reset_idx: reset_idx + 6000]
    assert '"last_phase_outcome"' in reset_window, (
        "reset_phase must preserve last_phase_outcome in its re-init dict so a "
        "nuclear reset's outcome survives the reset"
    )
