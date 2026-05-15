"""Section 7b — ``reset_reviewer`` must sync ``reviewer_retries`` to
``self.state`` so the UI sees the cleared counter.

Live regression observed on UI-E1: the operator triggered
``RESET_REVIEWER`` from the dashboard, the orchestrator wrote
``phase_state.json["reviewer_retries"] = 0``, but the dashboard kept
showing **3/3 reviewer attempts (×××)** for 30+ hours.

Root cause: ``reset_reviewer`` at ``orchestrator.py:2401`` zeros
``phase_state["reviewer_retries"]`` but never updates
``self.state["reviewer_retries"]``.  When the orchestrator subsequently
calls ``transition_state`` it writes ``self.state`` (including the stale
3) to ``pipeline_state.json``.  The UI reads
``pipeline_state.json["reviewer_retries"]`` at ``ui/server.py:6474`` for
the agent-attempts panel — so it shows the stale value.

Section 5c fixed the identical bug in ``reset_execution`` (added
``self.state["reviewer_retries"] = 0`` alongside the phase_state write).
``reset_reviewer`` was missed and the asymmetry has been silently
producing wrong-attempt-count UI for every RESET_REVIEWER since.

These tests pin both sides:

* ``reset_reviewer`` must update ``self.state["reviewer_retries"] = 0``.
* After ``reset_reviewer`` returns, both ``phase_state.json`` and
  ``pipeline_state.json`` agree on ``reviewer_retries == 0``.
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


_ORCH_SRC = open(
    os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8"
).read()


# ---------------------------------------------------------------------------
# Source-level pin
# ---------------------------------------------------------------------------


def test_reset_reviewer_zeros_self_state_reviewer_retries():
    """The ``reset_reviewer`` method body must contain a
    ``self.state["reviewer_retries"] = 0`` assignment so the in-memory
    state matches the on-disk ``phase_state.json`` zero.  Without this,
    ``transition_state`` later writes the stale value to
    ``pipeline_state.json`` and the UI shows the wrong count.
    """
    idx = _ORCH_SRC.find("def reset_reviewer")
    assert idx != -1, "Could not locate reset_reviewer"
    next_def = _ORCH_SRC.find("\n    def ", idx + 1)
    body = _ORCH_SRC[idx : next_def if next_def != -1 else idx + 3000]
    assert 'self.state["reviewer_retries"] = 0' in body or (
        "self.state['reviewer_retries'] = 0" in body
    ), (
        "reset_reviewer must zero self.state['reviewer_retries'] to keep "
        "pipeline_state.json in sync with phase_state.json (the UI reads "
        "pipeline_state.json — Section 7b live regression on UI-E1)"
    )


# ---------------------------------------------------------------------------
# Behavioural pin
# ---------------------------------------------------------------------------


@pytest.fixture
def reviewer_workspace(tmp_path, monkeypatch):
    """Bare Orchestrator with phase_state, pipeline_state, and the
    project artifacts dir stubbed in tmp_path.  Pre-populates the
    "3 reviewer rejections, escalation triggered" live state from
    UI-E1 so the test reproduces the regression conditions exactly.
    """
    project_dir = tmp_path / "project"
    project_artifacts = project_dir / ".autodev" / "pipeline"
    project_artifacts.mkdir(parents=True)
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(project_artifacts))
    monkeypatch.setattr(
        orch_mod, "PHASE_STATE_FILE", str(project_artifacts / "phase_state.json")
    )
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(project_dir))
    monkeypatch.setattr(
        orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json")
    )

    (project_artifacts / "phase_state.json").write_text(
        json.dumps(
            {
                "reviewer_retries": 3,
                "executor_retries": 0,
                "planner_retries": 0,
                "reviewer_rejected": True,
                "executor_succeeded": True,
                "escalation_resets": 0,
                "last_error_code": "ERR_VALIDATION_FAILED",
            }
        )
    )

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {
        "current_phase": 16,
        "current_phase_raw_id": "UI-E1",
        "current_agent": "escalation",
        "planner_retries": 0,
        "executor_retries": 0,
        "reviewer_retries": 3,            # the stale value the UI sees
        "project_path": str(project_dir),
        "status": "RUNNING",
        "pipeline_status": "WAITING_FOR_HUMAN",
    }
    orch.openclaw_config = {}
    orch.lock_fd = None
    return orch, tmp_path, project_artifacts


def test_reset_reviewer_syncs_pipeline_state_with_phase_state(
    reviewer_workspace,
):
    """After ``reset_reviewer()`` returns, both state files must show
    ``reviewer_retries == 0`` — the contract the UI relies on."""
    orch, tmp_path, project_artifacts = reviewer_workspace

    orch.reset_reviewer()

    # Disk-level invariant — both files must agree.
    ps = json.loads((project_artifacts / "phase_state.json").read_text())
    pipeline_state = json.loads((tmp_path / "pipeline_state.json").read_text())

    assert ps.get("reviewer_retries") == 0, (
        f"phase_state.reviewer_retries must be 0 after reset_reviewer; "
        f"got {ps.get('reviewer_retries')}"
    )
    assert pipeline_state.get("reviewer_retries") == 0, (
        f"pipeline_state.reviewer_retries must be 0 after reset_reviewer "
        f"(the UI reads this field at ui/server.py:6474 to render the "
        f"agent-attempts panel); got "
        f"{pipeline_state.get('reviewer_retries')}.  Live regression on "
        f"UI-E1 left this at 3 for 30+ hours."
    )
    # In-memory invariant — for fast UI calls that hit self.state.
    assert orch.state.get("reviewer_retries") == 0, (
        f"self.state['reviewer_retries'] must be 0 after reset_reviewer; "
        f"got {orch.state.get('reviewer_retries')}"
    )
