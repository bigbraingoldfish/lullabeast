"""Issue 1 — RETRY resumes the in-flight agent via the ``resume_target_agent`` stash.

The RETRY handler previously reconstructed ``current_agent`` by string-matching
``last_action`` — but the STOPPED transition overwrites ``last_action`` to
"Stop sentinel consumed …", so the match always fell through to the planner.
Result: a resume after an operator stop restarted the phase from the planner,
re-doing completed executor work. ``/api/resume-ready`` now stashes the pre-stop
``current_agent`` into ``resume_target_agent``; ``_restore_resume_target_agent()``
consumes it on RETRY.

These tests target the pure state helper directly (``Orchestrator.__new__`` — the
established pattern in this suite — so the heavy constructor is bypassed).
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402


def _bare_orch(state):
    o = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    o.state = state
    return o


def test_restore_uses_stashed_target():
    """A stashed ``resume_target_agent`` is applied to ``current_agent`` and
    consumed (popped) so it cannot leak into a later resume. This is the core
    fix: stop after the executor passed (current_agent="reviewer") → resume
    continues to the reviewer, not the planner."""
    o = _bare_orch({"resume_target_agent": "reviewer", "current_agent": "escalation"})
    o._restore_resume_target_agent()
    assert o.state["current_agent"] == "reviewer"
    assert "resume_target_agent" not in o.state


def test_restore_defaults_to_planner_when_absent():
    """No stash present → safe default of "planner". Catches a missing-stash
    crash or a wrong default."""
    o = _bare_orch({"current_agent": "escalation"})
    o._restore_resume_target_agent()
    assert o.state["current_agent"] == "planner"


def test_restore_rejects_invalid_target():
    """An out-of-range stash value is ignored (defaults to "planner") and still
    cleared. Catches trusting an unvalidated agent string."""
    o = _bare_orch({"resume_target_agent": "bogus", "current_agent": "escalation"})
    o._restore_resume_target_agent()
    assert o.state["current_agent"] == "planner"
    assert "resume_target_agent" not in o.state
