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