"""P1 Stage D — reviewer-gate regression check on prior phase.

When ``current_phase.json`` carries both ``prior_phase_raw_id`` and
``prior_phase_how_to_check`` (resolver-populated when the most recent
completed phase had a behavioural recipe), the reviewer must execute that
recipe alongside the current phase's and report the result as a structured
``regression_verification`` block.

Two error codes land on this dimension:

- ``ERR_REGRESSION_UNVERIFIED`` (shape failure, non-retry-consuming on the
  pooled ``reviewer_unverified_retries`` counter — orchestrator-side).
- ``ERR_REGRESSION_PRIOR_PHASE`` (content failure: verdict ∈ fail /
  cannot_verify, OR ``prior_phase_how_to_check_followed`` is False).

The synthesiser writes ONE blocking_issue per regression failure (regression
is one logical failure, not one-per-evidence — distinct from the behavioural
synthesiser). Idempotency keys on ``criterion_source == "regression_prior_phase"``
so the regression synthesiser coexists with the behavioural synthesiser when
both flavours fail with an empty initial blocking_issues list.

Mirror fixtures and structure of
``test_reviewer_gate_behavioral_verification.py``.
"""

import json
import os
import sys
from contextlib import ExitStack
from unittest.mock import patch

import pytest

# Path wiring handled by autodev/tests/conftest.py
import utils as utils_module
import reviewer_gate as reviewer_gate_module


# ---------------------------------------------------------------------------
# Fixtures — mirror of behavioural test fixtures, extended with prior-phase
# ---------------------------------------------------------------------------


def _patch_workspace(tmp_dir):
    """Return an ExitStack that redirects gate workspace paths to tmp_dir."""
    stack = ExitStack()
    tmp_dir = tmp_dir.rstrip(os.sep) + os.sep
    stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", tmp_dir))
    stack.enter_context(patch.object(utils_module, "ARTIFACTS_DIR", tmp_dir))
    ps = os.path.join(tmp_dir.rstrip(os.sep), "phase_state.json")
    stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", ps))
    stack.enter_context(patch.object(reviewer_gate_module, "WORKSPACE_DIR", tmp_dir))
    stack.enter_context(patch.object(reviewer_gate_module, "ARTIFACTS_DIR", tmp_dir))
    stack.enter_context(patch.object(reviewer_gate_module, "PHASE_STATE_FILE", ps))
    return stack


def _write_current_phase_with_prior(
    workspace,
    raw_id="CORE-E2",
    prior_raw_id="CORE-E1",
    prior_how_to_check="Navigate to /tasks; expect at least one row rendered.",
    behavioral_block=None,
):
    """Write current_phase.json with both behavioural and prior-phase fields
    populated. Defaults exercise the regression branch."""
    if behavioral_block is None:
        behavioral_block = {
            "user_observable": "User sees task list on /tasks",
            "how_to_check": "Navigate to /tasks; expect at least one row.",
            "failure_language": "The /tasks page does not load.",
        }
    payload = {
        "phase_number": 2,
        "detail": f"Phase {raw_id}: test",
        "category": raw_id.split("-")[0],
        "raw_id": raw_id,
        "behavioral_verification": behavioral_block,
        "prior_phase_raw_id": prior_raw_id,
        "prior_phase_how_to_check": prior_how_to_check,
    }
    with open(os.path.join(workspace, "current_phase.json"), "w") as f:
        json.dump(payload, f)


def _write_current_phase_no_prior(workspace, raw_id="CORE-E1"):
    """Write current_phase.json with prior_phase_raw_id: None — the first
    phase, or a phase whose predecessors were all blocked/skipped."""
    payload = {
        "phase_number": 1,
        "detail": f"Phase {raw_id}: test",
        "category": raw_id.split("-")[0],
        "raw_id": raw_id,
        "behavioral_verification": {
            "user_observable": "x",
            "how_to_check": "y",
            "failure_language": "z",
        },
        "prior_phase_raw_id": None,
        "prior_phase_how_to_check": None,
    }
    with open(os.path.join(workspace, "current_phase.json"), "w") as f:
        json.dump(payload, f)


def _write_phase_state(workspace, **kwargs):
    state = {
        "planner_retries": 0,
        "executor_retries": 0,
        "reviewer_retries": 0,
        **kwargs,
    }
    with open(os.path.join(workspace, "phase_state.json"), "w") as f:
        json.dump(state, f)


def _write_done_artifacts(workspace, raw_id):
    """Satisfy the existing _check_done_criteria_artifacts pre-gate."""
    phases_dir = os.path.join(workspace, "phases")
    os.makedirs(phases_dir, exist_ok=True)
    with open(os.path.join(phases_dir, f"{raw_id}.md"), "w") as f:
        f.write(f"# {raw_id}\n")
    with open(os.path.join(workspace, "metrics.jsonl"), "w") as f:
        f.write(json.dumps({"phase": raw_id, "ts": "2026-05-27T00:00:00Z"}) + "\n")


def _make_evidence_files(workspace, prefix="regression", n=3):
    """Create n evidence anchor files under workspace/behavioral-smoke/<prefix>/."""
    bdir = os.path.join(workspace, "behavioral-smoke", prefix)
    os.makedirs(bdir, exist_ok=True)
    paths = []
    for i in range(n):
        rel = f"behavioral-smoke/{prefix}/anchor-{i + 1}.txt"
        with open(os.path.join(workspace, rel), "w") as f:
            f.write(f"{prefix} anchor {i + 1}\n")
        paths.append(rel)
    return paths


def _valid_regression_block(
    workspace, prior_raw_id="CORE-E1", n_anchors=3, verdict="pass", followed=True
):
    """Return a valid regression_verification object with n_anchors evidence
    entries whose paths point at on-disk files under workspace.

    On verdict="pass" AND followed=True, the gate enforces ≥3 anchors."""
    paths = _make_evidence_files(workspace, prefix="regression", n=n_anchors)
    block = {
        "verdict": verdict,
        "prior_phase_raw_id": prior_raw_id,
        "prior_phase_how_to_check_followed": followed,
        "evidence": [
            {
                "claim": f"Regression claim {i + 1}: prior recipe still passes",
                "file_or_screenshot_or_log": paths[i],
                "method": "stdout_capture",
            }
            for i in range(n_anchors)
        ],
    }
    return block


def _valid_behavioral_block(workspace, n_anchors=3, verdict="pass"):
    """Return a valid behavioral_verification object — required to keep the
    existing gate behavioural check happy on regression-focused tests."""
    paths = _make_evidence_files(workspace, prefix="behavioral", n=n_anchors)
    return {
        "verdict": verdict,
        "evidence": [
            {
                "claim": f"Behavioural claim {i + 1}",
                "file_or_screenshot_or_log": paths[i],
                "method": "stdout_capture",
            }
            for i in range(n_anchors)
        ],
        "how_to_check_followed": True,
    }


def _reviewer_output(**overrides):
    """Baseline reviewer output. Override fields per test."""
    out = {
        "blocking_issues": [],
        "suggestions": [],
        "integration_tests_passing": True,
    }
    out.update(overrides)
    return out


# ---------------------------------------------------------------------------
# §3 File 3 — twelve rows
# ---------------------------------------------------------------------------


class TestRegressionCheckEvaluateReviewer:
    """End-to-end gate behaviour for the regression dimension."""

    def test_no_prior_phase_skips_regression_check(self, tmp_workspace):
        """current_phase has prior_phase_raw_id: None — no regression check
        runs. Reviewer output need not contain a regression_verification
        field. The gate must return PASS (assuming the rest of the output is
        well-formed)."""
        _write_current_phase_no_prior(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E1")
        bv_block = _valid_behavioral_block(tmp_workspace)
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(
                _reviewer_output(behavioral_verification=bv_block), f
            )

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "PASS", (
            f"Without prior_phase_*, the regression branch must be a no-op. "
            f"Got {result!r}"
        )

    def test_prior_phase_with_valid_pass_regression_block_passes(
        self, tmp_workspace
    ):
        """Happy path: prior phase has a recipe, reviewer ran it, all three
        regression anchors exist on disk. Gate returns PASS."""
        _write_current_phase_with_prior(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E2")
        bv_block = _valid_behavioral_block(tmp_workspace)
        regression_block = _valid_regression_block(tmp_workspace)
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(
                _reviewer_output(
                    behavioral_verification=bv_block,
                    regression_verification=regression_block,
                ),
                f,
            )

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "PASS", (
            f"Valid regression block with verdict:pass + 3 on-disk anchors + "
            f"followed:True must pass the gate. Got {result!r}"
        )

    def test_prior_phase_with_missing_regression_block_returns_regression_unverified(
        self, tmp_workspace
    ):
        """current_phase has prior_phase_* set, but reviewer output has no
        regression_verification field at all → REGRESSION_UNVERIFIED.

        Shape failure, non-retry-consuming on the pooled counter. The gate
        records the error code but does not increment any retry counter
        (orchestrator owns that)."""
        _write_current_phase_with_prior(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E2")
        bv_block = _valid_behavioral_block(tmp_workspace)
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(
                _reviewer_output(behavioral_verification=bv_block), f
            )

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "REGRESSION_UNVERIFIED"
        with open(os.path.join(tmp_workspace, "phase_state.json")) as f:
            state = json.load(f)
        assert state.get("last_error_code") == "ERR_REGRESSION_UNVERIFIED"
        # reviewer_retries unchanged — the counter the orchestrator owns is
        # the pooled reviewer_unverified_retries (not asserted here; this
        # is the gate-side test).
        assert state.get("reviewer_retries", 0) == 0

    def test_prior_phase_with_fail_verdict_routes_through_executor(
        self, tmp_workspace
    ):
        """Reviewer ran the prior recipe and it FAILED — the regression
        feature broke. Routes through ROUTE_EXECUTOR with
        ERR_REGRESSION_PRIOR_PHASE and a synthesised blocking_issue carrying
        criterion_source: 'regression_prior_phase'."""
        _write_current_phase_with_prior(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E2")
        bv_block = _valid_behavioral_block(tmp_workspace)
        # fail verdict — gate does NOT enforce ≥3 anchors on non-pass verdicts,
        # but we still need followed:True to distinguish from the "did not
        # run the recipe" case.
        regression_block = {
            "verdict": "fail",
            "prior_phase_raw_id": "CORE-E1",
            "prior_phase_how_to_check_followed": True,
            "failure_summary": "GET /api/tasks now returns 500",
            "evidence": [],
        }
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(
                _reviewer_output(
                    behavioral_verification=bv_block,
                    regression_verification=regression_block,
                ),
                f,
            )

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        # Pass 1 of a rejection routes to executor
        assert result == "ROUTE_EXECUTOR"
        with open(os.path.join(tmp_workspace, "phase_state.json")) as f:
            state = json.load(f)
        assert state.get("last_error_code") == "ERR_REGRESSION_PRIOR_PHASE"

        # Reviewer output was atomically rewritten — synthesised blocking_issue
        # must carry the regression_prior_phase attribution.
        with open(output_path) as f:
            rewritten = json.load(f)
        issues = rewritten.get("blocking_issues") or []
        regression_issues = [
            bi for bi in issues
            if isinstance(bi, dict)
            and bi.get("criterion_source") == "regression_prior_phase"
        ]
        assert len(regression_issues) == 1, (
            f"Exactly one synthesised regression blocking_issue expected, "
            f"got {len(regression_issues)}: {regression_issues}"
        )
        bi = regression_issues[0]
        assert bi.get("attribution") == "impl"
        assert bi.get("criterion_id") == "CORE-E1"
        assert bi.get("description") == "GET /api/tasks now returns 500", (
            f"failure_summary must drive description when present; "
            f"got {bi.get('description')!r}"
        )

    def test_prior_phase_with_cannot_verify_verdict_routes_through_executor(
        self, tmp_workspace
    ):
        """verdict: cannot_verify, no failure_summary → routes through
        ROUTE_EXECUTOR with the synthesised description
        'Prior phase regression check could not verify'."""
        _write_current_phase_with_prior(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E2")
        bv_block = _valid_behavioral_block(tmp_workspace)
        regression_block = {
            "verdict": "cannot_verify",
            "prior_phase_raw_id": "CORE-E1",
            "prior_phase_how_to_check_followed": True,
            "evidence": [],
        }
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(
                _reviewer_output(
                    behavioral_verification=bv_block,
                    regression_verification=regression_block,
                ),
                f,
            )

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "ROUTE_EXECUTOR"
        with open(os.path.join(tmp_workspace, "phase_state.json")) as f:
            state = json.load(f)
        assert state.get("last_error_code") == "ERR_REGRESSION_PRIOR_PHASE"

        with open(output_path) as f:
            rewritten = json.load(f)
        regression_issues = [
            bi for bi in rewritten.get("blocking_issues", [])
            if isinstance(bi, dict)
            and bi.get("criterion_source") == "regression_prior_phase"
        ]
        assert len(regression_issues) == 1
        assert regression_issues[0]["description"] == (
            "Prior phase regression check could not verify"
        )

    def test_prior_phase_how_to_check_followed_false_routes_through_executor(
        self, tmp_workspace
    ):
        """`prior_phase_how_to_check_followed: False` is treated identically
        to cannot_verify, even when verdict is 'pass'. Routes through
        ROUTE_EXECUTOR with a description that names the un-executed recipe.

        Pins the locked design decision (plan §1.locked-design-decisions
        bullet 3)."""
        _write_current_phase_with_prior(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E2")
        bv_block = _valid_behavioral_block(tmp_workspace)
        regression_block = {
            "verdict": "pass",
            "prior_phase_raw_id": "CORE-E1",
            "prior_phase_how_to_check_followed": False,
            "evidence": [],
        }
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(
                _reviewer_output(
                    behavioral_verification=bv_block,
                    regression_verification=regression_block,
                ),
                f,
            )

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "ROUTE_EXECUTOR"
        with open(os.path.join(tmp_workspace, "phase_state.json")) as f:
            state = json.load(f)
        assert state.get("last_error_code") == "ERR_REGRESSION_PRIOR_PHASE"

        with open(output_path) as f:
            rewritten = json.load(f)
        regression_issues = [
            bi for bi in rewritten.get("blocking_issues", [])
            if isinstance(bi, dict)
            and bi.get("criterion_source") == "regression_prior_phase"
        ]
        assert len(regression_issues) == 1
        assert regression_issues[0]["description"] == (
            "Prior phase CORE-E1 how_to_check recipe was not executed"
        )


class TestRegressionShapeValidation:
    """Shape failures of the regression_verification object trigger
    REGRESSION_UNVERIFIED. Mirror of the behavioural shape-validation rules
    using the shared evidence constants (deliberate coupling — see
    coupling comment in reviewer_gate.py)."""

    def test_regression_block_with_wrong_prior_phase_raw_id_fails_shape_check(
        self, tmp_workspace
    ):
        """The reviewer cannot claim a regression against a different prior
        phase than the resolver wrote. Shape violation."""
        _write_current_phase_with_prior(tmp_workspace, prior_raw_id="CORE-E1")
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E2")
        bv_block = _valid_behavioral_block(tmp_workspace)
        # reviewer claims regression against CORE-E0, not CORE-E1
        regression_block = _valid_regression_block(
            tmp_workspace, prior_raw_id="CORE-E0"
        )
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(
                _reviewer_output(
                    behavioral_verification=bv_block,
                    regression_verification=regression_block,
                ),
                f,
            )

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "REGRESSION_UNVERIFIED"

    def test_regression_block_path_traversal_rejected(self, tmp_workspace):
        """Evidence path escapes workspace → shape failure. Same path-bounding
        rule as the behavioural check (shared via the coupling comment)."""
        _write_current_phase_with_prior(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E2")
        bv_block = _valid_behavioral_block(tmp_workspace)
        regression_block = _valid_regression_block(tmp_workspace)
        regression_block["evidence"][0]["file_or_screenshot_or_log"] = "../escape.txt"
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(
                _reviewer_output(
                    behavioral_verification=bv_block,
                    regression_verification=regression_block,
                ),
                f,
            )

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "REGRESSION_UNVERIFIED"

    def test_regression_block_evidence_file_missing_on_disk_fails(
        self, tmp_workspace
    ):
        """Evidence path workspace-bound but file doesn't exist → shape
        failure. On-disk existence is required for verdict:pass anchors."""
        _write_current_phase_with_prior(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E2")
        bv_block = _valid_behavioral_block(tmp_workspace)
        regression_block = _valid_regression_block(tmp_workspace)
        regression_block["evidence"][0]["file_or_screenshot_or_log"] = (
            "behavioral-smoke/regression/does-not-exist.txt"
        )
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(
                _reviewer_output(
                    behavioral_verification=bv_block,
                    regression_verification=regression_block,
                ),
                f,
            )

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "REGRESSION_UNVERIFIED"

    def test_regression_block_pass_verdict_with_two_anchors_fails_shape_check(
        self, tmp_workspace
    ):
        """verdict:pass + followed:True + only 2 anchors → shape failure.
        Pins the ≥3 anchors rule for regression (shared with behavioural via
        the coupling comment). A future fork of the constants that weakens
        the regression bar fires here."""
        _write_current_phase_with_prior(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E2")
        bv_block = _valid_behavioral_block(tmp_workspace)
        regression_block = _valid_regression_block(tmp_workspace, n_anchors=2)
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(
                _reviewer_output(
                    behavioral_verification=bv_block,
                    regression_verification=regression_block,
                ),
                f,
            )

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "REGRESSION_UNVERIFIED"


class TestRegressionSynthesisCoexistence:
    """Pin the dual-synthesizer idempotency contract — without it, a pre-
    populated blocking_issues list with only behavioural entries would block
    regression synthesis from firing.

    The behavioural synthesiser skips when ``blocking_issues`` is non-empty;
    the regression synthesiser must use a stricter idempotency check that
    only skips when an existing entry already carries
    ``criterion_source == "regression_prior_phase"``."""

    def test_regression_synthesis_idempotent_when_blocking_issue_already_has_regression_source(
        self, tmp_workspace
    ):
        """Pre-populated blocking_issues already contains a regression entry
        — the synthesiser must NOT add a duplicate."""
        _write_current_phase_with_prior(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E2")
        bv_block = _valid_behavioral_block(tmp_workspace)
        regression_block = {
            "verdict": "fail",
            "prior_phase_raw_id": "CORE-E1",
            "prior_phase_how_to_check_followed": True,
            "failure_summary": "Something broke",
            "evidence": [],
        }
        pre_populated = {
            "description": "manual regression entry",
            "criterion_source": "regression_prior_phase",
            "criterion_id": "CORE-E1",
            "attribution": "impl",
            "affected_file": "",
        }
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(
                _reviewer_output(
                    blocking_issues=[pre_populated],
                    behavioral_verification=bv_block,
                    regression_verification=regression_block,
                ),
                f,
            )

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "ROUTE_EXECUTOR"

        with open(output_path) as f:
            rewritten = json.load(f)
        regression_issues = [
            bi for bi in rewritten.get("blocking_issues", [])
            if isinstance(bi, dict)
            and bi.get("criterion_source") == "regression_prior_phase"
        ]
        assert len(regression_issues) == 1, (
            f"Idempotent: only the pre-populated regression entry must remain; "
            f"got {len(regression_issues)}: {regression_issues}"
        )
        # The pre-populated one survives untouched
        assert regression_issues[0]["description"] == "manual regression entry"

    def test_dual_synthesis_behavioral_and_regression_coexist_when_both_fail_with_empty_initial_list(
        self, tmp_workspace
    ):
        """Both behavioural AND regression fail, blocking_issues starts empty.
        Both synthesisers fire — final list contains ONE behavioural-flavoured
        entry AND ONE regression-flavoured entry.

        Without the regression idempotency change (skip when regression entry
        exists, NOT when list is empty), the behavioural synthesiser's
        empty-list path consumes the slot and regression is skipped."""
        _write_current_phase_with_prior(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E2")

        # Build a single-anchor behavioural-failure block so the behavioural
        # synthesiser produces exactly one entry from its single evidence row
        bv_anchor_paths = _make_evidence_files(
            tmp_workspace, prefix="behavioral", n=1
        )
        bv_block = {
            "verdict": "fail",
            "how_to_check_followed": True,
            "evidence": [{
                "claim": "Current phase behavioural claim broke",
                "file_or_screenshot_or_log": bv_anchor_paths[0],
                "method": "stdout_capture",
            }],
        }
        regression_block = {
            "verdict": "fail",
            "prior_phase_raw_id": "CORE-E1",
            "prior_phase_how_to_check_followed": True,
            "failure_summary": "Prior phase recipe regressed",
            "evidence": [],
        }
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(
                _reviewer_output(
                    blocking_issues=[],  # empty — both synthesisers see open slot
                    behavioral_verification=bv_block,
                    regression_verification=regression_block,
                ),
                f,
            )

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "ROUTE_EXECUTOR"

        with open(output_path) as f:
            rewritten = json.load(f)
        issues = rewritten.get("blocking_issues", [])

        behavioral_issues = [
            bi for bi in issues
            if isinstance(bi, dict)
            and bi.get("criterion_source") == "behavioral"
        ]
        regression_issues = [
            bi for bi in issues
            if isinstance(bi, dict)
            and bi.get("criterion_source") == "regression_prior_phase"
        ]

        assert len(behavioral_issues) == 1, (
            f"Behavioural synthesiser must fire — got "
            f"{len(behavioral_issues)}: {behavioral_issues}"
        )
        assert len(regression_issues) == 1, (
            f"Regression synthesiser must coexist with behavioural — "
            f"without the regression-source-keyed idempotency, the behavioural "
            f"synthesiser consumes the empty-list slot and regression is "
            f"silently skipped. Got {len(regression_issues)}: "
            f"{regression_issues}"
        )
