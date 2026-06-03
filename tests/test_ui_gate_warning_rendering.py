"""Phase 3 (gate-feedback methodology) — UI rendering for the gate_warning event.

Static lint on ui/index.html: the new ``gate_warning`` event type must be
registered in all four event-handling maps (badge color, display label,
description, summary formatter). Without this guard a partial UI update would
leave the demoted-warning event rendered as ``Unknown`` in the activity feed —
re-hiding exactly the signal the demotion was supposed to surface.

Mirrors test_ui_reachability_warning_rendering.py (gate_warning is the sibling
advisory event).
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


def test_gate_warning_in_event_type_display():
    src = _ui_text()
    pat = re.compile(r"EVENT_TYPE_DISPLAY[\s\S]{0,2000}gate_warning", re.M)
    assert pat.search(src), (
        "gate_warning must appear in EVENT_TYPE_DISPLAY so the activity feed "
        "renders a label instead of the raw event name"
    )


def test_gate_warning_in_event_type_description():
    src = _ui_text()
    pat = re.compile(r"EVENT_TYPE_DESCRIPTION[\s\S]{0,3000}gate_warning", re.M)
    assert pat.search(src), (
        "gate_warning must have a hover description in EVENT_TYPE_DESCRIPTION"
    )


def test_gate_warning_in_badge_color_switch():
    src = _ui_text()
    pat = re.compile(
        r"function getEventBadgeColor[\s\S]{0,3000}case\s+['\"]gate_warning['\"]",
        re.M,
    )
    assert pat.search(src), (
        "gate_warning must have a case in getEventBadgeColor — yellow, the "
        "advisory family (visible, never blocking)"
    )


def _humanize_summary_body(src):
    start = src.find("function humanizeSummary")
    assert start != -1, "humanizeSummary not found in ui/index.html"
    next_fn = src.find("\n        function ", start + 1)
    end = next_fn if next_fn != -1 else len(src)
    return src[start:end]


def test_gate_warning_in_humanize_summary():
    src = _ui_text()
    body = _humanize_summary_body(src)
    assert re.search(r"case\s+['\"]gate_warning['\"]", body), (
        "gate_warning must have a case in humanizeSummary so the feed shows a "
        "human-readable line per event"
    )


def test_gate_warning_humanize_reads_as_non_blocking():
    """The prose must frame the warning as advisory/non-blocking and name the
    reviewer's adjudication, so an operator does not read it as a phase failure
    (the whole point of the demotion)."""
    src = _ui_text()
    body = _humanize_summary_body(src)
    # Scope to the gate_warning case body.
    case_idx = body.find("case 'gate_warning'")
    assert case_idx != -1
    case_body = body[case_idx:case_idx + 1000].lower()
    assert "non-blocking" in case_body or "advisory" in case_body, (
        "gate_warning prose must signal it is non-blocking/advisory"
    )
    assert "adjudicate" in case_body or "reviewer" in case_body, (
        "gate_warning prose must name the reviewer's adjudication"
    )
