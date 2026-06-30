"""TDD guard for the operator RESET_REVIEWER budget restore (v0.1.1).

``reset_reviewer()`` is the operator-driven reviewer reset. Like
``reset_execution('escalation')`` does for the executor, it must restore a FRESH
reviewer retry budget — otherwise the already-maxed ``reviewer_contract_retries``
(cap 3), ``reviewer_unverified_retries`` (cap 2), and ``reviewer_artifacts_retries``
(cap 2) survive the reset, and the very next reviewer failure re-escalates
immediately. That is the "fast fail, no retries" symptom observed live: the
contract counter climbing 3 -> 4 -> 5 across three operator resets, each granting
only one attempt before bouncing back to escalation.

These counters are deliberately PRESERVED by ``reset_execution`` (per-phase auto
budget) and zeroed by ``reset_phase`` — see test_contract_failure_orchestrator.py.
An operator RESET_REVIEWER is the third case: an explicit human decision to give
the reviewer a clean shot, so it zeros them too.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch


OPENCLAW_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in [os.path.join(OPENCLAW_DIR, "autodev", "pipeline"), OPENCLAW_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _make_reviewer_reset_orch(tmp_dir, phase_state):
    """Construct an Orchestrator with module FS paths patched into tmp_dir.

    PROJECT_ARTIFACTS_DIR is computed at module load from the real SYMLINK_TARGET,
    so it is patched explicitly here — reset_reviewer() os.remove()s reviewer
    output files from it, and the test must never touch the real project tree.
    """
    import orchestrator as orc_module

    ps_path = os.path.join(tmp_dir, "phase_state.json")
    with open(ps_path, "w") as f:
        json.dump(phase_state, f)

    with (
        patch.object(orc_module, "SYMLINK_TARGET", tmp_dir),
        patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
        patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", tmp_dir),
    ):
        from orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch.lock_fd = None
        orch.openclaw_config = {"hooks": {"token": "tok"}}
        orch.state = {
            "current_phase": 1,
            "current_phase_raw_id": "CORE-1",
            "current_agent": "escalation",
            "pipeline_status": "RUNNING",
            "last_action": "",
            "last_action_timestamp": "",
            "reviewer_retries": 3,
        }
        orch.write_state = MagicMock()
        orch.transition_state = MagicMock()
    return orch, ps_path


def test_reset_reviewer_zeros_contract_and_unverified_counters(tmp_workspace):
    """RESET_REVIEWER restores a fresh reviewer budget: zero both pooled retry
    counters, zero reviewer_retries, clear reviewer_rejected, count the reset
    toward the cap, log an audit entry, and route back to the reviewer."""
    import orchestrator as orc_module

    orch, ps_path = _make_reviewer_reset_orch(
        tmp_workspace,
        {
            "reviewer_retries": 3,
            "reviewer_rejected": True,
            "reviewer_contract_retries": 2,
            "reviewer_unverified_retries": 1,
            "reviewer_artifacts_retries": 2,
            "escalation_resets": 1,
            "last_error_code": "ERR_REVIEWER_CONTRACT_FAILURE",
        },
    )

    with (
        patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
        patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
        patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", tmp_workspace),
    ):
        orch.reset_reviewer()

    with open(ps_path) as f:
        state = json.load(f)

    # The fix under test: both pooled reviewer counters are restored to 0.
    assert state.get("reviewer_contract_retries") == 0, (
        "RESET_REVIEWER must zero reviewer_contract_retries (fresh operator budget); "
        "otherwise the next CONTRACT_FAILURE re-escalates immediately (3->4->5)"
    )
    assert state.get("reviewer_unverified_retries") == 0, (
        "RESET_REVIEWER must zero reviewer_unverified_retries (fresh operator budget)"
    )
    assert state.get("reviewer_artifacts_retries") == 0, (
        "RESET_REVIEWER must zero reviewer_artifacts_retries (cap 2) too, since otherwise "
        "the next MISSING_ARTIFACTS escalates instantly instead of re-invoking the executor"
    )
    # Pre-existing reset_reviewer behaviour must be preserved.
    assert state.get("reviewer_retries") == 0
    assert state.get("reviewer_rejected") is False
    assert state.get("escalation_resets") == 2, "operator reset still counts toward the cap"
    assert orch.state["reviewer_retries"] == 0, "self.state mirror must be zeroed (UI chips)"
    assert orch.state["current_agent"] == "reviewer", "must route back to the reviewer"
    reset_log = state.get("reset_log") or []
    assert reset_log and reset_log[-1]["command"] == "RESET_REVIEWER", (
        "must append a RESET_REVIEWER audit entry"
    )


def test_reset_reviewer_handles_absent_counters(tmp_workspace):
    """When the counters were never written (a phase that never hit a contract /
    unverified retry), RESET_REVIEWER still produces an explicit 0 — no KeyError,
    and the field is present so downstream reads are deterministic."""
    import orchestrator as orc_module

    orch, ps_path = _make_reviewer_reset_orch(
        tmp_workspace, {"reviewer_retries": 1, "escalation_resets": 0}
    )

    with (
        patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
        patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
        patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", tmp_workspace),
    ):
        orch.reset_reviewer()

    with open(ps_path) as f:
        state = json.load(f)
    assert state.get("reviewer_contract_retries") == 0
    assert state.get("reviewer_unverified_retries") == 0
    assert state.get("reviewer_artifacts_retries") == 0
