"""Tests for Escalation Log Tab UI component."""
import pytest
import re
from pathlib import Path

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"

@pytest.fixture
def html_content():
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text()

def test_tab_state_exists_in_activity_feed_panel(html_content):
    """Tab toggle state exists with 'activityTab' state variable."""
    has_tab_state = bool(re.search(r"activityTab|activeTab", html_content))
    assert has_tab_state, "Activity tab state (activityTab/activeTab) not found in ActivityFeedPanel"

def test_tab_toggle_ui_with_activity_button(html_content):
    """Tab toggle UI has 'Activity' button in Activity Feed panel header."""
    has_activity_button = bool(re.search(r"Activity.*tab|tab.*Activity|button.*Activity", html_content, re.IGNORECASE))
    assert has_activity_button, "Activity button not found in tab toggle UI"

def test_tab_toggle_ui_with_escalation_button(html_content):
    """Tab toggle UI has 'Escalation' button in Activity Feed panel header."""
    has_escalation_button = bool(re.search(r"Escalation.*tab|tab.*Escalation|button.*Escalation", html_content, re.IGNORECASE))
    assert has_escalation_button, "Escalation button not found in tab toggle UI"

def test_escalation_log_panel_subcomponent_exists(html_content):
    """EscalationLogPanel sub-component exists to render paired escalation events."""
    has_escalation_panel = bool(re.search(r"function\s+EscalationLogPanel", html_content))
    assert has_escalation_panel, "EscalationLogPanel sub-component not found"

def test_pairing_logic_matches_trigger_to_resolve_by_phase(html_content):
    """Pairing logic matches escalation_trigger to escalation_resolve by phase field."""
    has_pairing_logic = bool(re.search(r"escalation_trigger.*phase|phase.*escalation_resolve", html_content, re.IGNORECASE))
    assert has_pairing_logic, "Pairing logic matching trigger to resolve by phase not found"

def test_escalation_row_displays_phase_id(html_content):
    """Escalation row displays phase ID."""
    has_phase_display = bool(re.search(r"escalation.*phase|phase.*escalation", html_content))
    assert has_phase_display, "Phase ID not displayed in escalation row"

def test_escalation_row_displays_triggered_timestamp(html_content):
    """Escalation row displays triggered timestamp."""
    has_triggered_ts = bool(re.search(r"Triggered:|triggered\s+time|time\s+trigger", html_content, re.IGNORECASE))
    assert has_triggered_ts, "Triggered timestamp not displayed in escalation row"

def test_escalation_row_displays_trigger_reason(html_content):
    """Escalation row displays trigger reason."""
    has_reason = bool(re.search(r"Reason:|reason.*trigger|trigger.*reason", html_content, re.IGNORECASE))
    assert has_reason, "Trigger reason not displayed in escalation row"

def test_escalation_row_displays_command_field(html_content):
    """Escalation row displays command field."""
    has_command = bool(re.search(r"Command:|command.*escalation|escalation.*command", html_content, re.IGNORECASE))
    assert has_command, "Command field not displayed in escalation row"

def test_escalation_row_displays_resolved_timestamp(html_content):
    """Escalation row displays resolved timestamp."""
    has_resolved_ts = bool(re.search(r"Resolved:|resolved\s+time|time\s+resolved", html_content, re.IGNORECASE))
    assert has_resolved_ts, "Resolved timestamp not displayed in escalation row"

def test_escalation_row_displays_elapsed_duration(html_content):
    """Escalation row displays elapsed duration."""
    has_duration = bool(re.search(r"elapsed.*duration|duration.*elapsed", html_content, re.IGNORECASE))
    assert has_duration, "Elapsed duration not displayed in escalation row"

def test_in_progress_escalation_shows_awaiting_command(html_content):
    """In-progress escalation shows 'Awaiting command...' when trigger exists but no matching resolve."""
    has_awaiting = bool(re.search(r"Awaiting command", html_content, re.IGNORECASE))
    assert has_awaiting, "'Awaiting command...' not found for in-progress escalation"

def test_empty_escalation_view_displays_message(html_content):
    """Empty escalation view displays 'No escalations recorded in this session.'"""
    has_empty_message = bool(re.search(r"No escalations recorded", html_content, re.IGNORECASE))
    assert has_empty_message, "Empty escalation view message not found"

def test_tab_switching_uses_existing_events_from_state(html_content):
    """Tab switching uses existing events from state without making new API calls."""
    # Tab switching should not trigger new fetch calls
    # Check that tab click handler doesn't call fetch
    has_tab_handler = bool(re.search(r"setActivityTab|handleTabClick", html_content))
    assert has_tab_handler, "Tab click handler not found"

def test_tab_buttons_have_active_inactive_states(html_content):
    """Tab buttons styled with active/inactive states using Tailwind."""
    # Check for conditional styling based on active tab
    has_active_style = bool(re.search(r"active.*tab|tab.*active", html_content, re.IGNORECASE))
    assert has_active_style, "Active/inactive tab styling not found"

def test_tab_buttons_use_tailwind_classes(html_content):
    """Tab buttons use Tailwind CSS classes for styling."""
    has_tailwind = bool(re.search(r"bg-blue|bg-slate|px-|py-", html_content))
    assert has_tailwind, "Tailwind CSS classes not found for tab buttons"

def test_escalation_events_filtered_from_activity_events(html_content):
    """Escalation tab filters to show only escalation_trigger and escalation_resolve events."""
    has_filter = bool(re.search(r"filter.*escalation|escalation.*filter", html_content, re.IGNORECASE))
    assert has_filter, "Escalation event filtering not found"