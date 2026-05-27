"""P0 Stage H — ROUTE_EXECUTOR handler increments the rejection counter.

The reviewer-rejection retry path does NOT call ``reset_execution()``; it
runs inline inside the orchestrator main loop's ``elif gate_result ==
"ROUTE_EXECUTOR":`` branch. Stage H wires the new
``executor_reviewer_rejection_retries`` counter (and the
``_current_attempt_retry_class`` tracker) into that branch.

Both source-text and source-structure checks here. The branch lives deep
in the main loop where direct runtime drive would require half the
pipeline to be stubbed out; source-text pins the increment + the tracker
write are present, while the routing-pivot tests
(``test_p0_stage_h_routing_pivot_any_plan.py``) drive the gate logic
itself.
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

_ORCH_SRC = pathlib.Path(PIPELINE_DIR, "orchestrator.py").read_text()


def _route_executor_branch_body() -> str:
    """Slice the ROUTE_EXECUTOR handler body out of orchestrator source."""
    idx = _ORCH_SRC.find('elif gate_result == "ROUTE_EXECUTOR"')
    assert idx != -1, "ROUTE_EXECUTOR handler missing from orchestrator source"
    next_elif = _ORCH_SRC.find("elif gate_result ==", idx + 10)
    end = next_elif if next_elif != -1 else idx + 3000
    return _ORCH_SRC[idx:end]


def test_route_executor_handler_increments_rejection_counter():
    """The ROUTE_EXECUTOR branch must contain an increment of
    ``executor_reviewer_rejection_retries``. Without it the metrics row
    invariant breaks: ``executor_attempts`` will be smaller than the
    actual number of executor attempts that ran in the phase."""
    branch = _route_executor_branch_body()
    # Match either dict-increment (foo["key"] = foo.get("key", 0) + 1)
    # or augmented assignment shapes (rare in this codebase but defensive).
    # DOTALL so the increment expression can span lines (multi-line
    # paren-wrapped expressions are idiomatic here).
    pat = re.compile(
        r'executor_reviewer_rejection_retries["\']?\s*\]?\s*=\s*[^=].*?\+\s*1'
        r'|executor_reviewer_rejection_retries["\']?\s*\]?\s*\+=\s*1',
        re.DOTALL,
    )
    assert pat.search(branch), (
        "ROUTE_EXECUTOR handler must increment "
        "executor_reviewer_rejection_retries. Suggested shape:\n"
        '    _ps_re["executor_reviewer_rejection_retries"] = '
        '_ps_re.get("executor_reviewer_rejection_retries", 0) + 1\n'
        "Without this, the lifetime rejection count never advances and the "
        "metrics-row invariant "
        "executor_attempts == self_failures + rejections + 1 fails."
    )


def test_route_executor_handler_sets_retry_class_to_reviewer_rejection():
    """The branch must set the process-local tracker so subsequent
    attempt_end and gate_fail events carry retry_class='reviewer_rejection'."""
    branch = _route_executor_branch_body()
    pat = re.compile(
        r'_current_attempt_retry_class\s*=\s*["\']reviewer_rejection["\']'
    )
    assert pat.search(branch), (
        "ROUTE_EXECUTOR handler must set "
        "self._current_attempt_retry_class = 'reviewer_rejection' so the "
        "next executor attempt's events label the retry source correctly. "
        "Without this, the UI activity feed mis-labels rejection retries "
        "as self-failures."
    )


def test_route_executor_handler_does_not_touch_self_failure_counter():
    """Guard against accidental double-increment of the wrong counter."""
    branch = _route_executor_branch_body()
    # The branch should contain the rejection counter but NOT a literal
    # ``executor_self_failure_retries +=`` or ``= ... + 1`` for the
    # self-failure counter. Reading the counter (e.g. to log it) is fine.
    bad_pat = re.compile(
        r'executor_self_failure_retries["\']?\s*\]?\s*=\s*[^=].*?\+\s*1'
        r'|executor_self_failure_retries["\']?\s*\]?\s*\+=\s*1'
    )
    assert not bad_pat.search(branch), (
        "ROUTE_EXECUTOR handler must NOT increment "
        "executor_self_failure_retries. The rejection path is by "
        "definition not a self-failure. Cross-bumping would corrupt the "
        "metrics invariant."
    )


def test_route_executor_handler_preserves_legacy_executor_retries_reset():
    """Regression guard: the line that resets ``executor_retries = 0`` (the
    per-segment budget) must stay. Without it the executor inherits its
    prior failure count and the escalation cap fires immediately on the
    next failure."""
    branch = _route_executor_branch_body()
    # Look for the line that zeros executor_retries.
    has_reset = (
        'self.state["executor_retries"] = 0' in branch
        or "self.state['executor_retries'] = 0" in branch
    )
    assert has_reset, (
        "ROUTE_EXECUTOR handler must continue to reset "
        "self.state['executor_retries'] = 0 so the executor gets a fresh "
        "per-segment retry budget after a reviewer rejection. Removing "
        "this line was the F6-class behaviour where the cap fired on the "
        "first post-rejection failure."
    )


def test_route_executor_handler_writes_phase_state_atomically():
    """The branch must call ``write_phase_state_atomic`` so the rejection
    counter increment lands on disk via mkstemp + os.replace, matching
    every other phase_state mutation in this codebase."""
    branch = _route_executor_branch_body()
    assert "write_phase_state_atomic" in branch, (
        "ROUTE_EXECUTOR handler must call write_phase_state_atomic to "
        "persist the rejection counter increment. Non-atomic writes are "
        "the source of the original reviewer-rejected-but-lost bug class."
    )
