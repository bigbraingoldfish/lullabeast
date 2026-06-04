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

def test_event_row_displays_humanized_summary(html_content):
    event_row_block = re.search(r"function\s+EventRow\b.*?(?=function\s+\w)", html_content, re.DOTALL)
    assert event_row_block, "EventRow function not found"
    assert "humanizeSummary" in event_row_block.group(0), \
        "EventRow should use humanizeSummary for the summary column"

# Lullabeast: event rows are neutral chips with one small KEYED DOT (color = category),
# not filled badges. getEventBadgeColor returns the dot hex:
#   #4fd98c green=success · #f0697c rose=escalation · #e8893e amber=warning · #7c86c8 lavender=info
def _event_badge_fn(html_content):
    i = html_content.find("function getEventBadgeColor")
    assert i >= 0, "getEventBadgeColor function not found"
    return html_content[i:i + 2200]


def test_badge_color_gate_pass_green(html_content):
    fn = _event_badge_fn(html_content)
    assert re.search(r"'gate_pass'[\s\S]{0,400}?#4fd98c", fn), "gate_pass dot should be brand green #4fd98c"

def test_badge_color_gate_fail_amber(html_content):
    fn = _event_badge_fn(html_content)
    assert re.search(r"'gate_fail'[\s\S]{0,400}?#e8893e", fn), "gate_fail dot should be the warning amber #e8893e"


def test_badge_color_escalation_trigger_orange(html_content):
    fn = _event_badge_fn(html_content)
    assert re.search(r"'escalation_trigger'[\s\S]{0,200}?#f0697c", fn), "escalation_trigger dot should be the rose #f0697c"

def test_badge_color_escalation_resolve_blue(html_content):
    fn = _event_badge_fn(html_content)
    assert re.search(r"'escalation_resolve'[\s\S]{0,900}?#7c86c8", fn), "escalation_resolve dot should be the info lavender #7c86c8"

def test_badge_color_phase_complete_bright_green(html_content):
    fn = _event_badge_fn(html_content)
    assert re.search(r"'phase_complete'[\s\S]{0,400}?#4fd98c", fn), "phase_complete dot should be brand green #4fd98c"

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
    assert "gate_pass" in html_content
    assert "'Passed'" in html_content or '"Passed"' in html_content
    assert "'Updated'" in html_content or '"Updated"' in html_content


def test_get_event_type_raw_coalesces_event_and_event_type(html_content):
    assert "function getEventTypeRaw" in html_content
    assert "event.event_type" in html_content and "event.event" in html_content


def test_format_pipeline_event_detail_normalizes_structured_detail(html_content):
    assert "function formatPipelineEventDetail" in html_content
    assert "detail.reason" in html_content
    assert "detail.gate_result" in html_content
    assert 's === "{}"' in html_content

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


# --- Step 1: ensureEventId + SSE guard fix ---

def test_ensure_event_id_function_exists(html_content):
    assert bool(re.search(r"function\s+ensureEventId", html_content)), \
        "ensureEventId function not found — needed to assign IDs to file-based events"

def test_ensure_event_id_applied_in_fetch_events(html_content):
    fetch_block = re.search(r"function\s+fetchEvents\b.*?(?=function\s+\w)", html_content, re.DOTALL)
    assert fetch_block, "fetchEvents function not found"
    assert "ensureEventId" in fetch_block.group(0), \
        "ensureEventId not applied inside fetchEvents — file events will lack IDs"

def test_ensure_event_id_applied_in_sse_handler(html_content):
    sse_block = re.search(r"onmessage\s*=.*?(?=onerror)", html_content, re.DOTALL)
    assert sse_block, "SSE onmessage block not found"
    assert "ensureEventId" in sse_block.group(0), \
        "ensureEventId not applied inside SSE onmessage — streamed file events will lack IDs"

def test_sse_guard_validates_by_timestamp_not_id(html_content):
    sse_block = re.search(r"onmessage\s*=.*?(?=onerror)", html_content, re.DOTALL)
    assert sse_block, "SSE onmessage block not found"
    block = sse_block.group(0)
    has_ts_guard = bool(re.search(r"newEvent\s*&&\s*\(?\s*newEvent\.ts\s*\|\|\s*newEvent\.timestamp", block))
    has_old_id_guard = bool(re.search(r"newEvent\s*&&\s*newEvent\.id\s*\)", block))
    assert has_ts_guard or not has_old_id_guard, \
        "SSE guard still checks newEvent.id — file events without IDs are silently dropped"


# --- Step 3: EventRow 4-column layout, structured expand, row accents ---

def test_event_row_column_header_exists(html_content):
    assert bool(re.search(r"WHAT HAPPENED|What happened", html_content)), \
        "Column header 'WHAT HAPPENED' not found for the summary column"

def test_event_row_four_column_layout(html_content):
    event_row_block = re.search(r"function\s+EventRow\b.*?(?=function\s+\w)", html_content, re.DOTALL)
    assert event_row_block, "EventRow function not found"
    block = event_row_block.group(0)
    assert "event.phase" in block, "EventRow must show phase"
    assert "humanizeSummary" in block, "EventRow must show humanized summary"
    assert "attemptDisplay" not in block, "EventRow should not have attempt column"

def test_expanded_detail_structured_layout(html_content):
    event_row_block = re.search(r"function\s+EventRow\b.*?(?=function\s+\w)", html_content, re.DOTALL)
    assert event_row_block, "EventRow function not found"
    block = event_row_block.group(0)
    assert bool(re.search(r"grid|Grid", block)), \
        "Expanded detail should use a grid layout for key-value pairs"
    assert "text-slate-500" in block, \
        "Expanded detail should have muted labels (text-slate-500)"

def test_expanded_detail_shows_raw_data(html_content):
    event_row_block = re.search(r"function\s+EventRow\b.*?(?=function\s+\w)", html_content, re.DOTALL)
    assert event_row_block, "EventRow function not found"
    block = event_row_block.group(0)
    assert bool(re.search(r"Raw|raw data|Raw data", block)), \
        "Expanded detail should include a 'Raw data' section"

def test_escalation_row_no_random_border_outlines(html_content):
    event_row_block = re.search(r"function\s+EventRow\b.*?(?=function\s+\w)", html_content, re.DOTALL)
    assert event_row_block, "EventRow function not found"
    block = event_row_block.group(0)
    assert "border-orange-500" not in block and "border-l-2" not in block, \
        "EventRow should not add per-type left border outlines — they look random"

def test_gate_pass_row_dimmed(html_content):
    event_row_block = re.search(r"function\s+EventRow\b.*?(?=function\s+\w)", html_content, re.DOTALL)
    assert event_row_block, "EventRow function not found"
    block = event_row_block.group(0)
    assert "opacity" in block, \
        "Routine events (gate_pass, phase_complete) should be dimmed with opacity"
