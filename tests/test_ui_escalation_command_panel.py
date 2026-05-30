"""
Static-lint tests for the shared EscalationCommandPanel (P1 Stage G3).

History: this file previously also held "pure-logic" test classes
(TestEscalationCommandPanel, TestEscalationCommandPanelIntegration,
TestConfirmationModal, TestResetCapEnforcement) that re-implemented the panel's JS
logic in Python and asserted against the re-implementation. They never read
ui/index.html, so they were fake coverage — they passed no matter what the real
component did. P1 Stage G3 changed exactly that logic (render gating, modal triggers,
dispatch), so those classes were removed rather than left as false confidence. The
GRID_COMMANDS label class was also removed: after consolidation the only button-label
source is ESCALATION_CMD_DEFS (covered by TestEscalationCmdDefsLabels below), so the
GRID class was a duplicate. The QueueActionHub visibility class was removed because
that logic now lives in the single shared panel (covered by TestGroupedLayoutStructure
and tests/test_ui_escalation_panel_shared.py).

The tests that remain are real static-lint guards: they read ui/index.html and assert
against its source.
"""

import re
from pathlib import Path

INDEX_HTML = Path(__file__).parent.parent / "ui" / "index.html"


def _read_confirmation_modal_block():
    """Extract the ConfirmationModal function source text from index.html."""
    source = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(
        r"function ConfirmationModal\(\{.*?\}\)(.*?)function StopConfirmModal",
        source, re.DOTALL
    )
    assert m, "ConfirmationModal not found in index.html"
    return m.group(0)


def _read_handle_command_block():
    """Extract the handleCommand function body from the shared EscalationCommandPanel."""
    source = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(
        r"// ─── EscalationCommandPanel.*?const handleCommand\s*=\s*\(command\)\s*=>\s*\{(.*?)\};",
        source, re.DOTALL
    )
    assert m, "handleCommand in EscalationCommandPanel not found in index.html"
    # group(1) is the function body only — excludes the useState lines above it.
    return m.group(1)


def _read_escalation_cmds_block():
    """Extract the ESCALATION_CMD_DEFS array source text (the single button source)."""
    source = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(r"const ESCALATION_CMD_DEFS\s*=\s*\[(.*?)\];", source, re.DOTALL)
    assert m, "ESCALATION_CMD_DEFS not found in index.html"
    return m.group(0)


def _read_escalation_panel_block():
    """Extract the full EscalationCommandPanel function source text from index.html."""
    source = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(
        r"// ─── EscalationCommandPanel.*?(?=// ─── PipelineCompletePanel)",
        source,
        re.DOTALL,
    )
    assert m, "EscalationCommandPanel block not found"
    return m.group(0)


# ──────────────────────────────────────────────────────────────────────────────
# ConfirmationModal CTAs and body text (component unchanged by G3)
# ──────────────────────────────────────────────────────────────────────────────

class TestConfirmationModalCTAs:
    """ConfirmationModal must use verb-matched CTAs per command."""

    def test_reset_phase_cta_is_reset_phase(self):
        block = _read_confirmation_modal_block()
        assert re.search(r'cta\s*=\s*["\']Reset Phase["\']', block), \
            "ConfirmationModal RESET_PHASE branch does not set cta = 'Reset Phase'"

    def test_reset_execution_cta_is_reset_execution(self):
        block = _read_confirmation_modal_block()
        assert re.search(r'cta\s*=\s*["\']Reset Execution["\']', block), \
            "ConfirmationModal RESET_EXECUTION branch does not set cta = 'Reset Execution'"

    def test_reset_reviewer_cta_is_rerun_reviewer(self):
        block = _read_confirmation_modal_block()
        assert re.search(r'cta\s*=\s*["\']Re-run Reviewer["\']', block), \
            "ConfirmationModal RESET_REVIEWER branch does not set cta = 'Re-run Reviewer'"

    def test_skip_cta_is_abandon_phase(self):
        block = _read_confirmation_modal_block()
        assert re.search(r'cta\s*=\s*["\']Abandon Phase["\']', block), \
            "ConfirmationModal SKIP branch does not set cta = 'Abandon Phase'"

    def test_confirm_button_renders_cta_variable(self):
        block = _read_confirmation_modal_block()
        assert re.search(r'\{busy\s*\?\s*["\']Sending', block), \
            "ConfirmationModal button does not use busy ternary"
        assert re.search(r':\s*cta\s*\}', block), \
            "ConfirmationModal button does not render {cta} as the non-busy label"

    def test_stop_confirm_modal_still_says_stop_pipeline(self):
        source = INDEX_HTML.read_text(encoding="utf-8")
        m = re.search(
            r"function StopConfirmModal\(\{.*?\}\)(.*?)(?=function \w)",
            source, re.DOTALL
        )
        assert m, "StopConfirmModal not found"
        block = m.group(0)
        assert "Stop Pipeline" in block, \
            "StopConfirmModal no longer says 'Stop Pipeline'"

    def test_reset_phase_body_mentions_cannot_be_undone(self):
        block = _read_confirmation_modal_block()
        assert "cannot be undone from the UI" in block, \
            "RESET_PHASE modal body does not mention 'cannot be undone from the UI'"

    def test_skip_body_mentions_not_cleaned_up(self):
        block = _read_confirmation_modal_block()
        assert "will NOT be cleaned up" in block, \
            "SKIP modal body does not mention 'will NOT be cleaned up'"

    def test_reset_execution_body_mentions_planner_preserved(self):
        block = _read_confirmation_modal_block()
        assert "Planner output is preserved" in block, \
            "RESET_EXECUTION modal body does not mention 'Planner output is preserved'"


# ──────────────────────────────────────────────────────────────────────────────
# handleCommand (post-G3: opens a modal for EVERY command, no allowlist)
# ──────────────────────────────────────────────────────────────────────────────

class TestEscalationPanelHandleCommand:
    """Post-G3, handleCommand opens a confirmation modal for every command uniformly;
    the modal kind (StopConfirmModal vs ConfirmationModal) is chosen at render time on
    `modalCommand === 'STOP'`. There is no per-command allowlist and no STOP special-case."""

    def test_handle_command_opens_modal_for_all_commands(self):
        block = _read_handle_command_block()
        assert "setShowConfirmModal(true)" in block, \
            "handleCommand must open the confirmation modal"
        assert "setModalCommand(command)" in block, \
            "handleCommand must record which command is pending confirmation"

    def test_handle_command_has_no_command_allowlist(self):
        """No per-command branching survives — every command is modal-gated the same way."""
        block = _read_handle_command_block()
        for cmd in ("RESET_PHASE", "RESET_EXECUTION", "RESET_REVIEWER", "PROCEED", "SKIP"):
            assert cmd not in block, \
                f"handleCommand still special-cases {cmd}; post-G3 it must be allowlist-free"

    def test_stop_not_special_cased_in_handle_command(self):
        block = _read_handle_command_block()
        assert not re.search(r'=== ["\']STOP["\']', block), \
            "handleCommand must not special-case STOP (StopConfirmModal is chosen at render time)"


# ──────────────────────────────────────────────────────────────────────────────
# ESCALATION_CMD_DEFS labels (the single button-label source for BOTH views)
# ──────────────────────────────────────────────────────────────────────────────

class TestEscalationCmdDefsLabels:
    """ESCALATION_CMD_DEFS carries the renamed labels consumed by the shared panel."""

    def test_contains_reset_phase(self):
        block = _read_escalation_cmds_block()
        assert '"Reset Phase"' in block or "'Reset Phase'" in block

    def test_contains_reset_execution(self):
        block = _read_escalation_cmds_block()
        assert '"Reset Execution"' in block or "'Reset Execution'" in block

    def test_contains_rerun_reviewer(self):
        block = _read_escalation_cmds_block()
        assert '"Re-run Reviewer"' in block or "'Re-run Reviewer'" in block

    def test_contains_abandon_phase(self):
        block = _read_escalation_cmds_block()
        assert '"Abandon Phase"' in block or "'Abandon Phase'" in block

    def test_contains_mark_complete(self):
        block = _read_escalation_cmds_block()
        assert '"Mark Complete"' in block or "'Mark Complete'" in block

    def test_contains_stop_pipeline(self):
        block = _read_escalation_cmds_block()
        assert '"Stop Pipeline"' in block or "'Stop Pipeline'" in block

    def test_no_old_labels(self):
        block = _read_escalation_cmds_block()
        assert "Revert to Planner" not in block, "ESCALATION_CMD_DEFS still has 'Revert to Planner'"
        assert "Revert to Executor" not in block, "ESCALATION_CMD_DEFS still has 'Revert to Executor'"
        assert "Revert to Reviewer" not in block, "ESCALATION_CMD_DEFS still has 'Revert to Reviewer'"
        assert "Accept & Advance" not in block, "ESCALATION_CMD_DEFS still has 'Accept & Advance'"
        assert "Skip Phase" not in block, "ESCALATION_CMD_DEFS still has 'Skip Phase'"
        assert "Halt Pipeline" not in block, "ESCALATION_CMD_DEFS still has 'Halt Pipeline'"


# ──────────────────────────────────────────────────────────────────────────────
# Re-run Reviewer disabled when no executor output (shared panel + both call sites)
# ──────────────────────────────────────────────────────────────────────────────

class TestRerunReviewerDisableCondition:
    """RESET_REVIEWER is filtered out when executor_output_exists is false."""

    def test_panel_accepts_executor_output_exists_prop(self):
        source = INDEX_HTML.read_text(encoding="utf-8")
        panel_m = re.search(r"function EscalationCommandPanel\(\{(.*?)\}\)", source, re.DOTALL)
        assert panel_m, "EscalationCommandPanel signature not found"
        assert "executor_output_exists" in panel_m.group(1), \
            "EscalationCommandPanel does not accept executor_output_exists prop"

    def test_reset_reviewer_disable_condition_uses_executor_output_exists(self):
        source = INDEX_HTML.read_text(encoding="utf-8")
        assert re.search(
            r"RESET_REVIEWER.*executor_output_exists|executor_output_exists.*RESET_REVIEWER",
            source, re.DOTALL
        ), "panel does not reference executor_output_exists for RESET_REVIEWER filtering"

    def test_monitor_passes_executor_output_exists_prop(self):
        source = INDEX_HTML.read_text(encoding="utf-8")
        assert re.search(r"executor_output_exists=\{pState\.executor_output_exists", source), \
            "Pipeline Monitor does not pass executor_output_exists from pState"

    def test_queue_passes_executor_output_exists_prop(self):
        source = INDEX_HTML.read_text(encoding="utf-8")
        assert re.search(r"executor_output_exists=\{hubExecutorOutputExists", source), \
            "Queue view does not pass executor_output_exists to the shared component"


# ──────────────────────────────────────────────────────────────────────────────
# Grouped layout structure (shared panel)
# ──────────────────────────────────────────────────────────────────────────────

class TestGroupedLayoutStructure:
    """The shared EscalationCommandPanel uses the grouped layout."""

    def test_panel_has_recover_group_label(self):
        block = _read_escalation_panel_block()
        assert "Recover" in block, "panel does not render a 'Recover' group label"

    def test_panel_has_advance_manually_group_label(self):
        block = _read_escalation_panel_block()
        assert "Advance manually" in block, "panel does not render 'Advance manually' group label"

    def test_panel_renders_stop_def(self):
        """Stop is rendered from the ESCALATION_CMD_DEFS group:'stop' entry (its label
        literal now lives in the DEFS, not hard-coded in the panel)."""
        block = _read_escalation_panel_block()
        assert "stopDef" in block, \
            "panel does not render the stop command via the ESCALATION_CMD_DEFS stop def"

    def test_buttons_sourced_from_escalation_cmd_defs(self):
        block = _read_escalation_panel_block()
        assert "ESCALATION_CMD_DEFS" in block, \
            "panel buttons must be sourced from ESCALATION_CMD_DEFS (single source of truth)"

    def test_no_hardcoded_button_copy_in_panel(self):
        """Per-button copy moved into ESCALATION_CMD_DEFS (defined before the panel block)."""
        block = _read_escalation_panel_block()
        assert "Deletes branch · wipes all artifacts · reruns from top." not in block, \
            "per-button copy must live in ESCALATION_CMD_DEFS, not hard-coded in the panel"

    def test_cap_reached_notice_present_in_source(self):
        block = _read_escalation_panel_block()
        assert re.search(r"reset budget", block, re.IGNORECASE), \
            "Cap-reached status message ('reset budget') not found in EscalationCommandPanel"

    def test_info_group_label_component_exists(self):
        source = INDEX_HTML.read_text(encoding="utf-8")
        assert re.search(r"function InfoGroupLabel", source), \
            "InfoGroupLabel component not defined in index.html"

    def test_recover_group_uses_info_group_label(self):
        block = _read_escalation_panel_block()
        assert re.search(r"InfoGroupLabel", block), "EscalationCommandPanel does not use InfoGroupLabel"

    def test_rerun_reviewer_conditional_on_show_rerun_reviewer(self):
        block = _read_escalation_panel_block()
        assert re.search(
            r"showRerunReviewer|executor_output_exists.*RESET_REVIEWER|RESET_REVIEWER.*executor_output_exists",
            block, re.DOTALL,
        ), "Re-run Reviewer visibility is not conditional on executor_output_exists / showRerunReviewer"

    def test_mark_complete_conditional_on_show_mark_complete(self):
        block = _read_escalation_panel_block()
        assert re.search(
            r"showMarkComplete|merge_probe_passed.*PROCEED|PROCEED.*merge_probe_passed",
            block, re.DOTALL,
        ), "Mark Complete visibility is not conditional on merge_probe_passed / showMarkComplete"

    def test_cap_reached_hides_recover_group(self):
        block = _read_escalation_panel_block()
        assert re.search(r"capReached", block), "capReached variable not found in EscalationCommandPanel"

    def test_new_props_in_function_signature(self):
        source = INDEX_HTML.read_text(encoding="utf-8")
        panel_m = re.search(r"function EscalationCommandPanel\(\{(.*?)\}\)", source, re.DOTALL)
        assert panel_m, "EscalationCommandPanel signature not found"
        sig = panel_m.group(1)
        assert "planner_output_exists" in sig, "EscalationCommandPanel missing planner_output_exists prop"
        assert "phase_branch_exists" in sig, "EscalationCommandPanel missing phase_branch_exists prop"
        assert "merge_probe_passed" in sig, "EscalationCommandPanel missing merge_probe_passed prop"

    def test_g3_control_props_in_function_signature(self):
        """The shared component exposes the G3 control props that prop-gate the Queue."""
        source = INDEX_HTML.read_text(encoding="utf-8")
        panel_m = re.search(r"function EscalationCommandPanel\(\{(.*?)\}\)", source, re.DOTALL)
        assert panel_m, "EscalationCommandPanel signature not found"
        sig = panel_m.group(1)
        for prop in ("targetProjectPath", "onDispatched", "showOrchestratorLifecycle", "showCommandSentScreen"):
            assert prop in sig, f"EscalationCommandPanel missing G3 control prop: {prop}"

    def test_parent_passes_phase_2_props(self):
        source = INDEX_HTML.read_text(encoding="utf-8")
        assert re.search(r"planner_output_exists=\{pState\.planner_output_exists", source), \
            "Parent missing planner_output_exists prop"
        assert re.search(r"phase_branch_exists=\{pState\.phase_branch_exists", source), \
            "Parent missing phase_branch_exists prop"
        assert re.search(r"merge_probe_passed=\{pState\.merge_probe_passed", source), \
            "Parent missing merge_probe_passed prop"


# ──────────────────────────────────────────────────────────────────────────────
# Advisory block behaviour (shared panel)
# ──────────────────────────────────────────────────────────────────────────────

class TestAdvisoryBlock:
    """Collapsible advisory block must be present and correctly wired."""

    def test_advisory_expanded_state_wired_to_escalation_resets(self):
        block = _read_escalation_panel_block()
        assert re.search(
            r"advisoryExpanded.*escalation_resets\s*===\s*0|escalation_resets\s*===\s*0.*advisoryExpanded",
            block, re.DOTALL,
        ), "advisoryExpanded initial state not wired to escalation_resets === 0"

    def test_advisory_block_has_toggle(self):
        block = _read_escalation_panel_block()
        assert re.search(r"setAdvisoryExpanded|advisoryExpanded", block), \
            "Advisory block toggle (advisoryExpanded state) not found"

    def test_advisory_uses_advisory_text(self):
        block = _read_escalation_panel_block()
        assert "advisoryText" in block, "Advisory text variable not found in EscalationCommandPanel"

    def test_advisory_counter_strip_present(self):
        block = _read_escalation_panel_block()
        assert re.search(r"Escalation loops", block), \
            "Counter strip 'Escalation loops' not found in EscalationCommandPanel"

    def test_advisory_prefix_label(self):
        block = _read_escalation_panel_block()
        assert re.search(r"Advisory", block), "Advisory prefix label not found in EscalationCommandPanel"
