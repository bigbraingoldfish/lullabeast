"""P0 Stage H — apply_reviewer_routing pass-2 "any-plan" pivot.

(Folded-in Stage G callout #1.)

The reviewer gate's ``apply_reviewer_routing`` pass-2 routing previously
pivoted on ``blocking_issues[0].attribution`` — only the first issue's
attribution influenced the route. Today's gate synthesis path always
emits ``attribution: "impl"`` so the legacy logic is correct in
practice, but the pivot is ordering-sensitive: a future reviewer variant
emitting mixed attributions (one ``"plan"`` issue alongside several
``"impl"`` issues, in any order) would route incorrectly when the plan
issue is not at index 0.

Tighten to "any-plan" semantics: if ANY blocking_issue carries
``attribution: "plan"``, route to planner. Otherwise route to executor.
Uses more of the information the reviewer already writes; does NOT add
new routing power; does NOT touch the orchestrator's separate
``run_blame_attribution()`` AI-driven attribution system.

Pattern: direct unit tests on the pure function with mocked phase_state.
"""

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
GATE_SCRIPTS_DIR = os.path.join(PIPELINE_DIR, "gate_scripts")
for _p in (GATE_SCRIPTS_DIR, PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import reviewer_gate as reviewer_gate_module  # noqa: E402


def _set_phase_state(monkeypatch, tmp_path, reviewer_retries):
    """Stub PHASE_STATE_FILE so apply_reviewer_routing reads our value."""
    ps_path = tmp_path / "phase_state.json"
    ps_path.write_text(json.dumps({"reviewer_retries": reviewer_retries}))
    monkeypatch.setattr(reviewer_gate_module, "PHASE_STATE_FILE", str(ps_path))


# ---------------------------------------------------------------------------
# Regression guards: existing behaviour preserved
# ---------------------------------------------------------------------------


def test_pass2_routes_to_planner_when_first_issue_is_plan(monkeypatch, tmp_path):
    """Regression: a single plan-attributed issue at index 0 must still
    route to planner."""
    _set_phase_state(monkeypatch, tmp_path, reviewer_retries=1)
    data = {"blocking_issues": [{"attribution": "plan", "description": "x"}]}
    assert reviewer_gate_module.apply_reviewer_routing(data) == "ROUTE_PLANNER"


def test_pass2_routes_to_executor_when_all_issues_are_impl(monkeypatch, tmp_path):
    """Regression: when every issue is impl, route to executor."""
    _set_phase_state(monkeypatch, tmp_path, reviewer_retries=1)
    data = {"blocking_issues": [
        {"attribution": "impl"}, {"attribution": "impl"}, {"attribution": "impl"},
    ]}
    assert reviewer_gate_module.apply_reviewer_routing(data) == "ROUTE_EXECUTOR"


# ---------------------------------------------------------------------------
# The new behaviour: any-plan semantics
# ---------------------------------------------------------------------------


def test_pass2_routes_to_planner_when_any_issue_is_plan_even_if_first_is_impl(
    monkeypatch, tmp_path
):
    """NEW BEHAVIOUR: a plan issue at index 1 or 2 must still route to
    planner. The legacy code only inspected ``[0].attribution`` and would
    route to executor here — incorrectly, because a plan issue still
    needs the planner to fix it.

    This test FAILS against pre-Stage-H code; passing requires the
    "any-plan" tightening."""
    _set_phase_state(monkeypatch, tmp_path, reviewer_retries=1)
    data = {"blocking_issues": [
        {"attribution": "impl", "description": "test fails"},
        {"attribution": "plan", "description": "spec ambiguous"},
        {"attribution": "impl", "description": "lint warning"},
    ]}
    assert reviewer_gate_module.apply_reviewer_routing(data) == "ROUTE_PLANNER", (
        "Pass-2 routing must use any-plan semantics: if any blocking_issue "
        "carries attribution='plan', route to planner regardless of "
        "position. The legacy [0]-only pivot was ordering-sensitive — a "
        "valid plan issue at index 1+ would be silently misrouted."
    )


def test_pass2_routes_to_executor_when_no_issues_have_plan(monkeypatch, tmp_path):
    """If no issue has plan attribution (only impl or behavioral or
    free), route to executor. Mix of impl + behavioral confirms that
    only the literal value ``"plan"`` triggers ROUTE_PLANNER."""
    _set_phase_state(monkeypatch, tmp_path, reviewer_retries=1)
    data = {"blocking_issues": [
        {"attribution": "impl"},
        {"attribution": "behavioral"},
    ]}
    assert reviewer_gate_module.apply_reviewer_routing(data) == "ROUTE_EXECUTOR"


def test_pass2_safe_against_non_dict_issue_entries(monkeypatch, tmp_path):
    """The legacy ``test_route_executor_writes_failure_context_atomically``
    fixture passes string-shaped blocking_issues. Stage H's defensive
    ``(bi or {}).get(...)`` coalesce must keep the pivot from crashing
    on such input, while still detecting plan in dict entries."""
    _set_phase_state(monkeypatch, tmp_path, reviewer_retries=1)
    data = {"blocking_issues": [
        "this is a string, not a dict",
        {"attribution": "plan"},
    ]}
    # Must not raise. Must detect the plan entry.
    assert reviewer_gate_module.apply_reviewer_routing(data) == "ROUTE_PLANNER", (
        "any-plan pivot must defensively handle non-dict entries via "
        "(bi or {}).get(...). Without the coalesce, mixed-shape arrays "
        "would raise AttributeError mid-pivot, halting the gate."
    )


# ---------------------------------------------------------------------------
# Edge cases: empty list, no issues key, none data
# ---------------------------------------------------------------------------


def test_pass2_no_issues_falls_back_to_executor(monkeypatch, tmp_path):
    """Regression: empty list falls through to ROUTE_EXECUTOR."""
    _set_phase_state(monkeypatch, tmp_path, reviewer_retries=1)
    data = {"blocking_issues": []}
    assert reviewer_gate_module.apply_reviewer_routing(data) == "ROUTE_EXECUTOR"


def test_pass2_missing_issues_key_falls_back_to_executor(monkeypatch, tmp_path):
    """Regression: data without ``blocking_issues`` falls through to
    ROUTE_EXECUTOR. Matches existing fallback semantics."""
    _set_phase_state(monkeypatch, tmp_path, reviewer_retries=1)
    assert reviewer_gate_module.apply_reviewer_routing({}) == "ROUTE_EXECUTOR"


def test_pass2_none_data_falls_back_to_executor(monkeypatch, tmp_path):
    """Regression: ``None`` data falls through to ROUTE_EXECUTOR."""
    _set_phase_state(monkeypatch, tmp_path, reviewer_retries=1)
    assert reviewer_gate_module.apply_reviewer_routing(None) == "ROUTE_EXECUTOR"


# ---------------------------------------------------------------------------
# Cross-pass guards: pass-1 and pass-3 behaviour unchanged
# ---------------------------------------------------------------------------


def test_pass1_always_routes_to_executor_unchanged(monkeypatch, tmp_path):
    """Pass-1 must always route to executor regardless of attribution —
    the first rejection always goes to the executor for self-heal."""
    _set_phase_state(monkeypatch, tmp_path, reviewer_retries=0)
    data = {"blocking_issues": [{"attribution": "plan"}]}
    assert reviewer_gate_module.apply_reviewer_routing(data) == "ROUTE_EXECUTOR"


def test_pass3_always_routes_to_escalate_unchanged(monkeypatch, tmp_path):
    """Pass-3 must always route to escalation — two rejections already
    consumed; no further routing decisions to make."""
    _set_phase_state(monkeypatch, tmp_path, reviewer_retries=2)
    data = {"blocking_issues": [{"attribution": "plan"}]}
    assert reviewer_gate_module.apply_reviewer_routing(data) == "ROUTE_ESCALATE"
