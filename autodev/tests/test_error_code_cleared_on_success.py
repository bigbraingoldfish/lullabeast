"""last_error_code is cleared from phase_state when a gate passes (orchestrator.py)."""

import os
import sys

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PIPELINE_DIR = os.path.join(_REPO_DIR, "autodev", "pipeline")
for _p in [_PIPELINE_DIR, _REPO_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def test_orchestrator_clears_last_error_code_at_all_gate_pass_sites():
    """Planner, executor (normal + preempted), and reviewer success paths pop last_error_code."""
    orch_path = os.path.join(_PIPELINE_DIR, "orchestrator.py")
    with open(orch_path, "r", encoding="utf-8") as f:
        src = f.read()
    assert '_ps_pp.pop("last_error_code", None)' in src
    assert '_ps_ex.pop("last_error_code", None)' in src
    assert '_ps_ep.pop("last_error_code", None)' in src
    assert '_ps_rv.pop("last_error_code", None)' in src


def test_write_failure_context_path_does_not_clear_last_error_code():
    """Gate-failure path must not silently drop last_error_code (operators need the signal)."""
    orch_path = os.path.join(_PIPELINE_DIR, "orchestrator.py")
    with open(orch_path, "r", encoding="utf-8") as f:
        src = f.read()
    # write_failure_context should not be preceded by pop last_error in same block — weak check:
    assert "write_failure_context" in src
    # Ensure pop only appears in gate-pass contexts (four known variable names).
    assert src.count('pop("last_error_code", None)') == 4
