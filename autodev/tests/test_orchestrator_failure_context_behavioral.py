"""P0 Stage G — orchestrator failure_context behavioural extensions.

Two surfaces under test:

  - ``write_failure_context`` carries the **claimed-vs-observed** behavioural
    verification snapshot. Two new top-level fields on ``failure_context.json``:
      * ``behavioral_verification_evidence`` — from
        ``reviewer_output.behavioral_verification`` when the reviewer is the
        failing agent (None otherwise).
      * ``current_phase_behavioral_verification`` — from
        ``current_phase.behavioral_verification`` (the claimed contract).
  - ``_write_reviewer_failure_context`` enriches each blocking issue with
    ``criterion_source`` (the four-valued enum
    ``"behavioral" | "test" | "regression_prior_phase" | "free"``) and ``criterion_id``
    when an anchor is supplied. Entries that arrive without ``criterion_source``
    get the explicit ``"free"`` label so downstream code can branch on a
    complete enum without None-checking.

Test pattern follows the bare-orchestrator construction in
``test_reviewer_routing_dispatch.py::TestResetExecutionStateSync``.
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


def _bare_orchestrator(tmp_path, monkeypatch, raw_id="CORE-E1"):
    """Construct a bare Orchestrator with paths redirected to tmp_path.

    Mirrors the pattern in ``test_reviewer_routing_dispatch.py``. Used for tests
    that exercise the helper methods without booting the full pipeline."""
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json"))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))

    # Minimal phase_state on disk so read_phase_state() does not blow up.
    (tmp_path / "phase_state.json").write_text(json.dumps({
        "planner_retries": 0,
        "executor_retries": 0,
        "reviewer_retries": 0,
        "reviewer_rejected": False,
        "escalation_resets": 0,
    }))

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {
        "current_phase": 1,
        "current_phase_raw_id": raw_id,
        "current_agent": "reviewer",
        "planner_retries": 0,
        "executor_retries": 0,
        "reviewer_retries": 0,
        "pipeline_status": "RUNNING",
    }
    orch.openclaw_config = {}
    orch.lock_fd = None
    return orch


def _write_current_phase(tmp_path, with_block=True, raw_id="CORE-E1",
                        failure_language="The X view did not load."):
    """Write current_phase.json into tmp_path, with or without a behavioural
    block."""
    payload = {
        "phase_number": 1,
        "detail": f"Phase {raw_id}: test",
        "category": raw_id.split("-")[0],
        "raw_id": raw_id,
        "status": "PENDING",
        "exit_criteria": [],
    }
    if with_block:
        payload["behavioral_verification"] = {
            "user_observable": "User sees X.",
            "how_to_check": "Run foo; expect non-empty stdout.",
            "failure_language": failure_language,
        }
    else:
        payload["behavioral_verification"] = None
    (tmp_path / "current_phase.json").write_text(json.dumps(payload))


def _write_reviewer_output(tmp_path, behavioral_block=None, blocking_issues=None):
    """Write reviewer_output.json with the supplied behavioural block."""
    payload = {
        "blocking_issues": blocking_issues if blocking_issues is not None else [],
        "integration_tests_passing": True,
    }
    if behavioral_block is not None:
        payload["behavioral_verification"] = behavioral_block
    (tmp_path / "reviewer_output.json").write_text(json.dumps(payload))


class TestWriteFailureContextBehavioralFields:
    """``write_failure_context`` must carry the claimed-vs-observed behavioural
    verification snapshot when applicable."""

    def test_write_failure_context_includes_behavioral_verification_evidence_when_reviewer_failed(
        self, tmp_path, monkeypatch
    ):
        orch = _bare_orchestrator(tmp_path, monkeypatch)
        _write_current_phase(tmp_path, with_block=True)
        behavioral_block = {
            "verdict": "fail",
            "how_to_check_followed": True,
            "evidence": [
                {"claim": "c1", "file_or_screenshot_or_log": "behavioral-smoke/a.txt", "method": "stdout_capture"},
            ],
        }
        _write_reviewer_output(tmp_path, behavioral_block=behavioral_block)

        orch.write_failure_context("reviewer", attempt_number=1)
        fc = json.loads((tmp_path / "failure_context.json").read_text())

        assert fc.get("behavioral_verification_evidence") == behavioral_block, (
            "failure_context.json must carry the reviewer's behavioral_verification "
            "verbatim when the reviewer is the failing agent — this is the *observed* "
            "half of the claimed-vs-observed snapshot the executor's self-heal pass "
            "needs"
        )

    def test_write_failure_context_includes_current_phase_behavioral_verification(
        self, tmp_path, monkeypatch
    ):
        orch = _bare_orchestrator(tmp_path, monkeypatch)
        _write_current_phase(
            tmp_path,
            with_block=True,
            failure_language="The /tasks page did not load.",
        )
        _write_reviewer_output(tmp_path, behavioral_block={
            "verdict": "fail", "how_to_check_followed": True, "evidence": [],
        })

        orch.write_failure_context("reviewer", attempt_number=1)
        fc = json.loads((tmp_path / "failure_context.json").read_text())

        cp_block = fc.get("current_phase_behavioral_verification")
        assert isinstance(cp_block, dict), (
            "current_phase_behavioral_verification must be the *claimed* half — "
            "what the phase contract promised, regardless of which agent failed"
        )
        assert cp_block.get("failure_language") == "The /tasks page did not load.", (
            "failure_language must be preserved verbatim so the escalation "
            "advisory can quote it (Stage G item 4) without a second file read"
        )

    def test_write_failure_context_omits_behavioral_evidence_when_reviewer_not_failing(
        self, tmp_path, monkeypatch
    ):
        """When the executor (not the reviewer) is the failing agent, the
        observed behavioural verdict is irrelevant — the reviewer never produced
        one in this attempt. The field must be None (not an empty dict, not
        a stale prior verdict)."""
        orch = _bare_orchestrator(tmp_path, monkeypatch)
        _write_current_phase(tmp_path, with_block=True)
        # No reviewer_output.json on disk — executor failed before reviewer ran.
        orch.write_failure_context("executor", attempt_number=1)
        fc = json.loads((tmp_path / "failure_context.json").read_text())
        assert fc.get("behavioral_verification_evidence") is None, (
            "behavioral_verification_evidence must be None when the reviewer is "
            "not the failing agent — otherwise a stale reviewer verdict from a "
            "prior attempt could pollute the executor's failure context"
        )
        # Claimed half still present — that's a property of the phase, not the failure.
        assert fc.get("current_phase_behavioral_verification") is not None

    def test_write_failure_context_carries_both_claimed_and_observed_simultaneously(
        self, tmp_path, monkeypatch
    ):
        orch = _bare_orchestrator(tmp_path, monkeypatch)
        claimed_language = "The /tasks page did not load."
        _write_current_phase(tmp_path, with_block=True, failure_language=claimed_language)
        observed_block = {
            "verdict": "cannot_verify",
            "how_to_check_followed": False,
            "evidence": [],
        }
        _write_reviewer_output(tmp_path, behavioral_block=observed_block)

        orch.write_failure_context("reviewer", attempt_number=1)
        fc = json.loads((tmp_path / "failure_context.json").read_text())

        assert fc.get("behavioral_verification_evidence") == observed_block
        assert (fc.get("current_phase_behavioral_verification") or {}).get("failure_language") == claimed_language, (
            "both halves of the snapshot must be present and distinct — Stage G "
            "is the only place where the executor's retry pass sees what was "
            "claimed AND what was observed in one read"
        )


class TestWriteReviewerFailureContextCriterionEnrichment:
    """``_write_reviewer_failure_context`` enriches each blocking issue with an
    explicit ``criterion_source`` enum so the executor's reviewer-rejection
    AGENTS.md trigger phrase fires correctly."""

    def test_blocking_issues_carry_criterion_source_behavioral_from_synthesis(
        self, tmp_path, monkeypatch
    ):
        """Gate-synthesised behavioural issues arrive with criterion_source
        already set — the orchestrator's enricher must preserve them verbatim,
        not overwrite them with the free default."""
        orch = _bare_orchestrator(tmp_path, monkeypatch)
        gate_synthesised = [{
            "description": "User sees X",
            "attribution": "impl",
            "affected_file": "behavioral-smoke/anchor-1.txt",
            "criterion_source": "behavioral",
            "criterion_id": "behavioral_evidence[0]",
        }]
        orch._write_reviewer_failure_context(
            blocking_issues=gate_synthesised,
            reviewer_summary="behavioural rejection",
            reviewer_pass=1,
        )
        fc = json.loads((tmp_path / "failure_context.json").read_text())
        bi = fc["blocking_issues"][0]
        assert bi["criterion_source"] == "behavioral", (
            "gate-synthesised behavioural issues must keep their criterion_source "
            "through the orchestrator write path — overwriting them with 'free' "
            "would silently disable the executor's how_to_check re-run trigger"
        )
        assert bi["criterion_id"] == "behavioral_evidence[0]"

    def test_blocking_issues_carry_criterion_source_test_for_test_anchored(
        self, tmp_path, monkeypatch
    ):
        """Reviewer-agent-written test-anchored issues use ``criterion_source:
        "test"`` and the criterion_id is the test file path."""
        orch = _bare_orchestrator(tmp_path, monkeypatch)
        test_anchored = [{
            "description": "test_foo asserted X but got Y",
            "attribution": "impl",
            "affected_file": "src/foo.py",
            "criterion_source": "test",
            "criterion_id": "tests/test_foo.py",
        }]
        orch._write_reviewer_failure_context(
            blocking_issues=test_anchored,
            reviewer_summary="test failure",
            reviewer_pass=1,
        )
        fc = json.loads((tmp_path / "failure_context.json").read_text())
        bi = fc["blocking_issues"][0]
        assert bi["criterion_source"] == "test"
        assert bi["criterion_id"] == "tests/test_foo.py"

    def test_blocking_issues_default_to_free_when_no_anchor_supplied(
        self, tmp_path, monkeypatch
    ):
        """Reviewer-written free-form issue (no anchor field set). The
        orchestrator's enricher must apply the explicit ``"free"`` label so
        downstream code (executor AGENTS.md trigger, dashboard rendering) can
        branch on a complete enum without None-checking."""
        orch = _bare_orchestrator(tmp_path, monkeypatch)
        free_form = [{
            "description": "Module has dead imports",
            "attribution": "impl",
            "affected_file": "src/foo.py",
        }]
        orch._write_reviewer_failure_context(
            blocking_issues=free_form,
            reviewer_summary="dead imports",
            reviewer_pass=1,
        )
        fc = json.loads((tmp_path / "failure_context.json").read_text())
        bi = fc["blocking_issues"][0]
        assert bi.get("criterion_source") == "free", (
            "blocking issues without a populated criterion_source must default "
            "to the explicit 'free' label — leaving the field absent forces "
            "every downstream consumer to None-check, which is the bug pattern "
            "Stage G is meant to eliminate"
        )

    def test_blocking_issues_default_criterion_id_absent_for_free_source(
        self, tmp_path, monkeypatch
    ):
        """A free-source issue has no anchor — criterion_id must be absent
        (NOT empty string, NOT null) so the JSON shape signals 'no anchor'
        unambiguously."""
        orch = _bare_orchestrator(tmp_path, monkeypatch)
        free_form = [{
            "description": "Module has dead imports",
            "attribution": "impl",
            "affected_file": "src/foo.py",
        }]
        orch._write_reviewer_failure_context(
            blocking_issues=free_form,
            reviewer_summary="dead imports",
            reviewer_pass=1,
        )
        fc = json.loads((tmp_path / "failure_context.json").read_text())
        bi = fc["blocking_issues"][0]
        assert "criterion_id" not in bi, (
            "free-source blocking issues must omit criterion_id entirely — "
            "there is no anchor to point at, and adding an empty-string "
            "or null field would force downstream code to truthiness-check "
            "instead of presence-check"
        )
