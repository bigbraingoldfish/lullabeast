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
    has_badge = bool(
        re.search(r"formatActivityEventTypeLabel|data-event-type", html_content)
    )
    assert has_badge, "EventRow should map event types via formatActivityEventTypeLabel / data-event-type"

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
    expected = "No events yet. Events appear here as the pipeline runs."
    assert expected in html_content, f"L-37 empty state copy not found: {expected!r}"


def test_format_activity_event_type_label_helper_exists(html_content):
    assert "function formatActivityEventTypeLabel" in html_content


def test_event_type_display_map_includes_gate_pass_and_status_changed(html_content):
    assert "EVENT_TYPE_DISPLAY" in html_content
    assert '"gate_pass"' in html_content or "'gate_pass'" in html_content
    assert "Gate pass" in html_content
    assert "Status changed" in html_content


def test_get_event_type_raw_coalesces_event_and_event_type(html_content):
    assert "function getEventTypeRaw" in html_content
    assert "event.event_type" in html_content and "event.event" in html_content

# SSE and fallback tests
def test_sse_connection_via_event_source(html_content):
    has_sse = bool(re.search(r"new\s+EventSource\s*\(\s*['\"]\/api\/events\/stream['\"]", html_content))
    assert has_sse, "ActivityFeedPanel does not connect to /api/events/stream via EventSource"

def test_sse_onmessage_handler_parses_events(html_content):
    has_onmessage = bool(re.search(r"eventSource\.onmessage\s*=", html_content))
    assert has_onmessage, "ActivityFeedPanel does not have onmessage handler for SSE"

def test_heartbeat_filtering(html_content):
    has_heartbeat_filter = bool(re.search(r"heartbeat.*\|\|.*event\.data.*\{\}", html_content))
    assert has_heartbeat_filter, "ActivityFeedPanel does not filter out heartbeat SSE messages"

def test_new_events_prepended_at_top(html_content):
    has_prepend = bool(re.search(r"return\s*\[\s*newEvent.*\.\.\.prevEvents\s*\]", html_content))
    assert has_prepend, "New events from SSE are not prepended at the top of the feed"

def test_fade_in_animation_exists(html_content):
    has_animation = bool(re.search(r"@keyframes\s+fade-in-row|event-row-fade-in", html_content))
    assert has_animation, "No fade-in animation CSS found for new event rows"

def test_fade_in_animation_duration(html_content):
    has_duration = bool(re.search(r"animation.*0\.3s|animation.*300ms", html_content))
    assert has_duration, "Fade-in animation is not ~300ms"

def test_fallback_polling_function_exists(html_content):
    has_fallback = bool(re.search(r"function\s+startPollingFallback", html_content))
    assert has_fallback, "startPollingFallback function not found for SSE failure fallback"

def test_fallback_polls_every_5_seconds(html_content):
    has_fallback_polling = bool(re.search(r"setInterval.*fetchEventsPolling.*5000", html_content))
    assert has_fallback_polling, "Fallback polling does not poll every 5 seconds"

def test_event_deduplication_by_timestamp(html_content):
    has_dedup = bool(re.search(r"existingTs.*eventTs|eventTs.*existingTs", html_content))
    assert has_dedup, "Event deduplication by timestamp not implemented"

def test_sse_reconnection_logic(html_content):
    has_reconnect = bool(re.search(r"reconnectInterval|setupSSE.*reconnect", html_content))
    assert has_reconnect, "SSE reconnection logic not found"

def test_event_source_onerror_handler(html_content):
    has_onerror = bool(re.search(r"eventSource\.onerror\s*=", html_content))
    assert has_onerror, "EventSource onerror handler not found for connection failure detection"

def test_isnew_prop_passed_to_event_row(html_content):
    has_isnew = bool(re.search(r"isNew\s*=\s*\{?newEventIds\.has", html_content))
    assert has_isnew, "isNew prop not passed to EventRow for fade-in animation"
