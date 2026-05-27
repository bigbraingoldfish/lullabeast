"""P0 Stage G — end-to-end data path integration.

This file pins the full self-heal feedback loop: a behavioural rejection at
the reviewer gate must end with ``criterion_source: "behavioral"`` entries
in ``failure_context.blocking_issues``. That field is the trigger the
executor's AGENTS.md Scenario B reads to re-run the ``how_to_check``
procedure on retry — without this end-to-end pin, a regression in any one
of the four touch points (gate synthesis, gate write-back, orchestrator
general-context write, orchestrator reviewer-handoff write) would silently
break the executor's targeted self-heal pass.

Also includes source-level pins on the AGENTS.md files so that:
  - The executor's reviewer-rejection trigger phrase
    (``criterion_source == "behavioral"``) stays in place.
  - The reviewer's AGENTS.md documents ``criterion_source`` + ``criterion_id``
    per the Stage G defensive-symmetry rule.
"""

import json
import os
import sys
from contextlib import ExitStack
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import utils as utils_module  # noqa: E402
import reviewer_gate as reviewer_gate_module  # noqa: E402
import orchestrator as orch_mod  # noqa: E402


def _patch_gate_workspace(tmp_dir):
    """Redirect gate workspace paths to tmp_dir for the duration of a with-block."""
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


def _bare_orchestrator_pointing_at(tmp_path, monkeypatch, raw_id="CORE-E1"):
    """Bare Orchestrator with all path constants redirected to tmp_path."""
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json"))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {
        "current_phase": 1,
        "current_phase_raw_id": raw_id,
        "current_agent": "reviewer",
        "planner_retries": 0,
        "executor_retries": 0,
        "reviewer_retries": 0,
        "status": "RUNNING",
        "pipeline_status": "RUNNING",
    }
    orch.openclaw_config = {}
    orch.lock_fd = None
    return orch


class TestBehavioralRejectionEndToEndDataPath:
    """Drive a behavioural rejection through the full data path and verify
    ``criterion_source: "behavioral"`` arrives in failure_context.json."""

    def test_behavioural_rejection_synthesised_issues_arrive_in_failure_context(
        self, tmp_path, monkeypatch
    ):
        # 1. current_phase.json with populated behavioural block + raw_id "CORE-E1"
        cp_block = {
            "user_observable": "The user sees a list of tasks on /tasks.",
            "how_to_check": "Navigate to /tasks; expect a non-empty row count.",
            "failure_language": "The /tasks page did not load.",
        }
        (tmp_path / "current_phase.json").write_text(json.dumps({
            "phase_number": 1,
            "detail": "Phase CORE-E1: tasks view",
            "category": "CORE",
            "raw_id": "CORE-E1",
            "status": "PENDING",
            "exit_criteria": [],
            "behavioral_verification": cp_block,
        }))

        # 2. reviewer_output.json with verdict:"fail" + 3 evidence + empty blocking_issues
        evidence_dir = tmp_path / "behavioral-smoke"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence = []
        for i in range(3):
            rel = f"behavioral-smoke/anchor-{i + 1}.txt"
            (tmp_path / rel).write_text(f"anchor-{i + 1}")
            evidence.append({
                "claim": f"Claim {i + 1}",
                "file_or_screenshot_or_log": rel,
                "method": "stdout_capture",
            })
        rv_path = tmp_path / "reviewer_output.json"
        rv_path.write_text(json.dumps({
            "blocking_issues": [],
            "integration_tests_passing": True,
            "behavioral_verification": {
                "verdict": "fail",
                "how_to_check_followed": True,
                "evidence": evidence,
            },
        }))

        # 3. phase_state with reviewer_retries=0
        (tmp_path / "phase_state.json").write_text(json.dumps({
            "planner_retries": 0, "executor_retries": 0, "reviewer_retries": 0,
            "reviewer_rejected": False, "escalation_resets": 0,
        }))

        # 4. Done-criteria artifacts (phases/{id}.md + metrics.jsonl)
        phases_dir = tmp_path / "phases"
        phases_dir.mkdir(parents=True, exist_ok=True)
        (phases_dir / "CORE-E1.md").write_text("# CORE-E1\n")
        (tmp_path / "metrics.jsonl").write_text(
            json.dumps({"ts": "2026-05-22T00:00:00Z", "phase": "CORE-E1"}) + "\n"
        )

        # 5. Run the gate
        with _patch_gate_workspace(str(tmp_path)):
            verdict = reviewer_gate_module.evaluate_reviewer(str(rv_path))
        assert verdict == "ROUTE_EXECUTOR", (
            "behavioural failure on pass 1 must route to executor for self-heal"
        )

        # 6. reviewer_output.json on disk now has 3 synthesised blocking_issues
        rv_after = json.loads(rv_path.read_text())
        assert len(rv_after["blocking_issues"]) == 3, (
            "gate must persist synthesised blocking_issues back to disk so the "
            "orchestrator's downstream read sees them — this is the load-bearing "
            "data hop between the gate and the orchestrator"
        )
        assert all(bi["criterion_source"] == "behavioral" for bi in rv_after["blocking_issues"])

        # 7. Construct a bare orchestrator pointing at tmp_path
        orch = _bare_orchestrator_pointing_at(tmp_path, monkeypatch, raw_id="CORE-E1")

        # 8. write_failure_context (general path)
        orch.write_failure_context("reviewer", attempt_number=1)

        # 9. _write_reviewer_failure_context (specific path; ROUTE_EXECUTOR augmentation)
        orch._write_reviewer_failure_context(
            blocking_issues=rv_after["blocking_issues"],
            reviewer_summary="three behavioural claims unverified",
            reviewer_pass=1,
        )

        # 10. failure_context.json should carry the full chain
        fc = json.loads((tmp_path / "failure_context.json").read_text())
        assert fc.get("source") == "reviewer", "reviewer handoff marker required"
        assert any(
            bi.get("criterion_source") == "behavioral" for bi in fc.get("blocking_issues") or []
        ), (
            "failure_context.json must carry at least one blocking issue tagged "
            "criterion_source='behavioral' — this is the field the executor's "
            "AGENTS.md Scenario B trigger phrase keys on (line 145). Without it, "
            "the executor's reviewer-rejection retry does not re-run how_to_check, "
            "the next reviewer pass sees stale artifacts, and the rejection cycle "
            "does not converge"
        )
        # The claimed-vs-observed snapshot must be present too — that's the field
        # the escalation advisory reads if self-heal exhausts.
        cp_in_fc = fc.get("current_phase_behavioral_verification") or {}
        assert cp_in_fc.get("failure_language") == "The /tasks page did not load.", (
            "failure_context.current_phase_behavioral_verification.failure_language "
            "must mirror the phase contract verbatim — the escalation advisory "
            "reads this exact path (see "
            "test_advisory_failure_language_sourced_from_failure_context_not_current_phase)"
        )


class TestExecutorAgentMdSourceLevelGuard:
    """Source-level pins on executor/AGENTS.md. Stage E landed the prose; Stage
    G provides the data path. If a future change removes the trigger phrase,
    the executor stops re-running ``how_to_check`` on reviewer-rejection
    retries — silently."""

    def test_executor_agents_md_still_references_criterion_source_behavioral(self):
        path = os.path.join(REPO_ROOT, "autodev", "agents", "executor", "AGENTS.md")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        assert 'criterion_source == "behavioral"' in text, (
            "executor/AGENTS.md must still reference criterion_source == \"behavioral\" "
            "as the trigger for re-running how_to_check on reviewer-rejection "
            "retries — Stage G data path is wired to this exact phrase"
        )

    def test_executor_agents_md_inputs_section_references_behavioral_trigger(self):
        path = os.path.join(REPO_ROOT, "autodev", "agents", "executor", "AGENTS.md")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        # The trigger phrase appears in the Inputs section near the top of the
        # doc. We slice up to (but not including) the "## Output Contract"
        # heading — anything beyond is no longer "near the top", and we want to
        # catch a regression that pushes the trigger phrase past the Inputs
        # section.
        boundary = text.find("## Output Contract")
        assert boundary > 0, "executor/AGENTS.md must keep the Output Contract heading"
        inputs_section = text[:boundary]
        assert "criterion_source" in inputs_section and "behavioral" in inputs_section, (
            "executor/AGENTS.md Inputs section must reference the "
            "criterion_source: 'behavioral' trigger so the agent sees the "
            "contract before reading planner_output. If the phrase has moved "
            "past the Inputs section, the agent may miss it on a quick read."
        )


class TestReviewerAgentMdNewFields:
    """Stage G defensive symmetry: the reviewer AGENTS.md instructs the agent
    to populate criterion_source + criterion_id directly. The gate synthesis
    is the fallback when the agent leaves them off."""

    def test_reviewer_agents_md_documents_criterion_source_field(self):
        path = os.path.join(REPO_ROOT, "autodev", "agents", "reviewer", "AGENTS.md")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        assert "criterion_source" in text, (
            "reviewer/AGENTS.md must document criterion_source — without the "
            "doc, the agent never populates the field and the gate's "
            "defensive synthesis becomes the *only* source of the data. "
            "Stage G's defensive-symmetry rule requires both paths."
        )

    def test_reviewer_agents_md_documents_criterion_id_field(self):
        path = os.path.join(REPO_ROOT, "autodev", "agents", "reviewer", "AGENTS.md")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        assert "criterion_id" in text, (
            "reviewer/AGENTS.md must document criterion_id — the field "
            "carries the anchor (evidence index, test path, or PRD substring) "
            "that the executor traces back to on retry"
        )
