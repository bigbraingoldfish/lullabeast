"""P1 Stage F — UI rendering for reachability events.

Static lint on ui/index.html: both new event types must be registered in all
four event-handling maps (badge color, display label, description, summary
formatter). Without this guard, a partial UI update would leave one type
rendered as `Unknown` in the activity feed.
"""

import os
import re


UI_HTML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ui",
    "index.html",
)


def _ui_text():
    with open(UI_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


def test_reachability_warning_in_event_type_display():
    src = _ui_text()
    # EVENT_TYPE_DISPLAY is an object literal; key may be quoted or unquoted.
    pat = re.compile(r"EVENT_TYPE_DISPLAY[\s\S]{0,2000}reachability_warning", re.M)
    assert pat.search(src), (
        "reachability_warning must appear in EVENT_TYPE_DISPLAY so the activity "
        "feed renders a label instead of the raw event name"
    )


def test_reachability_not_applicable_in_event_type_display():
    src = _ui_text()
    pat = re.compile(r"EVENT_TYPE_DISPLAY[\s\S]{0,2000}reachability_not_applicable", re.M)
    assert pat.search(src)


def test_reachability_warning_in_event_type_description():
    src = _ui_text()
    pat = re.compile(r"EVENT_TYPE_DESCRIPTION[\s\S]{0,2000}reachability_warning", re.M)
    assert pat.search(src)


def test_reachability_not_applicable_in_event_type_description():
    src = _ui_text()
    pat = re.compile(r"EVENT_TYPE_DESCRIPTION[\s\S]{0,2000}reachability_not_applicable", re.M)
    assert pat.search(src)


def test_reachability_warning_in_badge_color_switch():
    src = _ui_text()
    # getEventBadgeColor uses a switch/case; the case label is a quoted string.
    pat = re.compile(
        r"function getEventBadgeColor[\s\S]{0,3000}case\s+['\"]reachability_warning['\"]",
        re.M,
    )
    assert pat.search(src), (
        "reachability_warning must have a case in getEventBadgeColor — "
        "yellow per the plan"
    )


def test_reachability_not_applicable_in_badge_color_switch():
    src = _ui_text()
    pat = re.compile(
        r"function getEventBadgeColor[\s\S]{0,3000}case\s+['\"]reachability_not_applicable['\"]",
        re.M,
    )
    assert pat.search(src), (
        "reachability_not_applicable must have a case in getEventBadgeColor — "
        "gray/slate per the plan, distinct from yellow warning"
    )


def _humanize_summary_body(src):
    """Return the body of humanizeSummary up to its closing brace + following
    function declaration. Scoped lookup so tests don't have to guess at byte
    distances inside a large file."""
    start = src.find("function humanizeSummary")
    assert start != -1, "humanizeSummary not found in ui/index.html"
    # The next top-level `function ` declaration at the same indent marks the
    # end of humanizeSummary.
    next_fn = src.find("\n        function ", start + 1)
    end = next_fn if next_fn != -1 else len(src)
    return src[start:end]


def test_reachability_warning_in_humanize_summary():
    src = _ui_text()
    body = _humanize_summary_body(src)
    assert re.search(r"case\s+['\"]reachability_warning['\"]", body), (
        "reachability_warning must have a case in humanizeSummary so the feed "
        "shows a human-readable line per event (with the hedged copy)"
    )


def test_reachability_not_applicable_in_humanize_summary():
    src = _ui_text()
    body = _humanize_summary_body(src)
    assert re.search(r"case\s+['\"]reachability_not_applicable['\"]", body)


def test_humanize_summary_uses_hedged_copy():
    """The plan calls for hedged copy that prevents reading 'unreachable' as
    'dead code.' Verify the summary case includes phrasing that surfaces
    'confirm intent' or 'orphan' / 'wiring' so the operator does not
    over-react to the warning."""
    src = _ui_text()
    body = _humanize_summary_body(src)
    assert any(
        phrase in body for phrase in ("Confirm intent", "orphan", "wiring landed")
    ), (
        "humanizeSummary must use hedged copy for reachability_warning so the "
        "operator does not infer 'broken' from 'unreachable'"
    )
