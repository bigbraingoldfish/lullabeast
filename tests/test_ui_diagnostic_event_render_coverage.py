"""Render coverage for the later-added diagnostic/advisory events.

Static-lint guards that ``sentinel_overflow_hold``, ``sentinel_verdict_hold``,
``token_capture_warning``, ``scope_warning``, and
``queue_revive_project_missing`` render in the activity feed with a specific
badge color, display label, hover description, and prose line — not the
generic fallbacks (snake-case label + raw JSON detail dump). These events fire
during holds, degraded runs, and failed revivals, exactly when the feed most
needs to read clearly.

Same convention as ``test_ui_observability_p2_render_events.py``: slice
``index.html`` between stable identifier anchors and pin case/key completeness
only; exact prose wording is operator-reviewed visual copy and is not asserted.
"""

from pathlib import Path

import pytest

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"

EVENTS = [
    "sentinel_overflow_hold",
    "sentinel_verdict_hold",
    "token_capture_warning",
    "scope_warning",
    "queue_revive_project_missing",
]


@pytest.fixture
def html():
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text()


def _slice(html, start_anchor, end_anchor):
    """Substring between two stable identifier anchors; fails loudly if either
    anchor is missing so a structural rename surfaces as a clear error."""
    s = html.find(start_anchor)
    if s == -1:
        pytest.fail(f"anchor not found: {start_anchor!r}")
    e = html.find(end_anchor, s + len(start_anchor))
    if e == -1:
        pytest.fail(f"end anchor not found after {start_anchor!r}: {end_anchor!r}")
    return html[s:e]


@pytest.mark.parametrize("event", EVENTS)
def test_color_map_has_case(html, event):
    body = _slice(html, "function getEventBadgeColor", "const EVENT_TYPE_DISPLAY")
    assert f"case '{event}':" in body, (
        f"getEventBadgeColor must have a case for {event!r} so its dot color is "
        f"deliberate, not the fall-through default"
    )


@pytest.mark.parametrize("event", EVENTS)
def test_display_map_has_key(html, event):
    body = _slice(html, "const EVENT_TYPE_DISPLAY", "const EVENT_TYPE_DESCRIPTION")
    assert f"{event}:" in body, (
        f"EVENT_TYPE_DISPLAY must label {event!r} so the badge shows a curated "
        f"label, not raw snake-case"
    )


@pytest.mark.parametrize("event", EVENTS)
def test_description_map_has_key(html, event):
    body = _slice(html, "const EVENT_TYPE_DESCRIPTION", "function getEventDescription")
    assert f"{event}:" in body, (
        f"EVENT_TYPE_DESCRIPTION must describe {event!r} so the badge hover is "
        f"non-empty"
    )


@pytest.mark.parametrize("event", EVENTS)
def test_prose_switch_has_case(html, event):
    body = _slice(html, "function humanizeSummary", "function humanizeSnakeCase")
    assert f"case '{event}':" in body, (
        f"humanizeSummary must have a case for {event!r} so the detail renders as "
        f"prose instead of a raw JSON dump"
    )
