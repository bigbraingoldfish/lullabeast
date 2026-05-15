"""Section 6.4 — phase-state outcome fields.

Persist the last poll outcome, abort result, and attempt summary into
``phase_state.json`` so a restarted orchestrator (or the dashboard,
which already reads ``phase_state.json``) can render "what happened
last" without scraping ``/tmp/orchestrator.log``.

Three fields, all optional and additive:

* ``last_poll_reason`` — succeeded / stalled / no_first_activity /
  stopped / timeout, written after every ``poll_for_sentinel`` return.
* ``last_abort_result`` — ok / FAILED / verify_failed, written by
  ``_handle_stall_outcome``.
* ``last_attempt_summary`` — dense one-line ``phase=… agent=… attempt=…
  reason=…`` string, written at each ``[ATTEMPT_END]``.

The writer is a small helper ``_record_phase_outcome(**fields)`` that
reads existing phase_state, merges the supplied fields, and writes
back atomically.  This keeps the call-site impact minimal.
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


@pytest.fixture
def bare_orch(tmp_path, monkeypatch):
    """Bare Orchestrator with phase_state.json in tmp_path."""
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json")
    )
    (tmp_path / "phase_state.json").write_text(
        json.dumps({"reviewer_retries": 1, "executor_retries": 2})
    )
    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {"current_phase_raw_id": "CORE-E5"}
    orch.openclaw_config = {}
    orch.lock_fd = None
    return orch, tmp_path


# ---------------------------------------------------------------------------
# F1 — helper merges fields without wiping existing state
# ---------------------------------------------------------------------------


def test_record_phase_outcome_helper_exists(bare_orch):
    """``_record_phase_outcome(**fields)`` must exist as a method on
    Orchestrator so the three call sites can share a single, tested
    implementation."""
    orch, _ = bare_orch
    assert callable(getattr(orch, "_record_phase_outcome", None)), (
        "Orchestrator must define _record_phase_outcome(**fields) for "
        "Section 6.4 outcome-field persistence"
    )


def test_record_phase_outcome_writes_supplied_fields(bare_orch):
    """Calling the helper with arbitrary field=value pairs must merge
    them into phase_state.json without disturbing existing fields."""
    orch, tmp_path = bare_orch
    orch._record_phase_outcome(
        last_poll_reason="stalled",
        last_abort_result="ok",
        last_attempt_summary="phase=CORE-E5 agent=executor attempt=2 reason=stalled",
    )
    ps = json.loads((tmp_path / "phase_state.json").read_text())
    # New fields present.
    assert ps.get("last_poll_reason") == "stalled"
    assert ps.get("last_abort_result") == "ok"
    assert ps.get("last_attempt_summary").startswith("phase=CORE-E5")
    # Existing fields preserved.
    assert ps.get("reviewer_retries") == 1
    assert ps.get("executor_retries") == 2


def test_record_phase_outcome_overwrites_prior_values(bare_orch):
    """Repeated calls must update the fields (last-write-wins), not
    accumulate or skip."""
    orch, tmp_path = bare_orch
    orch._record_phase_outcome(last_poll_reason="stalled")
    orch._record_phase_outcome(last_poll_reason="succeeded")
    ps = json.loads((tmp_path / "phase_state.json").read_text())
    assert ps.get("last_poll_reason") == "succeeded"


def test_record_phase_outcome_ignores_unset_fields(bare_orch):
    """Calling with only one field must not wipe the others.  This is
    the merge contract the call sites rely on."""
    orch, tmp_path = bare_orch
    orch._record_phase_outcome(last_abort_result="ok")
    orch._record_phase_outcome(last_poll_reason="stalled")
    ps = json.loads((tmp_path / "phase_state.json").read_text())
    assert ps.get("last_abort_result") == "ok"
    assert ps.get("last_poll_reason") == "stalled"


# ---------------------------------------------------------------------------
# F2 — helper called from _handle_stall_outcome
# ---------------------------------------------------------------------------


def test_handle_stall_outcome_records_abort_result_in_phase_state():
    """``_handle_stall_outcome`` must invoke ``_record_phase_outcome``
    with ``last_abort_result=...`` so the dashboard can see the abort
    outcome without re-parsing the orchestrator log."""
    method_idx = _ORCH_SRC.find("def _handle_stall_outcome")
    assert method_idx != -1
    next_def = _ORCH_SRC.find("\n    def ", method_idx + 1)
    method_body = _ORCH_SRC[method_idx : next_def if next_def != -1 else method_idx + 5000]
    assert "_record_phase_outcome" in method_body, (
        "_handle_stall_outcome must call _record_phase_outcome to persist "
        "last_abort_result for dashboard visibility"
    )
    assert "last_abort_result" in method_body, (
        "_handle_stall_outcome must record last_abort_result"
    )


# ---------------------------------------------------------------------------
# F3 — helper called from each poll site for last_poll_reason + summary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
def test_poll_site_records_last_poll_reason(agent):
    """Each poll site must call ``_record_phase_outcome`` with
    ``last_poll_reason`` after the poll returns."""
    marker = f"stall_detection_path=_{agent}_stamp"
    idx = _ORCH_SRC.find(marker)
    assert idx != -1
    window = _ORCH_SRC[max(0, idx - 1500) : idx + 2500]
    assert "_record_phase_outcome" in window, (
        f"{agent} poll site must invoke _record_phase_outcome after the poll"
    )
    assert "last_poll_reason" in window, (
        f"{agent} poll site must record last_poll_reason"
    )


@pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
def test_poll_site_records_last_attempt_summary(agent):
    """Each poll site must also record ``last_attempt_summary`` so the
    dashboard can show the dense one-liner without log access."""
    marker = f"stall_detection_path=_{agent}_stamp"
    idx = _ORCH_SRC.find(marker)
    assert idx != -1
    window = _ORCH_SRC[max(0, idx - 1500) : idx + 2500]
    assert "last_attempt_summary" in window, (
        f"{agent} poll site must record last_attempt_summary"
    )
