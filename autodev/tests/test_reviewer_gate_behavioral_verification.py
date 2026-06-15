"""P0 Stage F — reviewer-gate behavioral verification contract.

The reviewer gate must enforce a structured ``behavioral_verification`` block
on every phase whose ``current_phase.json`` carries a populated Behavioral
Verification spec (effectively every P0 phase). This is content-driven, not
prefix-driven — the parallel concept to ``_is_visual_phase`` but universal
rather than UI/INT-only.

Stage F adds:
  - ``_requires_behavioral_verification(current_phase)`` — content-driven helper
  - ``_check_behavioral_verification(data)`` — contract-shape validator
  - ``BEHAVIORAL_UNVERIFIED`` verdict (non-``reviewer_retries``-consuming
    re-invocation, analogous to ``VISUAL_UNVERIFIED``)
  - Removal of ``phase_intent_validated`` from the validation block — replaced
    by ``behavioral_rejection`` (verdict ∈ fail/cannot_verify on a behavioural
    phase) firing ``ERR_VALIDATION_FAILED``

Mirror of the visual-verification suite in
``test_reviewer_gate_visual_verification.py``.
"""

import json
import os
import sys
import tempfile
from contextlib import ExitStack
from unittest.mock import patch

import pytest

# Path wiring handled by conftest.py
import utils as utils_module
import reviewer_gate as reviewer_gate_module


def _make_escape_symlink(workspace, link_name="sneaky", target_file="anchor.txt"):
    """Create an in-workspace symlink that resolves OUTSIDE the workspace, plus a
    real file at its target. Returns the workspace-relative path
    ``"<link_name>/<target_file>"`` — lexically inside the workspace (old guard
    accepts) but ``realpath``-outside (hardened guard rejects). The precondition
    assert keeps the test valid on symlinked-TMPDIR hosts (e.g. macOS)."""
    parent = os.path.dirname(workspace.rstrip(os.sep))
    outside = tempfile.mkdtemp(dir=parent, prefix="ws_escape_")
    assert os.path.commonpath(
        [os.path.realpath(outside), os.path.realpath(workspace)]
    ) != os.path.realpath(workspace), "escape target must be outside the workspace"
    with open(os.path.join(outside, target_file), "w") as f:
        f.write("secret\n")
    os.symlink(outside, os.path.join(workspace, link_name))
    return f"{link_name}/{target_file}"


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


def _write_current_phase_with_behavioral(workspace, raw_id="CORE-E1", block=None):
    """Write current_phase.json with a populated Behavioral Verification block."""
    if block is None:
        block = {
            "user_observable": "The user sees a list of tasks on /tasks.",
            "how_to_check": "Navigate to /tasks; expect at least one row rendered.",
            "failure_language": "The /tasks page does not load.",
        }
    payload = {
        "phase_number": 1,
        "detail": f"Phase {raw_id}: test",
        "category": raw_id.split("-")[0],
        "raw_id": raw_id,
        "behavioral_verification": block,
    }
    with open(os.path.join(workspace, "current_phase.json"), "w") as f:
        json.dump(payload, f)


def _write_current_phase_no_behavioral(workspace, raw_id="CORE-E1"):
    """Write current_phase.json WITHOUT a behavioral block (legacy/transitional)."""
    payload = {
        "phase_number": 1,
        "detail": f"Phase {raw_id}: test",
        "category": raw_id.split("-")[0],
        "raw_id": raw_id,
        "behavioral_verification": None,
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
        f.write(json.dumps({"phase": raw_id, "ts": "2026-05-22T00:00:00Z"}) + "\n")


def _make_evidence_files(workspace, n=3):
    """Create n evidence anchor files under workspace/behavioral-smoke/."""
    bdir = os.path.join(workspace, "behavioral-smoke")
    os.makedirs(bdir, exist_ok=True)
    paths = []
    for i in range(n):
        rel = f"behavioral-smoke/anchor-{i + 1}.txt"
        with open(os.path.join(workspace, rel), "w") as f:
            f.write(f"anchor {i + 1}\n")
        paths.append(rel)
    return paths


def _valid_behavioral_block(workspace, n_anchors=3, verdict="pass"):
    """Return a valid behavioral_verification object with n_anchors evidence
    entries whose paths point at on-disk files under workspace."""
    paths = _make_evidence_files(workspace, n=n_anchors)
    return {
        "verdict": verdict,
        "evidence": [
            {
                "claim": f"Claim {i + 1}: surface item exercised",
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
# B1 — content-driven phase detection
#
# The predicate ``_requires_behavioral_verification`` was extracted to
# ``gate_scripts.utils.phase_has_behavioral_block`` in P1 Stage D Hygiene H1.
# Its contract is now pinned by ``test_gate_helpers.TestPhaseHasBehavioralBlock``;
# the gate-local symbol is gone. The end-to-end ``B3`` tests below still
# exercise the call site through ``evaluate_reviewer`` — that's the right
# layer for this file.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# B2 — _check_behavioral_verification contract validator
# ---------------------------------------------------------------------------


class TestCheckBehavioralVerificationContract:
    """Validates the shape of ``reviewer_output.behavioral_verification``
    — same return contract as ``_check_visual_verification``: list of
    problem strings, [] when clean."""

    def test_missing_block_returns_problem(self):
        problems = reviewer_gate_module._check_behavioral_verification({})
        assert problems  # non-empty

    def test_invalid_verdict_enum_returns_problem(self, tmp_workspace):
        with _patch_workspace(tmp_workspace):
            problems = reviewer_gate_module._check_behavioral_verification({
                "behavioral_verification": {
                    "verdict": "almost",
                    "evidence": [],
                    "how_to_check_followed": True,
                },
            })
        assert problems
        assert any("verdict" in p for p in problems)

    def test_missing_how_to_check_followed_returns_problem(self, tmp_workspace):
        block = _valid_behavioral_block(tmp_workspace)
        block.pop("how_to_check_followed")
        with _patch_workspace(tmp_workspace):
            problems = reviewer_gate_module._check_behavioral_verification({
                "behavioral_verification": block,
            })
        assert problems
        assert any("how_to_check_followed" in p for p in problems)

    def test_pass_with_fewer_than_three_evidence_anchors_returns_problem(
        self, tmp_workspace
    ):
        block = _valid_behavioral_block(tmp_workspace, n_anchors=2)
        with _patch_workspace(tmp_workspace):
            problems = reviewer_gate_module._check_behavioral_verification({
                "behavioral_verification": block,
            })
        assert problems
        assert any("at least 3" in p or "least 3" in p for p in problems)

    def test_evidence_entry_missing_required_keys_returns_problem(
        self, tmp_workspace
    ):
        block = _valid_behavioral_block(tmp_workspace)
        block["evidence"][1].pop("method")
        with _patch_workspace(tmp_workspace):
            problems = reviewer_gate_module._check_behavioral_verification({
                "behavioral_verification": block,
            })
        assert problems
        assert any("method" in p for p in problems)

    def test_evidence_path_escaping_workspace_returns_problem(
        self, tmp_workspace
    ):
        block = _valid_behavioral_block(tmp_workspace)
        block["evidence"][0]["file_or_screenshot_or_log"] = "../escape.txt"
        with _patch_workspace(tmp_workspace):
            problems = reviewer_gate_module._check_behavioral_verification({
                "behavioral_verification": block,
            })
        assert problems
        assert any("escape" in p.lower() or "escapes workspace" in p
                   for p in problems)

    def test_non_string_evidence_path_returns_problem(self, tmp_workspace):
        """T1.2 — a truthy non-string ``file_or_screenshot_or_log`` (the
        key-presence loop only checks truthiness) must be rejected with a clear
        contract problem, mirroring the visual check's ``isinstance(path, str)``
        guard.

        RED on current code: ``os.path.isabs(123)`` raises ``TypeError`` and the
        whole gate crashes → reviewer ROUTE_ESCALATE."""
        block = _valid_behavioral_block(tmp_workspace)
        block["evidence"][0]["file_or_screenshot_or_log"] = 123
        with _patch_workspace(tmp_workspace):
            problems = reviewer_gate_module._check_behavioral_verification({
                "behavioral_verification": block,
            })
        assert problems
        assert any("must be a string" in p for p in problems)

    def test_evidence_path_symlink_escape_returns_problem(self, tmp_workspace):
        """T1.4 — an in-workspace symlink whose realpath escapes the workspace
        must be rejected. RED on current code: lexical ``abspath`` does not follow
        the symlink → boundary passes → file exists via the symlink → ``[]``."""
        block = _valid_behavioral_block(tmp_workspace)
        rel = _make_escape_symlink(tmp_workspace)
        block["evidence"][0]["file_or_screenshot_or_log"] = rel
        with _patch_workspace(tmp_workspace):
            problems = reviewer_gate_module._check_behavioral_verification({
                "behavioral_verification": block,
            })
        assert problems
        assert any("escape" in p.lower() for p in problems)

    def test_evidence_path_missing_on_disk_returns_problem(self, tmp_workspace):
        block = _valid_behavioral_block(tmp_workspace)
        block["evidence"][0]["file_or_screenshot_or_log"] = (
            "behavioral-smoke/does-not-exist.txt"
        )
        with _patch_workspace(tmp_workspace):
            problems = reviewer_gate_module._check_behavioral_verification({
                "behavioral_verification": block,
            })
        assert problems
        assert any("does not exist" in p for p in problems)

    def test_valid_pass_with_three_anchors_returns_empty(self, tmp_workspace):
        block = _valid_behavioral_block(tmp_workspace, n_anchors=3)
        with _patch_workspace(tmp_workspace):
            problems = reviewer_gate_module._check_behavioral_verification({
                "behavioral_verification": block,
            })
        assert problems == []

    def test_fail_verdict_does_not_require_three_anchors(self, tmp_workspace):
        """A 'fail' verdict is itself the rejection signal — it doesn't need
        three anchors. (Mirror of how visual_verification='fail' is permissive
        on artifact count.) The reviewer is reporting that it ran the check
        and it failed; how-to-check_followed is still required."""
        with _patch_workspace(tmp_workspace):
            problems = reviewer_gate_module._check_behavioral_verification({
                "behavioral_verification": {
                    "verdict": "fail",
                    "evidence": [],
                    "how_to_check_followed": True,
                },
            })
        assert problems == []


# ---------------------------------------------------------------------------
# B3 — evaluate_reviewer end-to-end behavioural verdict
# ---------------------------------------------------------------------------


class TestEvaluateReviewerBehavioralPath:
    """End-to-end: behavioural-phase reviewer output must produce the right
    gate verdict and write the right error code."""

    def test_missing_behavioral_block_on_behavioural_phase_unverified(
        self, tmp_workspace
    ):
        """Reviewer output without behavioral_verification on a phase that
        requires it → BEHAVIORAL_UNVERIFIED (contract-shape failure,
        non-retry-consuming)."""
        _write_current_phase_with_behavioral(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E1")
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(), f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "BEHAVIORAL_UNVERIFIED"

    def test_unverified_records_error_code_without_consuming_retries(
        self, tmp_workspace
    ):
        _write_current_phase_with_behavioral(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E1")
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(), f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "BEHAVIORAL_UNVERIFIED"

        with open(os.path.join(tmp_workspace, "phase_state.json")) as f:
            state = json.load(f)
        # The error code is stamped …
        assert state.get("last_error_code") == "ERR_BEHAVIORAL_UNVERIFIED"
        # … but reviewer_retries is NOT incremented (re-invocation, not a
        # legitimate code-quality rejection).
        assert state.get("reviewer_retries", 0) == 0

    def test_legacy_phase_without_block_passes_cleanly(self, tmp_workspace):
        """A phase whose current_phase.json has behavioral_verification: None
        (pre-P0 in-flight transitional case) must pass the gate even when the
        reviewer output omits the behavioral_verification object entirely."""
        _write_current_phase_no_behavioral(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E1")
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(), f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "PASS"

    def test_verdict_fail_routes_as_rejection(self, tmp_workspace):
        """``behavioral_verification.verdict == "fail"`` on a behavioural
        phase is itself the rejection signal — gate must route via
        apply_reviewer_routing (pass 1 → ROUTE_EXECUTOR)."""
        _write_current_phase_with_behavioral(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E1")
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(
                behavioral_verification={
                    "verdict": "fail",
                    "evidence": [],
                    "how_to_check_followed": True,
                },
            ), f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        # Pass 1 rejection → executor
        assert result == "ROUTE_EXECUTOR"

    def test_phase_intent_validated_no_longer_required(self, tmp_workspace):
        """Dead-code removal proof: a reviewer output with NO
        ``phase_intent_validated`` field but a valid behavioural verdict=pass
        passes the gate. Before P0 Stage F, line 153 read the boolean and
        rejected this shape."""
        _write_current_phase_with_behavioral(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E1")

        valid_block = _valid_behavioral_block(tmp_workspace)
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            # Note: no phase_intent_validated field anywhere in this dict.
            json.dump(_reviewer_output(
                behavioral_verification=valid_block,
            ), f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "PASS", (
            f"Expected PASS (behavioural verdict valid, phase_intent_validated "
            f"removed from the schema in Stage F), got {result!r}"
        )

    def test_visual_check_runs_before_behavioral_check(self, tmp_workspace):
        """Precedence: an INFRA/MISSING/VISUAL failure must take precedence
        over the behavioural contract check — without it, a UI phase missing
        screenshots would route to BEHAVIORAL_UNVERIFIED rather than the
        more specific VISUAL_UNVERIFIED. UI-prefixed phase + missing visual
        fields + missing behavioural block → VISUAL_UNVERIFIED (more specific
        verdict wins)."""
        _write_current_phase_with_behavioral(tmp_workspace, raw_id="UI-E1")
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "UI-E1")
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        # Neither visual nor behavioural fields supplied
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(), f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        # Visual check runs first; this is a UI phase missing visual_verification.
        assert result == "VISUAL_UNVERIFIED"

    def test_non_dict_behavioral_verification_on_non_behavioral_phase_ignored(
        self, tmp_workspace
    ):
        """T1.2 — on a NON-behavioural phase the contract check
        (``_check_behavioral_verification``) is skipped, so a truthy non-dict
        ``behavioral_verification`` in the reviewer output reaches the
        ``behavioral_verdict`` assignment unguarded. ``or {}`` only rescues falsy
        values, so a string crashes ``.get("verdict")``. Because
        ``phase_has_behavioral_block`` is False here, the verdict is irrelevant —
        the correct fix treats a non-dict as absent (``None``) and the gate PASSes.

        RED on current code: ``("garbage" or {}).get`` → ``AttributeError`` →
        the reviewer gate crashes → ROUTE_ESCALATE (a needless human escalation)."""
        _write_current_phase_no_behavioral(tmp_workspace)
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E1")
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(behavioral_verification="garbage"), f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "PASS", (
            f"A malformed behavioral_verification on a non-behavioural phase must "
            f"be ignored, not crash into ROUTE_ESCALATE; got {result!r}"
        )


class TestMockedExternalApiEvidenceAccepted:
    """PREREQ-4 — characterization + regression guard.

    ``_check_behavioral_verification`` is *shape*-driven: it validates
    verdict / anchor-count / required-keys / workspace-bounded path, and NEVER
    inspects the evidence text for "live" vs "mock". So behavioral evidence
    describing a MOCKED paid-API interaction already satisfies the gate.

    PREREQ-4's skill guidance (mock paid APIs by default; the reviewer accepts
    mocked evidence) rests on this property. This test is green-on-arrival by
    design — the phase makes no gate change, so there is no red-first state to
    assert. Its job is to LOCK the property: a future change that re-introduced a
    "live call required" assumption in the reviewer gate would turn this test
    red, which is exactly the regression PREREQ-4 must prevent.
    """

    def _mocked_paid_api_block(self, workspace, n_anchors=3):
        """A valid behavioral_verification(pass) whose evidence explicitly
        describes a mocked paid-API boundary — no live call asserted."""
        paths = _make_evidence_files(workspace, n=n_anchors)
        claims = [
            "POST /v1/charges to Stripe mocked via the responses library -> 200; "
            "no live API call made",
            "Provider 401 path exercised against a recorded fixture, not a live key",
            "Idempotency-key retry verified against a local fake endpoint",
        ]
        return {
            "verdict": "pass",
            "evidence": [
                {
                    "claim": claims[i],
                    "file_or_screenshot_or_log": paths[i],
                    "method": "mock_intercept",
                }
                for i in range(n_anchors)
            ],
            "how_to_check_followed": True,
        }

    def test_mocked_paid_api_evidence_passes_gate(self, tmp_workspace):
        """An external/paid-API phase whose behavioral evidence is entirely
        MOCKED (no live call) must PASS the reviewer gate."""
        _write_current_phase_with_behavioral(tmp_workspace, raw_id="API-E1")
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "API-E1")

        block = self._mocked_paid_api_block(tmp_workspace)
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(behavioral_verification=block), f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "PASS", (
            f"Mocked paid-API behavioral evidence must satisfy the gate (no live "
            f"call required); got {result!r}. If this is red, a live-call "
            f"assumption was re-introduced into reviewer_gate — the PREREQ-4 "
            f"regression this test guards."
        )
