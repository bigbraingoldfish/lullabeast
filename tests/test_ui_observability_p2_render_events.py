"""Observability remediation — Phase 2 lifecycle/destructive event render cases.

Static-lint guards that each of the five NEW Phase 2 events
(``nuclear_reset``, ``queue_halted``, ``queue_parked``, ``queue_revived``,
``dependency_hold``) renders in the activity feed with a specific badge color,
display label, hover description, and prose line — not the generic ``default:``
fallbacks (dark badge + raw JSON detail dump).

Same convention as ``test_ui_observability_p1_render_fixes.py``: read
``index.html`` as text and slice between stable identifier anchors. Per the
standing "visual tweaks skip TDD" guidance, we pin **case/key completeness**
(the right cases exist) only; exact prose wording is operator-reviewed visual
copy and is intentionally NOT asserted here.

Kept a SEPARATE file from the P1 guards on purpose — P1 protects already-shipped
fixes and must stay independent of the Phase 2 additions.
"""

from pathlib import Path

import pytest

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"

# The five SILENT transitions Phase 2 makes first-class events.
NEW_EVENTS = ["nuclear_reset", "queue_halted", "queue_parked", "queue_revived", "dependency_hold"]


@pytest.fixture
def html():
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text()


def _slice(html, start_anchor, end_anchor):
    """Return the substring between two stable identifier anchors. Fails loudly if
    either anchor is missing so a structural rename surfaces as a clear error rather
    than a false pass. Anchors are function/const identifiers (not brace counting),
    so the slice is robust to wording edits inside the block."""
    s = html.find(start_anchor)
    if s == -1:
        pytest.fail(f"anchor not found: {start_anchor!r}")
    e = html.find(end_anchor, s + len(start_anchor))
    if e == -1:
        pytest.fail(f"end anchor not found after {start_anchor!r}: {end_anchor!r}")
    return html[s:e]


@pytest.mark.parametrize("event", NEW_EVENTS)
def test_color_map_has_case(html, event):
    """getEventBadgeColor must map each new event to a specific badge class instead of
    falling through to the dark ``default:`` badge. Catches a new event rendering with
    the indistinct default color."""
    body = _slice(html, "function getEventBadgeColor", "const EVENT_TYPE_DISPLAY")
    assert f"case '{event}':" in body, (
        f"getEventBadgeColor must have a case for {event!r} so it renders a specific "
        f"badge color instead of the dark default"
    )


@pytest.mark.parametrize("event", NEW_EVENTS)
def test_display_map_has_key(html, event):
    """EVENT_TYPE_DISPLAY must label each new event so the badge shows a curated label
    instead of raw ``humanizeSnakeCase`` output (e.g. 'Queue Halted')."""
    body = _slice(html, "const EVENT_TYPE_DISPLAY", "const EVENT_TYPE_DESCRIPTION")
    assert f"{event}:" in body, (
        f"EVENT_TYPE_DISPLAY must label {event!r} so the badge shows a curated label, "
        f"not raw snake-case"
    )


@pytest.mark.parametrize("event", NEW_EVENTS)
def test_description_map_has_key(html, event):
    """EVENT_TYPE_DESCRIPTION must describe each new event so the badge hover tooltip is
    non-empty. Catches a new event with an empty hover."""
    body = _slice(html, "const EVENT_TYPE_DESCRIPTION", "function getEventDescription")
    assert f"{event}:" in body, (
        f"EVENT_TYPE_DESCRIPTION must describe {event!r} so the badge hover is non-empty"
    )


@pytest.mark.parametrize("event", NEW_EVENTS)
def test_prose_switch_has_case(html, event):
    """humanizeSummary must have a prose case for each new event so the feed line reads
    as readable prose instead of a raw JSON detail dump via the ``default:`` fallback."""
    body = _slice(html, "function humanizeSummary", "function humanizeSnakeCase")
    assert f"case '{event}':" in body, (
        f"humanizeSummary must have a case for {event!r} so the detail renders as prose "
        f"instead of a raw JSON dump"
    )
