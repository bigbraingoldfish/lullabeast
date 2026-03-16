"""Tests for ActivityFeedPanel UI component."""
import pytest
import re
from pathlib import Path

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"

@pytest.fixture
def html_content():
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text()

def test_activity_feed_panel_component_exists(html_content):
    has_component = bool(re.search(r"function\s+ActivityFeedPanel", html_content))
    assert has_component, "ActivityFeedPanel component not found"

def test_activity_feed_fetches_from_api_events(html_content):
    has_fetch = bool(re.search(r"fetch.*/api/events", html_content))
    assert has_fetch, "ActivityFeedPanel does not fetch from /api/events"

def test_activity_feed_polls_every_5_seconds(html_content):
    has_polling = bool(re.search(r"setInterval.*5000", html_content))
    assert has_polling, "ActivityFeedPanel does not poll every 5 seconds"

def test_event_row_component_exists(html_content):
    has_event_row = bool(re.search(r"function\s+EventRow", html_content))
    assert has_event_row, "EventRow component not found"

def test_event_row_displays_timestamp_time_only(html_content):
    # Check for slice with 11 to extract time portion (characters 11-16)
    has_time_format = bool(re.search(r"slice.*11.*16", html_content))
    assert has_time_format, "EventRow does not display time-only timestamp"
    has_monospace = bool(re.search(r"font-mono|fontHeader", html_content))
    assert has_monospace, "EventRow timestamp is not in monospace font"

def test_event_row_displays_event_type_badge(html_content):
    has_badge = bool(re.search(r"event.*type.*badge|badge.*event.*type", html_content, re.IGNORECASE))
    assert has_badge, "EventRow does not display event type badge"

def test_event_row_displays_agent_field(html_content):
    has_agent = bool(re.search(r"event.*\.agent|event.*agent", html_content))
    assert has_agent, "EventRow does not display agent field"

def test_event_row_displays_phase_field(html_content):
    has_phase = bool(re.search(r"event.*\.phase|event.*phase", html_content))
    assert has_phase, "EventRow does not display phase field"

def test_event_row_displays_attempt_or_em_dash(html_content):
    has_attempt = bool(re.search(r"attempt.*—|attempt.*em-dash", html_content))
    assert has_attempt, "EventRow does not handle attempt with em-dash fallback"

def test_event_row_displays_truncated_detail(html_content):
    has_truncation = bool(re.search(r"slice.*60|substring.*60", html_content))
    assert has_truncation, "EventRow does not truncate detail to ~60 chars"
    has_ellipsis = bool(re.search(r"\.\.\.|&hellip|\.\.\.", html_content))
    assert has_ellipsis, "EventRow does not show ellipsis for truncated detail"

def test_badge_color_gate_pass_green(html_content):
    has_gate_pass = bool(re.search(r"gate_pass.*green|gate_pass.*emerald|gate_pass.*bg-green", html_content))
    assert has_gate_pass, "gate_pass badge does not use green color"

def test_badge_color_gate_fail_amber(html_content):
    has_gate_fail = bool(re.search(r"gate_fail.*amber|gate_fail.*yellow|gate_fail.*bg-amber", html_content))
    assert has_gate_fail, "gate_fail badge does not use amber color"

def test_badge_color_retry_amber(html_content):
    has_retry = bool(re.search(r"retry.*amber|retry.*yellow|retry.*bg-amber", html_content))
    assert has_retry, "retry badge does not use amber color"

def test_badge_color_escalation_trigger_orange(html_content):
    has_esc_trigger = bool(re.search(r"escalation_trigger.*orange|escalation_trigger.*bg-orange", html_content))
    assert has_esc_trigger, "escalation_trigger badge does not use orange color"

def test_badge_color_escalation_resolve_blue(html_content):
    has_esc_resolve = bool(re.search(r"escalation_resolve.*blue|escalation_resolve.*bg-blue", html_content))
    assert has_esc_resolve, "escalation_resolve badge does not use blue color"

def test_badge_color_phase_complete_bright_green(html_content):
    has_phase_complete = bool(re.search(r"phase_complete.*green|phase_complete.*lime|phase_complete.*bright", html_content, re.IGNORECASE))
    assert has_phase_complete, "phase_complete badge does not use bright green"

def test_badge_color_others_gray(html_content):
    has_gray_fallback = bool(re.search(r"bg-slate-600|bg-gray-600|default.*gray", html_content))
    assert has_gray_fallback, "Other event types do not have gray fallback"

def test_single_active_expand_collapse_pattern(html_content):
    has_expanded_state = bool(re.search(r"expandedEventId", html_content))
    assert has_expanded_state, "No expandedEventId state found for single-active pattern"
    has_toggle = bool(re.search(r"expandedEventId.*null|setExpandedEventId.*null", html_content))
    assert has_toggle, "No toggle logic (setting to null) for expand/collapse"

def test_inline_expansion_shows_full_detail(html_content):
    has_inline_expand = bool(re.search(r"expandedEventId.*event.*id|event.*id.*expandedEventId", html_content))
    assert has_inline_expand, "No inline expansion for full detail when expanded"

def test_empty_state_renders_placeholder(html_content):
    has_empty_state = bool(re.search(r"No events recorded yet", html_content))
    assert has_empty_state, "Empty state placeholder not found"
