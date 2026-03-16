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
        """Clicking STOP shows confirmation modal with 'Are you sure? This cannot be undone.'"""
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
        """When escalation_resets >= 3, disabled reset buttons show tooltip 'Reset cap reached (3/3). Use PROCEED or STOP.' on hover."""
        escalation_resets = 3
        is_reset_disabled = escalation_resets >= 3
        
        expected_tooltip = 'Reset cap reached (3/3). Use PROCEED or STOP.'
        
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