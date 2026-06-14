"""P0 Stage G — reviewer-gate behavioural blocking-issue synthesis.

When the reviewer records ``behavioral_verification.verdict ∈ {fail, cannot_verify}``
without populating ``blocking_issues``, the gate must synthesise one blocking issue
per evidence entry so the executor's self-heal context is never empty.

Two surfaces under test:

  - ``_synthesize_behavioral_blocking_issues(data)`` — mutates data in place.
    Idempotent. Respects a pre-populated ``blocking_issues`` list (the reviewer
    agent populated per AGENTS.md).
  - ``evaluate_reviewer`` — calls the helper on the behavioural-rejection path
    and persists the augmented payload back to ``reviewer_output.json``
    atomically (mkstemp + os.replace) so the orchestrator's downstream read
    sees the canonical list.

Mirrors the visual-verification suite in
``test_reviewer_gate_visual_verification.py`` and the behavioural-contract suite
in ``test_reviewer_gate_behavioral_verification.py``.
"""

import json
import os
from contextlib import ExitStack
from unittest.mock import patch

# Path wiring handled by conftest.py
import utils as utils_module
import reviewer_gate as reviewer_gate_module


def _patch_workspace(tmp_dir):
    """Return an ExitStack that redirects gate workspace paths to tmp_dir.

    Duplicated from ``test_reviewer_gate_behavioral_verification.py`` per the
    plan's instruction to NOT import private helpers across test files.
    """
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


def _write_current_phase_with_behavioral(workspace, raw_id="CORE-E1"):
    """Write current_phase.json with a populated Behavioral Verification block."""
    payload = {
        "phase_number": 1,
        "detail": f"Phase {raw_id}: test",
        "category": raw_id.split("-")[0],
        "raw_id": raw_id,
        "status": "PENDING",
        "exit_criteria": [],
        "behavioral_verification": {
            "user_observable": "User sees X.",
            "how_to_check": "Run script foo; expect non-empty stdout.",
            "failure_language": "The X view did not load.",
        },
    }
    with open(os.path.join(workspace, "current_phase.json"), "w") as f:
        json.dump(payload, f)


def _write_done_criteria_artifacts(workspace, raw_id="CORE-E1"):
    """Write phases/{id}.md + metrics.jsonl so the gate's pre-check passes."""
    phases_dir = os.path.join(workspace, "phases")
    os.makedirs(phases_dir, exist_ok=True)
    with open(os.path.join(phases_dir, f"{raw_id}.md"), "w") as f:
        f.write(f"# {raw_id} — smoke\n")
    with open(os.path.join(workspace, "metrics.jsonl"), "w") as f:
        f.write(json.dumps({"ts": "2026-05-22T00:00:00Z", "phase": raw_id, "goal": "x"}) + "\n")


def _make_evidence_entries(workspace, count=3):
    """Create N on-disk evidence files and return matching evidence dicts."""
    evidence_dir = os.path.join(workspace, "behavioral-smoke")
    os.makedirs(evidence_dir, exist_ok=True)
    entries = []
    for i in range(count):
        rel = f"behavioral-smoke/anchor-{i + 1}.txt"
        with open(os.path.join(workspace, rel), "w") as f:
            f.write(f"anchor-{i + 1}")
        entries.append({
            "claim": f"Claim number {i + 1}",
            "file_or_screenshot_or_log": rel,
            "method": "stdout_capture",
        })
    return entries


class TestSynthesizeBehavioralBlockingIssues:
    """Unit tests for ``_synthesize_behavioral_blocking_issues(data)``."""

    def test_synthesizes_one_blocking_issue_per_evidence_entry_on_fail(self, tmp_workspace):
        evidence = _make_evidence_entries(tmp_workspace, count=3)
        data = {
            "blocking_issues": [],
            "behavioral_verification": {
                "verdict": "fail",
                "how_to_check_followed": True,
                "evidence": evidence,
            },
        }
        reviewer_gate_module._synthesize_behavioral_blocking_issues(data)

        assert len(data["blocking_issues"]) == 3, (
            "one synthesised blocking issue per evidence entry — without this "
            "the executor's reviewer-rejection retry sees an empty list "
            "and the self-heal feedback loop carries no per-criterion signal"
        )
        for i, bi in enumerate(data["blocking_issues"]):
            assert bi["attribution"] == "impl", (
                "behavioural failures are implementation failures by definition — "
                "the artifact did not exhibit the claimed behaviour, so the "
                "executor (not the planner) owns the fix"
            )
            assert bi["criterion_source"] == "behavioral", (
                "criterion_source must label the anchor type so the executor's "
                "AGENTS.md trigger phrase fires"
            )
            assert bi["criterion_id"] == f"behavioral_evidence[{i}]", (
                "criterion_id must point at the evidence index so an operator "
                "can jq the original evidence claim from reviewer_output.json"
            )
            assert bi["description"] == evidence[i]["claim"], (
                "description should preserve the claim verbatim — the claim is "
                "what the executor needs to satisfy on retry"
            )
            assert bi["affected_file"] == evidence[i]["file_or_screenshot_or_log"], (
                "affected_file routes the executor's attention at the artifact "
                "the reviewer inspected"
            )

    def test_behavioral_synthesis_blanks_escaping_affected_file_on_fail(self, tmp_workspace):
        """An evidence path that escapes the workspace must be blanked in
        ``affected_file`` — not copied verbatim — while the blocking issue
        itself (its claim) is preserved.

        The pass-verdict validator's boundary check is skipped on ``fail`` (early
        return), so the synthesiser is the only guard against a traversal-shaped
        path flowing into failure_context.json. RED before the fix: line ~279
        copies ``"../../etc/passwd"`` straight into ``affected_file``. Mixing a
        safe entry in guards against over-blanking (the safe path must survive)."""
        safe = _make_evidence_entries(tmp_workspace, count=1)
        escaping = {
            "claim": "Traversal claim",
            "file_or_screenshot_or_log": "../../etc/passwd",
            "method": "stdout_capture",
        }
        data = {
            "blocking_issues": [],
            "behavioral_verification": {
                "verdict": "fail",
                "how_to_check_followed": True,
                "evidence": safe + [escaping],
            },
        }
        with _patch_workspace(tmp_workspace):
            reviewer_gate_module._synthesize_behavioral_blocking_issues(data)
        issues = data["blocking_issues"]
        assert len(issues) == 2, "one blocking issue per evidence entry, order preserved"
        assert issues[0]["affected_file"] == safe[0]["file_or_screenshot_or_log"], (
            "a safe in-workspace path must survive — the guard blanks only "
            "escaping paths, never legitimate evidence"
        )
        assert issues[1]["affected_file"] == "", (
            "an escaping path must be blanked, not propagated verbatim into "
            "failure_context.json via affected_file"
        )
        assert issues[1]["description"] == "Traversal claim", (
            "the blocking issue itself is preserved — only the unsafe path is "
            "dropped, so the executor still gets the self-heal claim"
        )

    def test_behavioral_synthesis_blanks_escaping_affected_file_on_cannot_verify(self, tmp_workspace):
        """Same boundary guard on the other non-pass verdict (cannot_verify),
        which the validator also skips. RED before the fix."""
        data = {
            "blocking_issues": [],
            "behavioral_verification": {
                "verdict": "cannot_verify",
                "how_to_check_followed": False,
                "evidence": [{
                    "claim": "Unverifiable claim",
                    "file_or_screenshot_or_log": "../../../etc/shadow",
                    "method": "stdout_capture",
                }],
            },
        }
        with _patch_workspace(tmp_workspace):
            reviewer_gate_module._synthesize_behavioral_blocking_issues(data)
        issues = data["blocking_issues"]
        assert len(issues) == 1
        assert issues[0]["affected_file"] == "", "escaping path blanked on cannot_verify too"
        assert issues[0]["description"] == "Unverifiable claim", "issue preserved"

    def test_synthesizes_on_cannot_verify_verdict(self, tmp_workspace):
        """cannot_verify is also a rejection signal — the reviewer could not
        complete the how_to_check procedure end-to-end. The synthesis still
        fires so the executor's retry has structured context."""
        evidence = _make_evidence_entries(tmp_workspace, count=2)
        data = {
            "blocking_issues": [],
            "behavioral_verification": {
                "verdict": "cannot_verify",
                "how_to_check_followed": False,
                "evidence": evidence,
            },
        }
        reviewer_gate_module._synthesize_behavioral_blocking_issues(data)
        assert len(data["blocking_issues"]) == 2
        assert all(bi["criterion_source"] == "behavioral" for bi in data["blocking_issues"])

    def test_does_not_synthesize_when_blocking_issues_already_populated(self, tmp_workspace):
        """If the reviewer agent populated ``blocking_issues`` directly (per
        AGENTS.md), the gate respects that list. The synthesis is a defensive
        fallback, not a rewrite — overwriting would lose information the agent
        put there deliberately."""
        evidence = _make_evidence_entries(tmp_workspace, count=3)
        agent_populated = [
            {"description": "agent wrote this", "attribution": "impl", "affected_file": "src/foo.py"}
        ]
        data = {
            "blocking_issues": list(agent_populated),
            "behavioral_verification": {
                "verdict": "fail",
                "how_to_check_followed": True,
                "evidence": evidence,
            },
        }
        reviewer_gate_module._synthesize_behavioral_blocking_issues(data)
        assert data["blocking_issues"] == agent_populated, (
            "agent-populated blocking_issues must survive the gate's defensive "
            "synthesis pass — the synthesis is for empty-list cases only"
        )

    def test_does_not_synthesize_on_pass_verdict(self, tmp_workspace):
        """A pass verdict with evidence should never produce blocking issues —
        that would invert the meaning of "pass". Legitimate code-quality
        problems flagged on a pass verdict come from elsewhere."""
        evidence = _make_evidence_entries(tmp_workspace, count=3)
        data = {
            "blocking_issues": [],
            "behavioral_verification": {
                "verdict": "pass",
                "how_to_check_followed": True,
                "evidence": evidence,
            },
        }
        reviewer_gate_module._synthesize_behavioral_blocking_issues(data)
        assert data["blocking_issues"] == [], (
            "synthesising blocking issues on a pass verdict would invert "
            "the meaning of the verdict — pass means no impl failures here"
        )

    def test_does_not_synthesize_when_evidence_missing(self, tmp_workspace):
        """Fail verdict with no evidence is a contract bug elsewhere (the gate's
        contract validator would have caught it on a pass verdict). The synthesis
        cannot invent claims — it returns without raising."""
        data = {
            "blocking_issues": [],
            "behavioral_verification": {
                "verdict": "fail",
                "how_to_check_followed": True,
                "evidence": [],
            },
        }
        reviewer_gate_module._synthesize_behavioral_blocking_issues(data)
        assert data["blocking_issues"] == []

        data_no_evidence_key = {
            "blocking_issues": [],
            "behavioral_verification": {
                "verdict": "fail",
                "how_to_check_followed": True,
            },
        }
        reviewer_gate_module._synthesize_behavioral_blocking_issues(data_no_evidence_key)
        assert data_no_evidence_key["blocking_issues"] == []

    def test_synthesize_idempotent_on_repeat_call(self, tmp_workspace):
        """Calling the helper twice on the same data must not double the
        list. After the first call, ``blocking_issues`` is no longer empty,
        so the second call exits early via the agent-populated guard."""
        evidence = _make_evidence_entries(tmp_workspace, count=3)
        data = {
            "blocking_issues": [],
            "behavioral_verification": {
                "verdict": "fail",
                "how_to_check_followed": True,
                "evidence": evidence,
            },
        }
        reviewer_gate_module._synthesize_behavioral_blocking_issues(data)
        first_pass = list(data["blocking_issues"])
        reviewer_gate_module._synthesize_behavioral_blocking_issues(data)
        assert data["blocking_issues"] == first_pass, (
            "idempotence — repeated synthesis must not duplicate. The second "
            "call sees a populated list and exits via the agent-populated guard."
        )


class TestEvaluateReviewerWritesBackSynthesizedIssues:
    """Integration tests for the disk write-back inside ``evaluate_reviewer``."""

    def test_evaluate_reviewer_persists_synthesized_blocking_issues_to_disk(
        self, tmp_workspace
    ):
        """Full gate run: behavioural fail + empty blocking_issues on disk.
        After ``evaluate_reviewer``, the on-disk ``reviewer_output.json`` must
        carry the synthesised list so the orchestrator's downstream read sees
        the canonical entries."""
        with _patch_workspace(tmp_workspace):
            _write_current_phase_with_behavioral(tmp_workspace, "CORE-E1")
            _write_done_criteria_artifacts(tmp_workspace, "CORE-E1")
            evidence = _make_evidence_entries(tmp_workspace, count=3)

            output_path = os.path.join(tmp_workspace, "reviewer_output.json")
            with open(output_path, "w") as f:
                json.dump({
                    "blocking_issues": [],
                    "integration_tests_passing": True,
                    "behavioral_verification": {
                        "verdict": "fail",
                        "how_to_check_followed": True,
                        "evidence": evidence,
                    },
                }, f)

            with open(os.path.join(tmp_workspace, "phase_state.json"), "w") as f:
                json.dump({"reviewer_retries": 0}, f)

            verdict = reviewer_gate_module.evaluate_reviewer(output_path)
            assert verdict == "ROUTE_EXECUTOR", (
                "fail verdict on pass 1 must route to executor for self-heal"
            )

            with open(output_path) as f:
                rewritten = json.load(f)
            assert len(rewritten["blocking_issues"]) == 3, (
                "evaluate_reviewer must persist the gate's synthesised "
                "blocking_issues back to reviewer_output.json — otherwise the "
                "orchestrator (which reads the file fresh) never sees the "
                "synthesised entries and the executor's self-heal context is "
                "empty in the very case Stage G is meant to fix"
            )
            assert all(bi["criterion_source"] == "behavioral" for bi in rewritten["blocking_issues"])

    def test_evaluate_reviewer_does_not_rewrite_when_blocking_issues_populated(
        self, tmp_workspace
    ):
        """Agent populated blocking_issues directly → on-disk file unchanged
        in the blocking_issues array. (Other fields may be unchanged or
        unaffected; this test pins specifically that the agent's list survives.)"""
        with _patch_workspace(tmp_workspace):
            _write_current_phase_with_behavioral(tmp_workspace, "CORE-E1")
            _write_done_criteria_artifacts(tmp_workspace, "CORE-E1")
            evidence = _make_evidence_entries(tmp_workspace, count=3)

            agent_populated = [
                {"description": "specific issue", "attribution": "impl", "affected_file": "src/x.py"}
            ]
            output_path = os.path.join(tmp_workspace, "reviewer_output.json")
            with open(output_path, "w") as f:
                json.dump({
                    "blocking_issues": list(agent_populated),
                    "integration_tests_passing": True,
                    "behavioral_verification": {
                        "verdict": "fail",
                        "how_to_check_followed": True,
                        "evidence": evidence,
                    },
                }, f)
            with open(os.path.join(tmp_workspace, "phase_state.json"), "w") as f:
                json.dump({"reviewer_retries": 0}, f)

            reviewer_gate_module.evaluate_reviewer(output_path)

            with open(output_path) as f:
                rewritten = json.load(f)
            assert rewritten["blocking_issues"] == agent_populated, (
                "agent-populated blocking_issues must NOT be overwritten by "
                "the gate's defensive synthesis"
            )

    def test_write_back_uses_atomic_rename(self, tmp_workspace):
        """The synthesis write-back must use os.replace (atomic rename), not
        a direct write. A crash mid-write would otherwise leave the file
        truncated and the orchestrator's read would fail downstream."""
        with _patch_workspace(tmp_workspace):
            _write_current_phase_with_behavioral(tmp_workspace, "CORE-E1")
            _write_done_criteria_artifacts(tmp_workspace, "CORE-E1")
            evidence = _make_evidence_entries(tmp_workspace, count=3)

            output_path = os.path.join(tmp_workspace, "reviewer_output.json")
            with open(output_path, "w") as f:
                json.dump({
                    "blocking_issues": [],
                    "integration_tests_passing": True,
                    "behavioral_verification": {
                        "verdict": "fail",
                        "how_to_check_followed": True,
                        "evidence": evidence,
                    },
                }, f)
            with open(os.path.join(tmp_workspace, "phase_state.json"), "w") as f:
                json.dump({"reviewer_retries": 0}, f)

            calls = []
            original = os.replace

            def tracking_replace(src, dst):
                calls.append((src, dst))
                return original(src, dst)

            with patch("os.replace", side_effect=tracking_replace):
                reviewer_gate_module.evaluate_reviewer(output_path)

            replace_to_output = [c for c in calls if c[1] == output_path]
            assert replace_to_output, (
                "synthesis write-back must use os.replace targeting the "
                "reviewer_output.json path — direct file.write leaves a "
                "window where a crash truncates the file"
            )
