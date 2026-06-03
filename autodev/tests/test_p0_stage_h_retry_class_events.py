"""P0 Stage H — pipeline event ``retry_class`` field.

Stage H adds a new ``retry_class`` field to pipeline events emitted at
``gate_fail`` (executor + reviewer) and ``attempt_end`` (planner +
executor + reviewer) sites. Value enum:

* ``"initial_attempt"`` — phase's first attempt
* ``"executor_self_failure"`` — this attempt was triggered by a previous
  executor self-failure (``reset_execution('auto')``)
* ``"reviewer_rejection"`` — this attempt was triggered by a previous
  reviewer ROUTE_EXECUTOR rejection

The orchestrator tracks the current attempt's class in
``self._current_attempt_retry_class``, set at attempt-start time
(``reset_phase`` sets it to ``"initial_attempt"``;
``reset_execution('auto')`` sets it to ``"executor_self_failure"``; the
ROUTE_EXECUTOR handler sets it to ``"reviewer_rejection"``).

Shape decision: ``retry_class`` is ALWAYS present on the affected
events, with ``None`` when not applicable (e.g., reviewer ``gate_fail``
for non-ROUTE_EXECUTOR verdicts). This keeps the JSONL schema stable so
the UI's ``humanizeSummary`` can do ``typeof d.retry_class === 'string'``
without branching on absence.

Pattern: a mix of source-text checks (emit sites' detail dicts) and
runtime checks on the tracker initialisation.
"""

import os
import pathlib
import re
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402

_ORCH_SRC = pathlib.Path(PIPELINE_DIR, "orchestrator.py").read_text()


# ---------------------------------------------------------------------------
# Runtime: tracker initialises to "initial_attempt"
# ---------------------------------------------------------------------------


def test_current_attempt_retry_class_initialises_to_initial_attempt(
    monkeypatch, tmp_path
):
    """A freshly constructed Orchestrator must have
    ``self._current_attempt_retry_class == "initial_attempt"``."""
    import json as _json

    # _validate_openclaw_root requires workspace-{role} subdirs + openclaw.json.
    for role in ("planner", "executor", "reviewer"):
        (tmp_path / f"workspace-{role}").mkdir()
    fake_config = tmp_path / "openclaw.json"
    fake_config.write_text(_json.dumps({
        "hooks": {"token": "t"},
        "gateway": {"port": 18789, "auth": {"token": "g"}},
    }))
    monkeypatch.setattr(orch_mod, "CONFIG_FILE", str(fake_config))
    monkeypatch.setattr(orch_mod, "OPENCLAW_ROOT", str(tmp_path))

    orch = orch_mod.Orchestrator()
    assert getattr(orch, "_current_attempt_retry_class", None) == "initial_attempt", (
        "Orchestrator must seed self._current_attempt_retry_class = "
        "'initial_attempt' in __init__. Without this, the first attempt's "
        "events emit retry_class=None, breaking the UI's clean-common-case "
        "rendering."
    )


def test_reset_phase_resets_retry_class_tracker(tmp_path, monkeypatch):
    """``reset_phase()`` is the canonical boundary at which a new phase
    starts — the tracker must reset to ``"initial_attempt"`` here so a
    phase that follows a retried-and-completed prior phase does not
    inherit the prior phase's retry class."""
    import json as _json

    (tmp_path / "phase_state.json").write_text(_json.dumps({
        "executor_retries": 2,
        "executor_self_failure_retries": 2,
        "executor_reviewer_rejection_retries": 1,
        "reviewer_retries": 1,
    }))
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json"))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {
        "current_phase": 1,
        "current_phase_raw_id": "CORE-E1",
        "current_agent": "executor",
        "executor_retries": 2,
        "reviewer_retries": 1,
        "planner_retries": 0,
        "pipeline_status": "RUNNING",
        "project_path": str(tmp_path),
    }
    orch.openclaw_config = {}
    orch.lock_fd = None
    # Pre-existing tracker state from a prior phase's reset_execution('auto').
    orch._current_attempt_retry_class = "executor_self_failure"

    import subprocess as _sp

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(_sp, "run", lambda *a, **k: _R())

    orch.reset_phase()

    assert orch._current_attempt_retry_class == "initial_attempt", (
        "reset_phase must reset _current_attempt_retry_class to "
        "'initial_attempt'. Carrying over a prior phase's tracker value "
        "would mis-label the first attempt of the new phase."
    )


# ---------------------------------------------------------------------------
# Source-text: executor gate_fail emit includes retry_class
# ---------------------------------------------------------------------------


def test_executor_gate_fail_emit_includes_retry_class():
    """The executor ``gate_fail`` emit must carry the retry_class field.
    Site: orchestrator.py around line 4253–4260 (inside the
    ``elif outcome == "executor_succeeded"`` block's else-branch)."""
    # Find the executor gate_fail emit block.
    # Marker: the comment-tag "W1-F" appears next to all gate_fail emits.
    # Slice the executor-agent branch (between "elif current_agent == "executor""
    # and "elif current_agent == "reviewer"") to scope the search.
    exec_start = _ORCH_SRC.find('elif current_agent == "executor"')
    rev_start = _ORCH_SRC.find('elif current_agent == "reviewer"', exec_start)
    assert exec_start != -1 and rev_start != -1, (
        "Could not locate executor/reviewer agent branches in main loop"
    )
    exec_block = _ORCH_SRC[exec_start:rev_start]
    # Within that block, look for the gate_fail emit detail dict.
    gate_fail_idx = exec_block.find('"gate_fail"')
    assert gate_fail_idx != -1, (
        "Executor branch must contain a gate_fail emit"
    )
    # Slice ~1500 chars around it — wide enough to span a small dict
    # literal with explanatory comments alongside each field.
    emit_window = exec_block[gate_fail_idx : gate_fail_idx + 1500]
    assert "retry_class" in emit_window, (
        "Executor gate_fail emit must include retry_class in its detail "
        "dict (sourced from self._current_attempt_retry_class). Without "
        "it, the UI cannot label whether this gate failure is from an "
        "initial attempt or a retry."
    )


def test_executor_gate_fail_retry_class_sourced_from_tracker():
    """The executor gate_fail emit must source retry_class from the
    self._current_attempt_retry_class tracker, not a hardcoded value."""
    exec_start = _ORCH_SRC.find('elif current_agent == "executor"')
    rev_start = _ORCH_SRC.find('elif current_agent == "reviewer"', exec_start)
    exec_block = _ORCH_SRC[exec_start:rev_start]
    gate_fail_idx = exec_block.find('"gate_fail"')
    emit_window = exec_block[gate_fail_idx : gate_fail_idx + 1500]
    # Look for the field being assigned from the tracker — accept either
    # the bare attribute reference or via self.
    has_tracker_source = (
        "_current_attempt_retry_class" in emit_window
    )
    assert has_tracker_source, (
        "Executor gate_fail emit must source retry_class from "
        "self._current_attempt_retry_class so the value reflects what "
        "actually kicked off the current attempt."
    )


# ---------------------------------------------------------------------------
# Source-text: reviewer gate_fail emit conditionally includes retry_class
# ---------------------------------------------------------------------------


def test_reviewer_gate_fail_emit_includes_retry_class_key():
    """The reviewer ``gate_fail`` emit must include the retry_class key
    in its detail dict (value is ``"reviewer_rejection"`` when
    ``gate_result == "ROUTE_EXECUTOR"``, ``None`` otherwise — chosen
    shape for schema stability)."""
    rev_start = _ORCH_SRC.find('elif current_agent == "reviewer"')
    # Slice forward 30000 chars — reviewer branch is large (CONTRACT_FAILURE
    # and unverified handling).
    rev_block = _ORCH_SRC[rev_start : rev_start + 30000]
    gate_fail_idx = rev_block.find('"gate_fail"')
    assert gate_fail_idx != -1, "Reviewer branch must contain a gate_fail emit"
    emit_window = rev_block[gate_fail_idx : gate_fail_idx + 1500]
    assert "retry_class" in emit_window, (
        "Reviewer gate_fail emit must include retry_class in its detail "
        "dict. Use None for non-ROUTE_EXECUTOR verdicts and "
        "'reviewer_rejection' for ROUTE_EXECUTOR — this is the shape the "
        "UI's typeof-string check is designed for."
    )


def test_reviewer_gate_fail_retry_class_conditional_on_route_executor():
    """Source-text guard: the value used for retry_class on the reviewer
    gate_fail emit must be conditioned on the ROUTE_EXECUTOR verdict
    (the only reviewer outcome that triggers an executor retry)."""
    rev_start = _ORCH_SRC.find('elif current_agent == "reviewer"')
    rev_block = _ORCH_SRC[rev_start : rev_start + 30000]
    gate_fail_idx = rev_block.find('"gate_fail"')
    # Look 800 chars before + 800 after for the conditional value assignment.
    emit_window = rev_block[max(0, gate_fail_idx - 800) : gate_fail_idx + 800]
    # We want to see a conditional that ties retry_class to ROUTE_EXECUTOR.
    has_condition = (
        "ROUTE_EXECUTOR" in emit_window
        and "reviewer_rejection" in emit_window
    )
    assert has_condition, (
        "Reviewer gate_fail emit must condition retry_class on the "
        "gate_result. Expected to see both 'ROUTE_EXECUTOR' and "
        "'reviewer_rejection' within ~800 chars of the gate_fail emit so "
        "the conditional value assignment is co-located with the emit."
    )


# ---------------------------------------------------------------------------
# Source-text: all three attempt_end emit sites include retry_class
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
def test_attempt_end_emit_includes_retry_class(agent):
    """Each of the three ``attempt_end`` emit sites must include
    retry_class in its detail dict, sourced from
    self._current_attempt_retry_class."""
    # Each emit is shaped like:
    #     _write_pipeline_event(
    #         "attempt_end", raw_id, "<agent>",
    #         {
    #             ...
    #             "retry_class": self._current_attempt_retry_class,
    #         },
    #     )
    # Find the emit for this agent.
    marker = f'"attempt_end", raw_id, "{agent}"'
    idx = _ORCH_SRC.find(marker)
    assert idx != -1, (
        f"attempt_end emit for {agent} missing from orchestrator source"
    )
    # 1500-char window — wide enough to capture a small dict literal
    # with explanatory comments alongside each field.
    window = _ORCH_SRC[idx : idx + 1500]
    assert "retry_class" in window, (
        f"{agent} attempt_end emit must include retry_class in its detail "
        "dict. Without it, the UI cannot distinguish initial / "
        "self-failure / rejection retries in the activity feed."
    )
    assert "_current_attempt_retry_class" in window, (
        f"{agent} attempt_end emit's retry_class must be sourced from "
        "self._current_attempt_retry_class (the orchestrator-private "
        "tracker set at attempt-start time)."
    )
