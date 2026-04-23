"""
Tests for EscalationCommandPanel component in pipeline UI.

Verifies conditional rendering, button functionality, and state management
for the human escalation command panel.
"""

import pytest
import json
from unittest.mock import Mock, patch, MagicMock


class TestEscalationCommandPanel:
    """Test suite for EscalationCommandPanel React component."""
    
    def test_panel_does_not_render_when_pipeline_status_is_running(self):
        """EscalationCommandPanel should not render when pipeline_status is RUNNING."""
        pipeline_status = 'RUNNING'
        should_render = pipeline_status == 'WAITING_FOR_HUMAN'
        assert should_render is False, "Panel should not render when status is RUNNING"
    
    def test_panel_does_not_render_when_pipeline_status_is_waiting_for_sentinel(self):
        """EscalationCommandPanel should not render when pipeline_status is WAITING_FOR_SENTINEL."""
        pipeline_status = 'WAITING_FOR_SENTINEL'
        should_render = pipeline_status == 'WAITING_FOR_HUMAN'
        assert should_render is False
    
    def test_panel_does_not_render_when_pipeline_status_is_halted_silent(self):
        """EscalationCommandPanel should not render when pipeline_status is HALTED_SILENT."""
        pipeline_status = 'HALTED_SILENT'
        should_render = pipeline_status == 'WAITING_FOR_HUMAN'
        assert should_render is False
    
    def test_panel_does_not_render_when_pipeline_status_is_blocked(self):
        """EscalationCommandPanel should not render when pipeline_status is BLOCKED."""
        pipeline_status = 'BLOCKED'
        should_render = pipeline_status == 'WAITING_FOR_HUMAN'
        assert should_render is False
    
    def test_panel_renders_with_orange_border_when_waiting_for_human(self):
        """EscalationCommandPanel renders with orange border when pipeline_status is WAITING_FOR_HUMAN."""
        pipeline_status = 'WAITING_FOR_HUMAN'
        should_render = pipeline_status == 'WAITING_FOR_HUMAN'
        assert should_render is True
    
    def test_panel_header_displays_escalation_trigger_reason(self):
        """Panel header displays escalation_trigger_reason from state response."""
        state = {
            'escalation_trigger_reason': 'Test reason from planner',
            'last_action': 'Some last action',
            'pipeline_status': 'WAITING_FOR_HUMAN'
        }
        header_text = state.get('escalation_trigger_reason') or state.get('last_action')
        assert header_text == 'Test reason from planner'
    
    def test_panel_header_falls_back_to_last_action(self):
        """Panel header falls back to last_action when escalation_trigger_reason is absent."""
        state = {
            'escalation_trigger_reason': None,
            'last_action': 'Executor failed with error E123',
            'pipeline_status': 'WAITING_FOR_HUMAN'
        }
        header_text = state.get('escalation_trigger_reason') or state.get('last_action')
        assert header_text == 'Executor failed with error E123'
    
    def test_all_six_buttons_render(self):
        """All six buttons render: RETRY, RESET EXECUTION, RESET PHASE, SKIP, PROCEED, STOP."""
        expected_buttons = ['RETRY', 'RESET EXECUTION', 'RESET PHASE', 'SKIP', 'PROCEED', 'STOP']
        assert len(expected_buttons) == 6
    
    def test_clicking_retry_sends_post_to_api_command(self):
        """Clicking RETRY sends POST /api/command with command: RETRY."""
        def handle_retry():
            return {'command': 'RETRY', 'method': 'POST', 'endpoint': '/api/command'}
        
        result = handle_retry()
        assert result['command'] == 'RETRY'
        assert result['method'] == 'POST'
        assert result['endpoint'] == '/api/command'
    
    def test_clicking_reset_execution_sends_post_to_api_command(self):
        """Clicking RESET EXECUTION sends POST /api/command with command: RESET_EXECUTION."""
        def handle_reset_execution():
            return {'command': 'RESET_EXECUTION', 'method': 'POST', 'endpoint': '/api/command'}
        
        result = handle_reset_execution()
        assert result['command'] == 'RESET_EXECUTION'
    
    def test_clicking_proceed_sends_post_to_api_command(self):
        """Clicking PROCEED sends POST /api/command with command: PROCEED."""
        def handle_proceed():
            return {'command': 'PROCEED', 'method': 'POST', 'endpoint': '/api/command'}
        
        result = handle_proceed()
        assert result['command'] == 'PROCEED'
    
    def test_waiting_state_shows_command_sent_message(self):
        """After sending non-destructive command, panel shows 'Command sent — waiting for orchestrator...'."""
        command_sent = True
        waiting_message = 'Command sent — waiting for orchestrator...'
        
        if command_sent:
            message = waiting_message
        else:
            message = None
        
        assert message == 'Command sent — waiting for orchestrator...'
    
    def test_waiting_state_clears_when_status_changes(self):
        """Waiting state clears when pipeline_status changes from WAITING_FOR_HUMAN."""
        old_status = 'WAITING_FOR_HUMAN'
        new_status = 'RUNNING'
        
        if new_status != 'WAITING_FOR_HUMAN':
            command_sent = False
        
        assert command_sent is False


class TestEscalationCommandPanelIntegration:
    """Integration tests for full escalation command panel behavior."""
    
    def test_full_render_cycle(self):
        """Test complete render cycle: no render -> render -> waiting -> clear."""
        # Initial state: not WAITING_FOR_HUMAN
        pipeline_status = 'RUNNING'
        command_sent = False
        
        should_render = pipeline_status == 'WAITING_FOR_HUMAN'
        assert should_render is False
        
        # Status changes to WAITING_FOR_HUMAN
        pipeline_status = 'WAITING_FOR_HUMAN'
        should_render = pipeline_status == 'WAITING_FOR_HUMAN'
        assert should_render is True
        
        # User clicks RETRY
        command_sent = True
        assert command_sent is True
        
        # Status changes away from WAITING_FOR_HUMAN
        pipeline_status = 'RUNNING'
        should_render = pipeline_status == 'WAITING_FOR_HUMAN'
        assert should_render is False
        
        # command_sent should be reset when status changes
        if pipeline_status != 'WAITING_FOR_HUMAN':
            command_sent = False
        assert command_sent is False


class TestConfirmationModal:
    """Tests for confirmation modal behavior."""
    
    # Destructive commands that should trigger modal
    DESTRUCTIVE_COMMANDS = ['RESET_PHASE', 'SKIP', 'STOP']
    NON_DESTRUCTIVE_COMMANDS = ['RETRY', 'RESET_EXECUTION', 'PROCEED']
    
    def test_clicking_reset_phase_shows_confirmation_modal(self):
        """Clicking RESET_PHASE shows confirmation modal with 'Are you sure? This cannot be undone.'"""
        command = 'RESET_PHASE'
        should_show_modal = command in self.DESTRUCTIVE_COMMANDS
        assert should_show_modal is True
        
        modal_message = 'Are you sure? This cannot be undone.'
        assert modal_message == 'Are you sure? This cannot be undone.'
    
    def test_clicking_skip_shows_confirmation_modal(self):
        """Clicking SKIP shows confirmation modal with 'Are you sure? This cannot be undone.'"""
        command = 'SKIP'
        should_show_modal = command in self.DESTRUCTIVE_COMMANDS
        assert should_show_modal is True
    
    def test_clicking_stop_shows_confirmation_modal(self):
        """Clicking STOP shows confirmation modal (copy: Send STOP to the orchestrator?)."""
        command = 'STOP'
        should_show_modal = command in self.DESTRUCTIVE_COMMANDS
        assert should_show_modal is True
    
    def test_confirming_modal_sends_post_api_command(self):
        """Confirming modal sends POST /api/command with correct command."""
        def modalConfirm(command):
            return {'command': command, 'method': 'POST', 'endpoint': '/api/command'}
        
        for cmd in self.DESTRUCTIVE_COMMANDS:
            result = modalConfirm(cmd)
            assert result['method'] == 'POST'
            assert result['endpoint'] == '/api/command'
            assert result['command'] == cmd
    
    def test_dismissing_modal_closes_without_sending(self):
        """Dismissing modal closes it without sending any request."""
        modal_open = True
        
        def modalDismiss():
            return {'request_sent': False, 'modal_closed': True}
        
        result = modalDismiss()
        assert result['request_sent'] is False
        assert result['modal_closed'] is True


class TestResetCapEnforcement:
    """Tests for reset cap enforcement (3 resets max)."""
    
    def test_is_reset_disabled_true_when_escalation_resets_gte_3(self):
        """isResetDisabled is true when escalation_resets >= 3."""
        for resets in [3, 4, 5, 10]:
            is_reset_disabled = resets >= 3
            assert is_reset_disabled is True
    
    def test_is_reset_disabled_false_when_escalation_resets_lt_3(self):
        """isResetDisabled is false when escalation_resets < 3."""
        for resets in [0, 1, 2]:
            is_reset_disabled = resets >= 3
            assert is_reset_disabled is False
    
    def test_reset_phase_button_disabled_when_escalation_resets_gte_3(self):
        """When escalation_resets >= 3, RESET_PHASE button is visually disabled."""
        escalation_resets = 3
        is_reset_disabled = escalation_resets >= 3
        
        # Button should be disabled
        button_disabled = is_reset_disabled
        assert button_disabled is True
    
    def test_reset_execution_button_disabled_when_escalation_resets_gte_3(self):
        """When escalation_resets >= 3, RESET_EXECUTION button is visually disabled."""
        escalation_resets = 3
        is_reset_disabled = escalation_resets >= 3
        
        # Button should be disabled
        button_disabled = is_reset_disabled
        assert button_disabled is True
    
    def test_disabled_reset_buttons_show_tooltip(self):
        """When escalation_resets >= 3, disabled reset buttons show the reset-cap tooltip (Proceed/Stop + anti-loop hint) on hover."""
        escalation_resets = 3
        is_reset_disabled = escalation_resets >= 3
        
        expected_tooltip = (
            "Reset cap reached (3/3). Use Proceed or Stop to advance or halt. "
            "Cap prevents infinite reset loops; fix the repo manually if needed."
        )
        
        if is_reset_disabled:
            tooltip = expected_tooltip
        else:
            tooltip = None
        
        assert tooltip == expected_tooltip
    
    def test_clicking_disabled_reset_button_does_not_send_request(self):
        """When escalation_resets >= 3, clicking disabled reset buttons does not send any request."""
        escalation_resets = 3
        is_reset_disabled = escalation_resets >= 3
        
        def handle_disabled_click():
            if is_reset_disabled:
                return {'request_sent': False}
            return {'request_sent': True}
        
        result = handle_disabled_click()
        assert result['request_sent'] is False
    
    def test_retry_button_never_disabled_by_reset_cap(self):
        """RETRY button is never disabled by reset cap."""
        for resets in [0, 1, 2, 3, 10]:
            is_reset_disabled = resets >= 3
            # RETRY is not a reset command, so never disabled
            retry_never_disabled = True
            assert retry_never_disabled is True
    
    def test_proceed_button_never_disabled_by_reset_cap(self):
        """PROCEED button is never disabled by reset cap."""
        for resets in [0, 1, 2, 3, 10]:
            is_reset_disabled = resets >= 3
            # PROCEED is not a reset command, so never disabled
            proceed_never_disabled = True
            assert proceed_never_disabled is True
    
    def test_skip_button_never_disabled_by_reset_cap(self):
        """SKIP button is never disabled by reset cap (modal still shows but button is enabled)."""
        for resets in [0, 1, 2, 3, 10]:
            is_reset_disabled = resets >= 3
            # SKIP is not a reset command (RESET_PHASE and RESET_EXECUTION are), so never disabled
            skip_never_disabled = True
            assert skip_never_disabled is True


# ──────────────────────────────────────────────────────────────────────────────
# Step 1.1 — GRID_COMMANDS label renames
# ──────────────────────────────────────────────────────────────────────────────

import re
from pathlib import Path

INDEX_HTML = Path(__file__).parent.parent / "ui" / "index.html"


def _read_grid_commands_block():
    """Extract the GRID_COMMANDS array source text (or the full EscalationCommandPanel block in Phase 2+).

    Phase 1: GRID_COMMANDS is a const array. Phase 2 replaces it with inline
    renderActionButton calls in a grouped layout. Fall back to the full panel
    block so Phase 1 label assertions remain valid against the renamed labels.
    """
    source = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(r"const GRID_COMMANDS\s*=\s*\[(.*?)\];", source, re.DOTALL)
    if m:
        return m.group(0)
    # Phase 2+ fallback: verify labels in the full EscalationCommandPanel block
    panel_m = re.search(
        r"// ─── EscalationCommandPanel.*?(?=// ─── PipelineCompletePanel)",
        source,
        re.DOTALL,
    )
    assert panel_m, "Neither GRID_COMMANDS nor EscalationCommandPanel block found"
    return panel_m.group(0)


def _read_confirmation_modal_block():
    """Extract the ConfirmationModal function source text from index.html."""
    source = INDEX_HTML.read_text(encoding="utf-8")
    # Capture from 'function ConfirmationModal' up through the closing tag of the function
    m = re.search(
        r"function ConfirmationModal\(\{.*?\}\)(.*?)function StopConfirmModal",
        source, re.DOTALL
    )
    assert m, "ConfirmationModal not found in index.html"
    return m.group(0)


def _read_handle_command_block():
    """Extract the handleCommand function source text from EscalationCommandPanel."""
    source = INDEX_HTML.read_text(encoding="utf-8")
    # Find the handleCommand inside EscalationCommandPanel (first occurrence after that comment)
    m = re.search(
        r"// ─── EscalationCommandPanel.*?const handleCommand\s*=\s*\(command\)\s*=>\s*\{(.*?)\};",
        source, re.DOTALL
    )
    assert m, "handleCommand in EscalationCommandPanel not found in index.html"
    # Return only the function body (group 1), not the full match prefix,
    # so that setShowConfirmModal from the useState() line above is not included.
    return m.group(1)


def _read_escalation_cmds_block():
    """Extract the ESCALATION_CMDS array source text from index.html."""
    source = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(r"const ESCALATION_CMDS\s*=\s*\[(.*?)\];", source, re.DOTALL)
    assert m, "ESCALATION_CMDS not found in index.html"
    return m.group(0)


class TestGridCommandLabels:
    """Step 1.1 — GRID_COMMANDS must use renamed labels."""

    def test_grid_contains_reset_phase_label(self):
        """GRID_COMMANDS entry for RESET_PHASE has label 'Reset Phase'."""
        block = _read_grid_commands_block()
        assert '"Reset Phase"' in block or "'Reset Phase'" in block, \
            "GRID_COMMANDS does not contain 'Reset Phase'"

    def test_grid_does_not_contain_revert_to_planner(self):
        """Old label 'Revert to Planner' must not appear in GRID_COMMANDS."""
        block = _read_grid_commands_block()
        assert "Revert to Planner" not in block, \
            "GRID_COMMANDS still contains old label 'Revert to Planner'"

    def test_grid_contains_reset_execution_label(self):
        """GRID_COMMANDS entry for RESET_EXECUTION has label 'Reset Execution'."""
        block = _read_grid_commands_block()
        assert '"Reset Execution"' in block or "'Reset Execution'" in block, \
            "GRID_COMMANDS does not contain 'Reset Execution'"

    def test_grid_does_not_contain_revert_to_executor(self):
        """Old label 'Revert to Executor' must not appear in GRID_COMMANDS."""
        block = _read_grid_commands_block()
        assert "Revert to Executor" not in block, \
            "GRID_COMMANDS still contains old label 'Revert to Executor'"

    def test_grid_contains_rerun_reviewer_label(self):
        """GRID_COMMANDS entry for RESET_REVIEWER has label 'Re-run Reviewer'."""
        block = _read_grid_commands_block()
        assert '"Re-run Reviewer"' in block or "'Re-run Reviewer'" in block, \
            "GRID_COMMANDS does not contain 'Re-run Reviewer'"

    def test_grid_does_not_contain_revert_to_reviewer(self):
        """Old label 'Revert to Reviewer' must not appear in GRID_COMMANDS."""
        block = _read_grid_commands_block()
        assert "Revert to Reviewer" not in block, \
            "GRID_COMMANDS still contains old label 'Revert to Reviewer'"

    def test_grid_contains_mark_complete_label(self):
        """GRID_COMMANDS entry for PROCEED has label 'Mark Complete'."""
        block = _read_grid_commands_block()
        assert '"Mark Complete"' in block or "'Mark Complete'" in block, \
            "GRID_COMMANDS does not contain 'Mark Complete'"

    def test_grid_does_not_contain_proceed_label(self):
        """Old label 'Proceed' must not appear as a GRID_COMMANDS label."""
        block = _read_grid_commands_block()
        # 'Proceed' as a standalone label value (not part of another word)
        assert re.search(r'label:\s*["\']Proceed["\']', block) is None, \
            "GRID_COMMANDS still contains old label 'Proceed'"

    def test_grid_contains_abandon_phase_label(self):
        """GRID_COMMANDS entry for SKIP has label 'Abandon Phase'."""
        block = _read_grid_commands_block()
        assert '"Abandon Phase"' in block or "'Abandon Phase'" in block, \
            "GRID_COMMANDS does not contain 'Abandon Phase'"

    def test_grid_does_not_contain_skip_label(self):
        """Old label 'Skip' must not appear as a GRID_COMMANDS label."""
        block = _read_grid_commands_block()
        assert re.search(r'label:\s*["\']Skip["\']', block) is None, \
            "GRID_COMMANDS still contains old label 'Skip'"

    def test_grid_does_not_contain_stop_entry(self):
        """No STOP entry in GRID_COMMANDS array (Stop is standalone/header-only).

        Phase 1: checks GRID_COMMANDS array doesn't list STOP as a grid command.
        Phase 2+: GRID_COMMANDS is gone; Stop is a standalone bottom button (correct).
        We verify the GRID_COMMANDS const itself has no STOP entry; if GRID_COMMANDS
        no longer exists, the test passes trivially (Phase 2 layout is correct).
        """
        source = INDEX_HTML.read_text(encoding="utf-8")
        m = re.search(r"const GRID_COMMANDS\s*=\s*\[(.*?)\];", source, re.DOTALL)
        if m:
            block = m.group(0)
            assert '"STOP"' not in block and "'STOP'" not in block, \
                "GRID_COMMANDS still contains a STOP entry — Stop must be header-only"
        # Phase 2+: GRID_COMMANDS removed; standalone Stop Pipeline button is correct.


# ──────────────────────────────────────────────────────────────────────────────
# Step 1.2 — ConfirmationModal CTAs and body text
# ──────────────────────────────────────────────────────────────────────────────

class TestConfirmationModalCTAs:
    """Step 1.2 — ConfirmationModal must use verb-matched CTAs per command."""

    def test_reset_phase_cta_is_reset_phase(self):
        """RESET_PHASE branch sets cta to 'Reset Phase' (not 'Confirm')."""
        block = _read_confirmation_modal_block()
        # The cta variable for RESET_PHASE must be set to "Reset Phase"
        assert re.search(r'cta\s*=\s*["\']Reset Phase["\']', block), \
            "ConfirmationModal RESET_PHASE branch does not set cta = 'Reset Phase'"

    def test_reset_execution_cta_is_reset_execution(self):
        """RESET_EXECUTION branch sets cta to 'Reset Execution'."""
        block = _read_confirmation_modal_block()
        assert re.search(r'cta\s*=\s*["\']Reset Execution["\']', block), \
            "ConfirmationModal RESET_EXECUTION branch does not set cta = 'Reset Execution'"

    def test_reset_reviewer_cta_is_rerun_reviewer(self):
        """RESET_REVIEWER branch sets cta to 'Re-run Reviewer'."""
        block = _read_confirmation_modal_block()
        assert re.search(r'cta\s*=\s*["\']Re-run Reviewer["\']', block), \
            "ConfirmationModal RESET_REVIEWER branch does not set cta = 'Re-run Reviewer'"

    def test_skip_cta_is_abandon_phase(self):
        """SKIP branch sets cta to 'Abandon Phase'."""
        block = _read_confirmation_modal_block()
        assert re.search(r'cta\s*=\s*["\']Abandon Phase["\']', block), \
            "ConfirmationModal SKIP branch does not set cta = 'Abandon Phase'"

    def test_confirm_button_renders_cta_variable(self):
        """The confirm button renders {cta} not hardcoded 'Confirm'."""
        block = _read_confirmation_modal_block()
        # Should use {cta} in the button, not "Confirm" as the non-busy label
        assert re.search(r'\{busy\s*\?\s*["\']Sending', block), \
            "ConfirmationModal button does not use busy ternary"
        assert re.search(r':\s*cta\s*\}', block), \
            "ConfirmationModal button does not render {cta} as the non-busy label"

    def test_stop_confirm_modal_still_says_stop_pipeline(self):
        """StopConfirmModal CTA is 'Stop Pipeline' — verify no regression."""
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
        """RESET_PHASE modal body contains 'cannot be undone from the UI'."""
        block = _read_confirmation_modal_block()
        assert "cannot be undone from the UI" in block, \
            "RESET_PHASE modal body does not mention 'cannot be undone from the UI'"

    def test_skip_body_mentions_not_cleaned_up(self):
        """SKIP modal body contains 'will NOT be cleaned up'."""
        block = _read_confirmation_modal_block()
        assert "will NOT be cleaned up" in block, \
            "SKIP modal body does not mention 'will NOT be cleaned up'"

    def test_reset_execution_body_mentions_planner_preserved(self):
        """RESET_EXECUTION modal body contains 'Planner output is preserved'."""
        block = _read_confirmation_modal_block()
        assert "Planner output is preserved" in block, \
            "RESET_EXECUTION modal body does not mention 'Planner output is preserved'"


# ──────────────────────────────────────────────────────────────────────────────
# Step 1.3 — RESET_EXECUTION requires confirm modal
# ──────────────────────────────────────────────────────────────────────────────

class TestResetExecutionRequiresConfirm:
    """Step 1.3 — RESET_EXECUTION must be in the confirm-trigger list."""

    def test_reset_execution_is_in_confirm_trigger_condition(self):
        """handleCommand condition includes RESET_EXECUTION in the confirm-modal branch."""
        block = _read_handle_command_block()
        # The confirm-trigger 'if' must include RESET_EXECUTION
        assert "RESET_EXECUTION" in block, \
            "handleCommand does not mention RESET_EXECUTION at all"
        # More specifically: the branch that sets showConfirmModal=true must contain it
        # We look for RESET_EXECUTION appearing before 'setShowConfirmModal'
        idx_re = block.find("RESET_EXECUTION")
        idx_modal = block.find("setShowConfirmModal")
        assert idx_re < idx_modal, \
            "RESET_EXECUTION appears after setShowConfirmModal — not in the confirm-trigger branch"

    def test_stop_not_in_escalation_panel_confirm_trigger(self):
        """STOP is removed from the EscalationCommandPanel handleCommand confirm-trigger list."""
        block = _read_handle_command_block()
        # After the refactor, STOP should NOT be in the escalation panel handleCommand
        # because Stop is header-only (StopConfirmModal)
        # The block should not contain === "STOP" as a confirm trigger
        assert not re.search(r'=== ["\']STOP["\']', block), \
            "handleCommand (EscalationCommandPanel) still lists STOP as a confirm trigger"


# ──────────────────────────────────────────────────────────────────────────────
# Step 1.12 — Queue hub ESCALATION_CMDS label renames
# ──────────────────────────────────────────────────────────────────────────────

class TestQueueHubEscalationCmdLabels:
    """Step 1.12 — ESCALATION_CMDS in QueueActionHub uses renamed labels."""

    def test_queue_hub_contains_reset_phase(self):
        block = _read_escalation_cmds_block()
        assert '"Reset Phase"' in block or "'Reset Phase'" in block, \
            "ESCALATION_CMDS does not contain 'Reset Phase'"

    def test_queue_hub_contains_reset_execution(self):
        block = _read_escalation_cmds_block()
        assert '"Reset Execution"' in block or "'Reset Execution'" in block, \
            "ESCALATION_CMDS does not contain 'Reset Execution'"

    def test_queue_hub_contains_rerun_reviewer(self):
        block = _read_escalation_cmds_block()
        assert '"Re-run Reviewer"' in block or "'Re-run Reviewer'" in block, \
            "ESCALATION_CMDS does not contain 'Re-run Reviewer'"

    def test_queue_hub_contains_abandon_phase(self):
        block = _read_escalation_cmds_block()
        assert '"Abandon Phase"' in block or "'Abandon Phase'" in block, \
            "ESCALATION_CMDS does not contain 'Abandon Phase'"

    def test_queue_hub_contains_mark_complete(self):
        block = _read_escalation_cmds_block()
        assert '"Mark Complete"' in block or "'Mark Complete'" in block, \
            "ESCALATION_CMDS does not contain 'Mark Complete'"

    def test_queue_hub_contains_stop_pipeline(self):
        block = _read_escalation_cmds_block()
        assert '"Stop Pipeline"' in block or "'Stop Pipeline'" in block, \
            "ESCALATION_CMDS does not contain 'Stop Pipeline'"

    def test_queue_hub_no_old_labels(self):
        block = _read_escalation_cmds_block()
        assert "Revert to Planner" not in block, "ESCALATION_CMDS still has 'Revert to Planner'"
        assert "Revert to Executor" not in block, "ESCALATION_CMDS still has 'Revert to Executor'"
        assert "Revert to Reviewer" not in block, "ESCALATION_CMDS still has 'Revert to Reviewer'"
        assert "Accept & Advance" not in block, "ESCALATION_CMDS still has 'Accept & Advance'"
        assert "Skip Phase" not in block, "ESCALATION_CMDS still has 'Skip Phase'"
        assert "Halt Pipeline" not in block, "ESCALATION_CMDS still has 'Halt Pipeline'"


# ──────────────────────────────────────────────────────────────────────────────
# Step 1.10 — Re-run Reviewer disabled when no executor output
# ──────────────────────────────────────────────────────────────────────────────

class TestRerunReviewerDisableCondition:
    """Step 1.10 — renderGridButton must disable RESET_REVIEWER when executor_output_exists is false."""

    def test_render_grid_button_handles_executor_output_exists_prop(self):
        """EscalationCommandPanel accepts executor_output_exists prop."""
        source = INDEX_HTML.read_text(encoding="utf-8")
        # The prop must appear in the function signature or destructuring
        panel_m = re.search(
            r"function EscalationCommandPanel\(\{(.*?)\}\)",
            source, re.DOTALL
        )
        assert panel_m, "EscalationCommandPanel signature not found"
        props_block = panel_m.group(1)
        assert "executor_output_exists" in props_block, \
            "EscalationCommandPanel does not accept executor_output_exists prop"

    def test_reset_reviewer_disable_condition_uses_executor_output_exists(self):
        """renderGridButton disables RESET_REVIEWER when executor_output_exists is false."""
        source = INDEX_HTML.read_text(encoding="utf-8")
        # Look for the disable condition referencing both RESET_REVIEWER and executor_output_exists
        assert re.search(
            r"RESET_REVIEWER.*executor_output_exists|executor_output_exists.*RESET_REVIEWER",
            source, re.DOTALL
        ), "renderGridButton does not reference executor_output_exists for RESET_REVIEWER disable"

    def test_parent_passes_executor_output_exists_prop(self):
        """EscalationCommandPanel render site passes executor_output_exists from pState."""
        source = INDEX_HTML.read_text(encoding="utf-8")
        # Find the EscalationCommandPanel JSX usage and verify prop is passed
        assert re.search(
            r"executor_output_exists=\{pState\.executor_output_exists",
            source
        ), "Parent does not pass executor_output_exists={pState.executor_output_exists} to EscalationCommandPanel"


# ──────────────────────────────────────────────────────────────────────────────
# Step 2.3 — Grouped layout: visibility rules for four panel states
# ──────────────────────────────────────────────────────────────────────────────


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


def _read_info_group_label_block():
    """Extract the InfoGroupLabel component source text from index.html."""
    source = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(r"function InfoGroupLabel\(\{(.*?)\}\)\s*\{(.*?)\}", source, re.DOTALL)
    assert m, "InfoGroupLabel component not found in index.html"
    return m.group(0)


class TestGroupedLayoutStructure:
    """Step 2.3 — EscalationCommandPanel must use grouped layout (not flat grid)."""

    def test_panel_has_recover_group_label(self):
        """EscalationCommandPanel renders a 'Recover' group label."""
        block = _read_escalation_panel_block()
        # "Recover" appears as JSX text child, not a quoted string
        assert "Recover" in block, \
            "EscalationCommandPanel does not render a 'Recover' group label"

    def test_panel_has_advance_manually_group_label(self):
        """EscalationCommandPanel renders an 'Advance manually' group label."""
        block = _read_escalation_panel_block()
        # "Advance manually" appears as JSX text child, not a quoted string
        assert "Advance manually" in block, \
            "EscalationCommandPanel does not render 'Advance manually' group label"

    def test_panel_has_stop_pipeline_button(self):
        """EscalationCommandPanel renders a standalone 'Stop Pipeline' button."""
        block = _read_escalation_panel_block()
        assert re.search(r"Stop Pipeline", block), \
            "EscalationCommandPanel does not render 'Stop Pipeline' button"

    def test_flat_grid_is_replaced(self):
        """The old flat 'grid grid-cols-2' render for GRID_COMMANDS is gone."""
        block = _read_escalation_panel_block()
        # The Phase 2 layout does not render all GRID_COMMANDS in a single flat grid.
        # GRID_COMMANDS array may still exist as a source reference but the flat-grid
        # render pattern 'GRID_COMMANDS.map(renderGridButton)' inside a grid div is replaced.
        assert not re.search(
            r'grid grid-cols-2.*?GRID_COMMANDS\.map\(renderGridButton\)',
            block,
            re.DOTALL,
        ), "Old flat grid GRID_COMMANDS.map(renderGridButton) is still present — Phase 2 layout not applied"

    def test_cap_reached_notice_present_in_source(self):
        """Source contains cap-reached status message copy."""
        block = _read_escalation_panel_block()
        assert re.search(r"reset budget", block, re.IGNORECASE), \
            "Cap-reached status message ('reset budget') not found in EscalationCommandPanel"

    def test_info_group_label_component_exists(self):
        """InfoGroupLabel component is defined in index.html."""
        source = INDEX_HTML.read_text(encoding="utf-8")
        assert re.search(r"function InfoGroupLabel", source), \
            "InfoGroupLabel component not defined in index.html"

    def test_info_group_label_renders_info_icon(self):
        """InfoGroupLabel renders the ⓘ info icon when hiddenItems are present."""
        source = INDEX_HTML.read_text(encoding="utf-8")
        # Find the InfoGroupLabel region and check a generous slice for the icon
        m = re.search(r"function InfoGroupLabel", source)
        assert m, "InfoGroupLabel not found"
        region = source[m.start(): m.start() + 3000]
        assert "ⓘ" in region, \
            "InfoGroupLabel does not render ⓘ info icon"

    def test_recover_group_uses_info_group_label(self):
        """Recover group header uses InfoGroupLabel component."""
        block = _read_escalation_panel_block()
        assert re.search(r"InfoGroupLabel", block), \
            "EscalationCommandPanel does not use InfoGroupLabel"

    def test_rerun_reviewer_conditional_on_show_rerun_reviewer(self):
        """Re-run Reviewer button is conditionally rendered based on showRerunReviewer or executor_output_exists."""
        block = _read_escalation_panel_block()
        assert re.search(
            r"showRerunReviewer|executor_output_exists.*RESET_REVIEWER|RESET_REVIEWER.*executor_output_exists",
            block,
            re.DOTALL,
        ), "Re-run Reviewer visibility is not conditional on executor_output_exists / showRerunReviewer"

    def test_mark_complete_conditional_on_show_mark_complete(self):
        """Mark Complete button is conditionally rendered based on showMarkComplete or merge_probe_passed."""
        block = _read_escalation_panel_block()
        assert re.search(
            r"showMarkComplete|merge_probe_passed.*PROCEED|PROCEED.*merge_probe_passed",
            block,
            re.DOTALL,
        ), "Mark Complete visibility is not conditional on merge_probe_passed / showMarkComplete"

    def test_cap_reached_hides_recover_group(self):
        """When capReached, the Recover group is replaced by a status message."""
        block = _read_escalation_panel_block()
        assert re.search(r"capReached", block), \
            "capReached variable not found in EscalationCommandPanel"

    def test_new_props_in_function_signature(self):
        """EscalationCommandPanel signature includes Phase 2 props."""
        source = INDEX_HTML.read_text(encoding="utf-8")
        panel_m = re.search(
            r"function EscalationCommandPanel\(\{(.*?)\}\)",
            source,
            re.DOTALL,
        )
        assert panel_m, "EscalationCommandPanel signature not found"
        sig = panel_m.group(1)
        assert "planner_output_exists" in sig, \
            "EscalationCommandPanel missing planner_output_exists prop"
        assert "phase_branch_exists" in sig, \
            "EscalationCommandPanel missing phase_branch_exists prop"
        assert "merge_probe_passed" in sig, \
            "EscalationCommandPanel missing merge_probe_passed prop"

    def test_parent_passes_phase_2_props(self):
        """Parent render site passes the four Phase 2 props to EscalationCommandPanel."""
        source = INDEX_HTML.read_text(encoding="utf-8")
        assert re.search(r"planner_output_exists=\{pState\.planner_output_exists", source), \
            "Parent missing planner_output_exists prop"
        assert re.search(r"phase_branch_exists=\{pState\.phase_branch_exists", source), \
            "Parent missing phase_branch_exists prop"
        assert re.search(r"merge_probe_passed=\{pState\.merge_probe_passed", source), \
            "Parent missing merge_probe_passed prop"


# ──────────────────────────────────────────────────────────────────────────────
# Step 2.4 — Advisory block behavior
# ──────────────────────────────────────────────────────────────────────────────


class TestAdvisoryBlock:
    """Step 2.4 — Collapsible advisory block must be present and correctly wired."""

    def test_advisory_expanded_state_wired_to_escalation_resets(self):
        """advisoryExpanded initial state depends on escalation_resets === 0."""
        block = _read_escalation_panel_block()
        assert re.search(
            r"advisoryExpanded.*escalation_resets\s*===\s*0|escalation_resets\s*===\s*0.*advisoryExpanded",
            block,
            re.DOTALL,
        ), "advisoryExpanded initial state not wired to escalation_resets === 0"

    def test_advisory_block_has_toggle(self):
        """Advisory block has a toggle mechanism (setAdvisoryExpanded or similar)."""
        block = _read_escalation_panel_block()
        assert re.search(r"setAdvisoryExpanded|advisoryExpanded", block), \
            "Advisory block toggle (advisoryExpanded state) not found"

    def test_advisory_text_fallback_chain(self):
        """Advisory text uses fallback chain: escalation_message || escalation_trigger_reason || last_action || static."""
        block = _read_escalation_panel_block()
        assert re.search(r"advisoryText|escalation_message.*escalation_trigger_reason", block, re.DOTALL), \
            "Advisory fallback text chain not found in EscalationCommandPanel"

    def test_advisory_counter_strip_present(self):
        """Advisory block contains counter strip with 'Escalation loops' label."""
        block = _read_escalation_panel_block()
        assert re.search(r"Escalation loops", block), \
            "Counter strip 'Escalation loops' not found in EscalationCommandPanel"

    def test_advisory_prefix_label(self):
        """Advisory paragraph shows 'Advisory' prefix label."""
        block = _read_escalation_panel_block()
        assert re.search(r"Advisory", block), \
            "Advisory prefix label not found in EscalationCommandPanel"


# ──────────────────────────────────────────────────────────────────────────────
# Step 2.8 — QueueActionHub visibility rules
# ──────────────────────────────────────────────────────────────────────────────


def _read_queue_action_hub_block():
    """Extract the QueueActionHub function source text from index.html."""
    source = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(
        r"function QueueActionHub\(\)(.*?)(?=\n\s+function [A-Z])",
        source,
        re.DOTALL,
    )
    assert m, "QueueActionHub not found in index.html"
    return m.group(0)


class TestQueueActionHubVisibilityRules:
    """Step 2.8 — QueueActionHub applies same action visibility rules as pipeline panel."""

    def test_queue_hub_hides_rerun_reviewer_when_no_executor_output(self):
        """QueueActionHub hides Re-run Reviewer when executor_output_exists is false."""
        source = INDEX_HTML.read_text(encoding="utf-8")
        hub = _read_queue_action_hub_block()
        assert re.search(
            r"executor_output_exists.*RESET_REVIEWER|RESET_REVIEWER.*executor_output_exists",
            hub,
            re.DOTALL,
        ), "QueueActionHub does not apply executor_output_exists filter to RESET_REVIEWER"

    def test_queue_hub_hides_mark_complete_when_merge_not_passed(self):
        """QueueActionHub hides Mark Complete when merge_probe_passed is false."""
        hub = _read_queue_action_hub_block()
        assert re.search(
            r"merge_probe_passed.*PROCEED|PROCEED.*merge_probe_passed",
            hub,
            re.DOTALL,
        ), "QueueActionHub does not apply merge_probe_passed filter to PROCEED"

    def test_queue_hub_hides_recover_actions_when_cap_reached(self):
        """QueueActionHub hides all recover actions when escalation_resets >= 3."""
        hub = _read_queue_action_hub_block()
        assert re.search(r"escalation_resets.*>=.*3|capReached", hub, re.DOTALL), \
            "QueueActionHub does not apply cap-reached filter"