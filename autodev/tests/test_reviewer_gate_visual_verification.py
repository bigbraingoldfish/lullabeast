"""
Reviewer gate — visual verification check on UI/INT phases.

The reviewer must produce a `visual_verification` field set to "pass" with a
non-empty `visual_smoke_artifacts` list on phases that produce user-visible
output. Without this, a text-only model could rubber-stamp a visually broken
phase (see solitaire post-mortem, plans/Active/solitaire-postmortem.md).

This check applies on phases whose raw_id begins with one of the configured
visual subsystem prefixes (UI, INT) or matches an explicit allowlist
(CORE-E4 = the DOM renderer).

A missing or wrong field does NOT consume reviewer_retries — like
ERR_MISSING_ARTIFACTS, it triggers a re-invocation with a specific instruction
to produce the artifact.

FIND-ID: FIND-VISUAL-VERIFICATION
Spec Reference: solitaire post-mortem Step 10 (reviewer gate additions)
"""

import json
import os
import sys
from contextlib import ExitStack
from unittest.mock import patch

import pytest

# Path wiring handled by conftest.py
import utils as utils_module
import reviewer_gate as reviewer_gate_module


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


def _write_current_phase(workspace, raw_id):
    """Write current_phase.json with the given raw_id."""
    with open(os.path.join(workspace, "current_phase.json"), "w") as f:
        json.dump({"raw_id": raw_id, "detail": "test", "phase_number": 1}, f)


def _write_phase_state(workspace, **kwargs):
    """Write phase_state.json. Defaults: 0 retries across the board."""
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
    metrics_path = os.path.join(workspace, "metrics.jsonl")
    with open(metrics_path, "w") as f:
        f.write(json.dumps({"phase": raw_id, "ts": "2026-05-13T00:00:00Z"}) + "\n")


def _reviewer_output(**overrides):
    """A base passing reviewer output. Override fields per test.

    Note: ``phase_intent_validated`` was removed in P0 Stage F — the gate no
    longer reads that boolean. Visual-phase tests in this file do not write a
    ``behavioral_verification`` block to ``current_phase.json``, so the gate's
    new behavioral check is content-driven False and these tests stay focused
    on visual semantics."""
    out = {
        "blocking_issues": [],
        "suggestions": [],
        "integration_tests_passing": True,
    }
    out.update(overrides)
    return out


class TestVisualPhaseDetection:
    """The gate must identify which phases require visual verification."""

    def test_ui_prefix_is_visual(self):
        assert reviewer_gate_module._is_visual_phase("UI-E1") is True
        assert reviewer_gate_module._is_visual_phase("UI-E2") is True
        assert reviewer_gate_module._is_visual_phase("ui-e1") is True  # case-insensitive

    def test_int_prefix_is_visual(self):
        assert reviewer_gate_module._is_visual_phase("INT-E1") is True

    def test_non_ui_int_prefix_is_not_visual_by_default(self):
        """Any prefix that isn't UI or INT is not visual by default — even if
        a particular project has a visual phase there (e.g. CORE-E4 in the
        solitaire roadmap). Such projects can extend via env var (covered in
        a separate test)."""
        for raw_id in ("CORE-E1", "CORE-E4", "DATA-E1", "AUDIO-E2",
                       "INFRA-E1", "GAME-E1", "TEST-E1", "API-E2"):
            assert reviewer_gate_module._is_visual_phase(raw_id) is False, (
                f"{raw_id} should not be classified as a visual phase by default"
            )

    def test_env_var_extends_visual_phase_set(self, monkeypatch):
        """Operators can extend the visual-phase set for project-specific
        phases via AUTODEV_VISUAL_PHASE_RAW_IDS (comma-separated)."""
        monkeypatch.setenv("AUTODEV_VISUAL_PHASE_RAW_IDS", "CORE-E4,SETUP-E2")
        assert reviewer_gate_module._is_visual_phase("CORE-E4") is True
        assert reviewer_gate_module._is_visual_phase("setup-e2") is True  # case-insensitive
        # Still respects the UI/INT prefix rule
        assert reviewer_gate_module._is_visual_phase("UI-E1") is True
        # And doesn't accidentally match unrelated phases
        assert reviewer_gate_module._is_visual_phase("CORE-E1") is False

    def test_env_var_unset_means_only_prefix_rule(self, monkeypatch):
        monkeypatch.delenv("AUTODEV_VISUAL_PHASE_RAW_IDS", raising=False)
        assert reviewer_gate_module._is_visual_phase("CORE-E4") is False
        assert reviewer_gate_module._is_visual_phase("UI-E1") is True

    def test_empty_raw_id_is_not_visual(self):
        assert reviewer_gate_module._is_visual_phase("") is False
        assert reviewer_gate_module._is_visual_phase(None) is False


class TestVisualVerificationRequiredOnUiPhases:
    """On visual phases, the reviewer must set visual_verification + artifacts."""

    def test_missing_visual_verification_field_triggers_unverified(self, tmp_workspace):
        """UI phase + reviewer output with no visual_verification field → ERR_VISUAL_UNVERIFIED."""
        _write_current_phase(tmp_workspace, "UI-E1")
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "UI-E1")
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(), f)  # no visual_verification field

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        assert result == "VISUAL_UNVERIFIED", (
            f"Expected VISUAL_UNVERIFIED for missing visual_verification on UI phase, got {result!r}"
        )

    def test_visual_verification_pass_with_artifacts_allows_pass(self, tmp_workspace):
        """Valid visual_verification=pass with ≥1 artifact path → normal PASS routing."""
        _write_current_phase(tmp_workspace, "UI-E1")
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "UI-E1")

        # Create a fake screenshot file so the artifact-existence check passes.
        screenshot_path = os.path.join(tmp_workspace, "visual-smoke", "UI-E1-default.png")
        os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
        with open(screenshot_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")  # PNG magic header

        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(
                visual_verification="pass",
                visual_smoke_artifacts=[
                    {"path": "visual-smoke/UI-E1-default.png", "description": "card faces show suit glyphs"}
                ],
            ), f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        assert result == "PASS"

    def test_visual_verification_fail_routes_as_rejection(self, tmp_workspace):
        """visual_verification=fail → treat as a blocking issue → reviewer rejection routing."""
        _write_current_phase(tmp_workspace, "UI-E1")
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "UI-E1")

        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        # Reviewer set visual_verification to "fail" but left blocking_issues empty
        # by mistake. The gate must still treat this as a rejection.
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(
                visual_verification="fail",
                visual_smoke_artifacts=[
                    {"path": "visual-smoke/UI-E1-default.png",
                     "description": "card faces render as raw text 10Spades"}
                ],
            ), f)

        # Pre-create the artifact so existence check is independent of verdict
        screenshot_path = os.path.join(tmp_workspace, "visual-smoke", "UI-E1-default.png")
        os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
        with open(screenshot_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        # Pass 1: rejection routes to executor.
        assert result == "ROUTE_EXECUTOR", (
            f"visual_verification=fail must route as a rejection, got {result!r}"
        )

    def test_visual_verification_cannot_verify_routes_as_rejection(self, tmp_workspace):
        """visual_verification=cannot_verify (server didn't boot, tool unavailable) → rejection routing."""
        _write_current_phase(tmp_workspace, "UI-E1")
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "UI-E1")
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(
                visual_verification="cannot_verify",
                visual_smoke_artifacts=[],
            ), f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        assert result == "ROUTE_EXECUTOR"

    def test_missing_artifacts_list_triggers_unverified(self, tmp_workspace):
        """visual_verification=pass but no visual_smoke_artifacts → VISUAL_UNVERIFIED."""
        _write_current_phase(tmp_workspace, "UI-E1")
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "UI-E1")
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(visual_verification="pass"), f)  # no artifacts

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        assert result == "VISUAL_UNVERIFIED"

    def test_empty_artifacts_list_triggers_unverified(self, tmp_workspace):
        _write_current_phase(tmp_workspace, "UI-E1")
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "UI-E1")
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(
                visual_verification="pass",
                visual_smoke_artifacts=[],
            ), f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        assert result == "VISUAL_UNVERIFIED"

    def test_artifact_path_must_exist_on_disk(self, tmp_workspace):
        """If reviewer claims an artifact path but it does not exist → VISUAL_UNVERIFIED."""
        _write_current_phase(tmp_workspace, "UI-E1")
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "UI-E1")
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(
                visual_verification="pass",
                visual_smoke_artifacts=[
                    {"path": "visual-smoke/does-not-exist.png", "description": "ghost"}
                ],
            ), f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)

        assert result == "VISUAL_UNVERIFIED"

    def test_visual_unverified_does_not_consume_reviewer_retries(self, tmp_workspace):
        """Like ERR_MISSING_ARTIFACTS, VISUAL_UNVERIFIED is a re-invocation, not a rejection."""
        _write_current_phase(tmp_workspace, "UI-E1")
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "UI-E1")
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(), f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "VISUAL_UNVERIFIED"

        with open(os.path.join(tmp_workspace, "phase_state.json")) as f:
            state = json.load(f)
        assert state.get("reviewer_retries", 0) == 0
        assert state.get("last_error_code") == "ERR_VISUAL_UNVERIFIED"


class TestVisualVerificationNotRequiredOnNonVisualPhases:
    """On CORE/DATA/AUDIO/etc phases, the gate must NOT require visual fields."""

    def test_core_e1_passes_without_visual_verification(self, tmp_workspace):
        _write_current_phase(tmp_workspace, "CORE-E1")
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "CORE-E1")
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(), f)  # no visual_verification at all

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "PASS"

    def test_data_e1_passes_without_visual_verification(self, tmp_workspace):
        _write_current_phase(tmp_workspace, "DATA-E1")
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "DATA-E1")
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(), f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "PASS"


class TestVisualVerificationDoesNotBreakExistingChecks:
    """The new gate logic must not affect existing CONTRACT_FAILURE / MISSING_ARTIFACTS paths."""

    def test_missing_artifacts_still_takes_precedence(self, tmp_workspace):
        """If phase archive is missing, MISSING_ARTIFACTS wins over visual check."""
        _write_current_phase(tmp_workspace, "UI-E1")
        _write_phase_state(tmp_workspace)
        # Deliberately do NOT call _write_done_artifacts — phase archive missing.
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        with open(output_path, "w") as f:
            json.dump(_reviewer_output(visual_verification="pass",
                                      visual_smoke_artifacts=[{"path": "x", "description": "y"}]), f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "MISSING_ARTIFACTS"

    def test_existing_blocking_issues_still_route_as_rejection(self, tmp_workspace):
        """A normal blocking_issues rejection on a UI phase still routes correctly."""
        _write_current_phase(tmp_workspace, "UI-E1")
        _write_phase_state(tmp_workspace)
        _write_done_artifacts(tmp_workspace, "UI-E1")
        output_path = os.path.join(tmp_workspace, "reviewer_output.json")
        # Reviewer found a real code issue AND ran visual verification
        screenshot_path = os.path.join(tmp_workspace, "visual-smoke", "UI-E1-default.png")
        os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
        with open(screenshot_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        with open(output_path, "w") as f:
            json.dump({
                "blocking_issues": [
                    {"description": "Unused import", "attribution": "impl",
                     "affected_file": "src/x.js"}
                ],
                "suggestions": [],
                "integration_tests_passing": True,
                "visual_verification": "pass",
                "visual_smoke_artifacts": [
                    {"path": "visual-smoke/UI-E1-default.png", "description": "ok"}
                ],
            }, f)

        with _patch_workspace(tmp_workspace):
            result = reviewer_gate_module.evaluate_reviewer(output_path)
        assert result == "ROUTE_EXECUTOR"  # pass 1 rejection routing
