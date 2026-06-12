"""P0 Stage H — reset_execution counter behaviour.

``reset_execution(caller)`` is the orchestrator's single entry point for
the executor-self-failure retry path. Stage H adds:

* On ``caller == 'auto'`` — increment ``executor_self_failure_retries``
  alongside the existing ``executor_retries`` increment, and set the
  process-local tracker ``self._current_attempt_retry_class`` to
  ``"executor_self_failure"`` so subsequent ``attempt_end`` and
  ``gate_fail`` events carry the correct label.
* On ``caller == 'escalation'`` — NEITHER new counter is touched (they
  are lifetime accumulators, not segment-budget; the operator-driven
  reset preserves visibility into prior failures).
* On ``caller == 'auto'`` — ``executor_reviewer_rejection_retries`` is
  NOT touched (no double-increment with the ROUTE_EXECUTOR handler).

Pattern: runtime drive of ``reset_execution`` with subprocess stubbed.
Mirrors ``test_reviewer_routing_dispatch.py::TestResetExecutionStateSync``.
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


class _R:
    """Stub for ``subprocess.CompletedProcess``."""
    returncode = 0
    stdout = ""
    stderr = ""


def _bare_orch_with_state(tmp_path, monkeypatch, phase_state, self_state=None):
    """Build a bare Orchestrator with stubbed paths and the given phase_state."""
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path))
    monkeypatch.setattr(
        orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json")
    )
    monkeypatch.setattr(
        orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json")
    )

    (tmp_path / "phase_state.json").write_text(json.dumps(phase_state))

    default_self_state = {
        "current_phase": 1,
        "current_phase_raw_id": "CORE-E1",
        "current_agent": "executor",
        "executor_retries": phase_state.get("executor_retries", 0),
        "reviewer_retries": phase_state.get("reviewer_retries", 0),
        "planner_retries": 0,
        "pipeline_status": "RUNNING",
        "project_path": str(tmp_path),
    }
    if self_state:
        default_self_state.update(self_state)

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = default_self_state
    orch.openclaw_config = {}
    orch.lock_fd = None
    orch._current_attempt_retry_class = "initial_attempt"

    import subprocess as _sp

    monkeypatch.setattr(_sp, "run", lambda *a, **k: _R())
    return orch


def test_auto_caller_increments_self_failure_counter(tmp_path, monkeypatch):
    """``reset_execution('auto')`` must bump ``executor_self_failure_retries``."""
    orch = _bare_orch_with_state(
        tmp_path, monkeypatch,
        {
            "executor_retries": 0,
            "executor_self_failure_retries": 2,
            "executor_reviewer_rejection_retries": 1,
            "reviewer_retries": 0,
            "reviewer_rejected": False,
            "escalation_resets": 0,
        },
    )

    orch.reset_execution("auto")

    ps = json.loads((tmp_path / "phase_state.json").read_text())
    assert ps.get("executor_self_failure_retries") == 3, (
        "reset_execution('auto') must increment executor_self_failure_retries "
        "from 2 → 3. The new lifetime counter tracks every self-failure "
        "across the phase so the metrics row's executor_attempts invariant "
        "holds across reviewer rejections."
    )


def test_auto_caller_also_increments_legacy_executor_retries(tmp_path, monkeypatch):
    """Regression guard: the existing ``executor_retries`` increment must
    remain (per-segment budget for escalation/cap logic)."""
    orch = _bare_orch_with_state(
        tmp_path, monkeypatch,
        {
            "executor_retries": 1,
            "executor_self_failure_retries": 0,
            "executor_reviewer_rejection_retries": 0,
            "reviewer_retries": 0,
            "reviewer_rejected": False,
            "escalation_resets": 0,
        },
    )

    orch.reset_execution("auto")

    ps = json.loads((tmp_path / "phase_state.json").read_text())
    assert ps.get("executor_retries") == 2, (
        "Legacy executor_retries semantics MUST stay unchanged — it is the "
        "per-segment budget that escalation/blame caps key off. Stage H "
        "adds a *parallel* lifetime counter without removing the segment "
        "counter."
    )


def test_auto_caller_syncs_self_failure_to_self_state(tmp_path, monkeypatch):
    """State-sync invariant: ``self.state['executor_self_failure_retries']``
    must mirror phase_state after the reset. Same property as the existing
    ``executor_retries`` and ``reviewer_retries`` sync."""
    orch = _bare_orch_with_state(
        tmp_path, monkeypatch,
        {
            "executor_retries": 0,
            "executor_self_failure_retries": 4,
            "executor_reviewer_rejection_retries": 0,
            "reviewer_retries": 0,
            "reviewer_rejected": False,
            "escalation_resets": 0,
        },
    )

    orch.reset_execution("auto")

    assert orch.state.get("executor_self_failure_retries") == 5, (
        "self.state['executor_self_failure_retries'] must be incremented in "
        "lockstep with phase_state.json. Drift between the two state stores "
        "is the CORE-E6 bug class this invariant exists to prevent."
    )


def test_escalation_caller_does_not_touch_new_counters(tmp_path, monkeypatch):
    """Operator-driven escalation reset resets the per-segment ``executor_retries``
    to give the executor a fresh budget but must NOT clear the new
    lifetime counters — those represent the operator's history into prior
    failures and survive escalation."""
    orch = _bare_orch_with_state(
        tmp_path, monkeypatch,
        {
            "executor_retries": 3,
            "executor_self_failure_retries": 5,
            "executor_reviewer_rejection_retries": 3,
            "reviewer_retries": 0,
            "reviewer_rejected": False,
            "escalation_resets": 0,
        },
    )

    orch.reset_execution("escalation")

    ps = json.loads((tmp_path / "phase_state.json").read_text())
    # Legacy budget reset to 0 — existing behaviour, preserved.
    assert ps.get("executor_retries") == 0, (
        "Legacy executor_retries reset to 0 on escalation reset MUST be "
        "preserved (regression guard for the operator-driven fresh-budget "
        "behaviour)."
    )
    # Lifetime counters unchanged.
    assert ps.get("executor_self_failure_retries") == 5, (
        "executor_self_failure_retries must NOT be touched by "
        "reset_execution('escalation') — lifetime visibility into prior "
        "failures is precisely what the operator needs to decide whether "
        "another retry is worth attempting."
    )
    assert ps.get("executor_reviewer_rejection_retries") == 3, (
        "executor_reviewer_rejection_retries must NOT be touched by "
        "reset_execution('escalation') for the same reason."
    )
    # Escalation counter incremented as today.
    assert ps.get("escalation_resets") == 1


def test_auto_caller_does_not_touch_rejection_counter(tmp_path, monkeypatch):
    """Guard against accidental double-increment: ``reset_execution('auto')``
    is fired by executor self-failures, not reviewer rejections. The
    rejection counter is incremented at the ROUTE_EXECUTOR handler site
    (see ``test_p0_stage_h_route_executor_counter.py``), not here."""
    orch = _bare_orch_with_state(
        tmp_path, monkeypatch,
        {
            "executor_retries": 0,
            "executor_self_failure_retries": 0,
            "executor_reviewer_rejection_retries": 2,
            "reviewer_retries": 0,
            "reviewer_rejected": False,
            "escalation_resets": 0,
        },
    )

    orch.reset_execution("auto")

    ps = json.loads((tmp_path / "phase_state.json").read_text())
    assert ps.get("executor_reviewer_rejection_retries") == 2, (
        "executor_reviewer_rejection_retries must NOT change inside "
        "reset_execution('auto'). The rejection path bypasses this method "
        "entirely (see orchestrator.py ROUTE_EXECUTOR handler); counting "
        "it here would double-bump on the rejection→reset_execution flow."
    )


def test_auto_caller_sets_retry_class_to_self_failure(tmp_path, monkeypatch):
    """``reset_execution('auto')`` must set
    ``self._current_attempt_retry_class = 'executor_self_failure'`` so the
    next executor attempt's ``attempt_end`` and ``gate_fail`` events carry
    the correct retry classification."""
    orch = _bare_orch_with_state(
        tmp_path, monkeypatch,
        {
            "executor_retries": 0,
            "executor_self_failure_retries": 0,
            "executor_reviewer_rejection_retries": 0,
            "reviewer_retries": 0,
            "reviewer_rejected": False,
            "escalation_resets": 0,
        },
    )

    orch.reset_execution("auto")

    assert orch._current_attempt_retry_class == "executor_self_failure", (
        "After reset_execution('auto'), the orchestrator-private "
        "retry-class tracker must read 'executor_self_failure' so the "
        "next attempt's events label the retry source correctly. Without "
        "this, the UI activity feed cannot distinguish self-failure from "
        "reviewer-rejection retries."
    )
