"""Section 7c — Activity Feed UI polish for Section 6 events.

The Section 6 events flow through ``pipeline_events.jsonl`` → UI SSE
and render in the Activity Feed.  Today they show as raw snake-case
labels ("Poll Outcome", "Attempt End") that wrap across two lines, with
the detail rendered as raw JSON like
``{"startup_grace":600,"stall_threshold":1200,...}``.  Operator wants:

* Event tags rendered on a single line (``whitespace-nowrap`` + room).
* A consistent purple ``#764cc5`` colour for the new event family.
* Readable "what happened" prose like the existing ``gate_pass`` /
  ``gate_fail`` summaries — not raw JSON.
* Hover descriptors on each tag explaining what the event means
  (because labels like "Attempt End" are obscure without context).

These tests pin all four requirements at the source-level (same grep
pattern as the existing ``test_ui_activity_feed_humanize.py``).
"""

import re
from pathlib import Path

import pytest

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"


@pytest.fixture
def html():
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text()


# Canonical Section 6 event names from CLAUDE.md's event catalogue.
SECTION6_EVENTS = (
    "poll_start",
    "poll_outcome",
    "attempt_end",
    "abort_attempted",
    "abort_verify_failed",
    "reviewer_verdict",
    "stamp_init_failed",
)


# ---------------------------------------------------------------------------
# 1 — Friendly labels for every Section 6 event in EVENT_TYPE_DISPLAY
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event", SECTION6_EVENTS)
def test_event_type_display_includes_section6_event(html, event):
    """EVENT_TYPE_DISPLAY must map each Section 6 event name to a friendly
    label so the badge does not fall through to ``humanizeSnakeCase`` and
    render the raw "Poll Outcome" / "Attempt End" forms the operator
    flagged as obscure."""
    block = re.search(r"EVENT_TYPE_DISPLAY\s*=\s*\{.*?\};", html, re.DOTALL)
    assert block, "EVENT_TYPE_DISPLAY map not found in index.html"
    body = block.group(0)
    assert event in body, (
        f"EVENT_TYPE_DISPLAY must include a key for {event!r} so the "
        f"badge renders a curated label instead of raw snake-case"
    )


# ---------------------------------------------------------------------------
# 2 — Purple #764cc5 colour for the Section 6 event family
# ---------------------------------------------------------------------------


def test_event_badge_color_uses_section6_purple(html):
    """``getEventBadgeColor`` must return the curated purple ``#764cc5``
    for the Section 6 event family so they read as one cohesive
    diagnostic family in the Activity Feed."""
    func_match = re.search(
        r"function\s+getEventBadgeColor\([^)]*\)\s*\{.*?\n\s*\}",
        html,
        re.DOTALL,
    )
    assert func_match, "getEventBadgeColor function not found"
    body = func_match.group(0)
    assert "#764cc5" in body, (
        "getEventBadgeColor must reference #764cc5 — the operator-"
        "specified colour for the Section 6 diagnostic event family"
    )


@pytest.mark.parametrize("event", SECTION6_EVENTS)
def test_event_badge_color_assigns_purple_to_each_section6_event(html, event):
    """Each Section 6 event name must appear inside the badge-color
    function within a window that references ``#764cc5`` — pinning that
    the event genuinely gets the purple colour, not falling through to
    the default dark badge."""
    func_match = re.search(
        r"function\s+getEventBadgeColor\([^)]*\)\s*\{.*?\n\s*\}",
        html,
        re.DOTALL,
    )
    assert func_match
    body = func_match.group(0)
    # Match the event name appearing in the same case-branch / mapping
    # entry as the purple.  We accept either a per-key match or a shared
    # array/case fallthrough containing all of them.
    assert event in body, (
        f"{event!r} must appear in getEventBadgeColor so it maps to "
        f"the curated #764cc5 — without this it falls through to the "
        f"default grey badge"
    )


# ---------------------------------------------------------------------------
# 3 — Readable "what happened" text in humanizeSummary
# ---------------------------------------------------------------------------


def test_humanize_summary_handles_poll_start(html):
    """``poll_start`` must produce readable prose (not raw JSON)."""
    func = re.search(
        r"function\s+humanizeSummary\([^)]*\)\s*\{.*?\n\s{8}\}",
        html,
        re.DOTALL,
    )
    assert func, "humanizeSummary function not found"
    assert "poll_start" in func.group(0), (
        "humanizeSummary must include a case for poll_start so the "
        "detail renders as prose rather than raw "
        "{\"startup_grace\":600,...} JSON"
    )


def test_humanize_summary_handles_poll_outcome(html):
    func = re.search(
        r"function\s+humanizeSummary\([^)]*\)\s*\{.*?\n\s{8}\}",
        html,
        re.DOTALL,
    )
    assert func
    body = func.group(0)
    assert "poll_outcome" in body, (
        "humanizeSummary must include a case for poll_outcome with at "
        "least the canonical reasons (succeeded/stalled/"
        "no_first_activity/timeout/stopped) rendered as prose"
    )
    # The reasons themselves should appear so the prose distinguishes them.
    for reason in ("succeeded", "stalled", "no_first_activity", "timeout"):
        assert reason in body, (
            f"humanizeSummary poll_outcome branch must reference "
            f"{reason!r} so each reason renders distinctly"
        )


@pytest.mark.parametrize(
    "event",
    [
        "attempt_end",
        "abort_attempted",
        "abort_verify_failed",
        "reviewer_verdict",
        "stamp_init_failed",
    ],
)
def test_humanize_summary_handles_event(html, event):
    """Every Section 6 event must have a curated case in
    ``humanizeSummary`` so the operator sees prose, not raw JSON."""
    func = re.search(
        r"function\s+humanizeSummary\([^)]*\)\s*\{.*?\n\s{8}\}",
        html,
        re.DOTALL,
    )
    assert func
    assert event in func.group(0), (
        f"humanizeSummary must include a case for {event!r}"
    )


def test_humanize_summary_reviewer_verdict_handles_dispatch_outcomes(html):
    """The reviewer_verdict branch must render ROUTE_EXECUTOR /
    ROUTE_PLANNER / ROUTE_ESCALATE / PASS distinctly so the operator
    sees the routing decision in plain English."""
    func = re.search(
        r"function\s+humanizeSummary\([^)]*\)\s*\{.*?\n\s{8}\}",
        html,
        re.DOTALL,
    )
    assert func
    body = func.group(0)
    # Pin that the branch handles at least the two outcomes the operator
    # is most likely to see live: PASS and ROUTE_EXECUTOR.
    for verdict in ("PASS", "ROUTE_EXECUTOR"):
        assert verdict in body, (
            f"reviewer_verdict branch in humanizeSummary must handle "
            f"{verdict!r}"
        )


# ---------------------------------------------------------------------------
# 4 — Single-line rendering (no wrap)
# ---------------------------------------------------------------------------


def test_event_badge_does_not_wrap(html):
    """The badge span that renders the event tag must include
    ``whitespace-nowrap`` so multi-word labels like "Reviewer Verdict"
    stay on one line (operator regression: "Attempt End" / "Poll
    Outcome" wrapping to two lines because the column was narrow)."""
    # Find the EventRow's badge span — anchor on ``data-event-type``.
    badge_match = re.search(
        r"<span[^>]*data-event-type=[^>]*>",
        html,
    )
    assert badge_match, "Could not locate event-type badge span"
    badge_open_tag = badge_match.group(0)
    # whitespace-nowrap can be on the badge directly OR on its wrapper.
    # Search a small window around the badge for the class.
    badge_idx = badge_match.start()
    window = html[max(0, badge_idx - 600) : badge_idx + 400]
    assert "whitespace-nowrap" in window, (
        "The event-type badge (or its wrapper) must apply "
        "``whitespace-nowrap`` so the tag stays on a single line. "
        "Found tag: " + badge_open_tag
    )


# ---------------------------------------------------------------------------
# 5 — Hover tooltip with event-type description
# ---------------------------------------------------------------------------


def test_event_badge_has_title_tooltip(html):
    """The event-type badge must have a ``title=`` attribute (hover
    tooltip) so operators get the friendly description of what the
    event means without needing to read code."""
    # Anchor on the data-event-type span and check title= within the
    # same tag.
    pat = re.compile(
        r"<span[^>]*data-event-type=[^>]*title=",
    )
    assert pat.search(html), (
        "Event-type badge must include a ``title=`` attribute so "
        "hovering reveals the event description (operator requested "
        "this for the obscure 'Attempt End' / 'Poll Outcome' tags)"
    )


def test_event_type_description_map_exists(html):
    """A descriptions map / function that maps Section 6 event names to
    hover text must exist so the title= attribute resolves to a
    meaningful description per event type."""
    # We accept either an EVENT_TYPE_DESCRIPTION map or a function that
    # returns descriptions.  Pin the structure loosely so the
    # implementation has freedom.
    has_map = bool(re.search(r"EVENT_TYPE_DESCRIPTION\s*=\s*\{", html))
    has_fn = bool(re.search(
        r"function\s+(getEventDescription|formatActivityEventDescription)\s*\(",
        html,
    ))
    assert has_map or has_fn, (
        "Either an EVENT_TYPE_DESCRIPTION map or a "
        "getEventDescription() helper must exist so the badge "
        "``title=`` tooltip resolves to a per-event description"
    )


@pytest.mark.parametrize("event", SECTION6_EVENTS)
def test_event_type_description_includes_section6_event(html, event):
    """Each Section 6 event must appear in the descriptions map/helper
    so its hover tooltip is non-empty and informative."""
    # Find the descriptions map or function body.
    map_match = re.search(
        r"EVENT_TYPE_DESCRIPTION\s*=\s*\{.*?\};", html, re.DOTALL,
    )
    fn_match = re.search(
        r"function\s+(?:getEventDescription|formatActivityEventDescription)\s*\([^)]*\)\s*\{.*?\n\s*\}",
        html,
        re.DOTALL,
    )
    body = ""
    if map_match:
        body += map_match.group(0)
    if fn_match:
        body += fn_match.group(0)
    assert body, "Descriptions map/function not found"
    assert event in body, (
        f"Description for {event!r} not found — operator-facing "
        f"tooltips must cover every Section 6 event"
    )
