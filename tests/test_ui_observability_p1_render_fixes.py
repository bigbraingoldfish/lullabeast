"""Observability remediation — Phase 1 activity-feed render fixes.

Static-lint guards for the five ``ui/index.html`` render surfaces that
roadmap Phase 1 corrects so every *already-emitted* P0->P1 event renders
with specific, correct prose — no generic fallbacks, no contradictory
copy.  These read ``index.html`` as text, the same convention as
``test_ui_activity_feed_section6_events.py`` / ``test_ui_activity_feed_humanize.py``.

Scope (per the roadmap + the standing "visual tweaks skip TDD" guidance):
we pin **map/case completeness** (the right keys/cases exist) and the
**absence** of the one actively-misleading STALE phrase.  Exact prose
wording is operator-reviewed and intentionally NOT asserted — with two
deliberate exceptions that encode real requirements, not styling:

* the ``abort_verify_failed`` drift-guard (the stale "halt"/"stacking"
  copy must be *gone* — it states the opposite of what the code does), and
* the ``SKIP`` cascade cue (a removed "secret-menu" verb whose log line
  must *reinforce* its danger, per operator direction).
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


def _slice(html, start_anchor, end_anchor):
    """Return the substring of ``html`` between two stable identifier
    anchors.  Fails loudly if either anchor is missing so a structural
    rename surfaces as a clear error rather than a false pass.  We anchor
    on identifiers/case-labels (not brace counting) so the slice is robust
    to wording edits inside the block."""
    s = html.find(start_anchor)
    if s == -1:
        pytest.fail(f"anchor not found: {start_anchor!r}")
    e = html.find(end_anchor, s + len(start_anchor))
    if e == -1:
        pytest.fail(f"end anchor not found after {start_anchor!r}: {end_anchor!r}")
    return html[s:e]


# ---------------------------------------------------------------------------
# 1 — escalation_resolve command map completeness (audit gaps #2, #7)
# ---------------------------------------------------------------------------

ESCALATION_RESOLVE_NEW_COMMANDS = ["NUCLEAR_RESET", "RESET_EXECUTION", "SKIP", "PROCEED"]


@pytest.mark.parametrize("command", ESCALATION_RESOLVE_NEW_COMMANDS)
def test_escalation_resolve_maps_new_command(html, command):
    """The escalation_resolve prose map must name each resume command the
    backend can dispatch (``VALID_COMMANDS``, server.py — all 8).  Without
    these keys the feed falls back to the generic ``Human chose: {command}``.
    Catches a command silently regressing to that flat fallback."""
    # Anchor on the prose case (trailing ` {`) — the badge-color switch has a
    # one-line `case 'escalation_resolve': return ...` we must NOT match here.
    block = _slice(html, "case 'escalation_resolve': {", "case 'status_changed':")
    assert command in block, (
        f"escalation_resolve prose map must handle {command!r} so it renders "
        f"specific prose instead of the generic 'Human chose: {command}' fallback"
    )


def test_escalation_resolve_skip_is_risk_flagged(html):
    """SKIP is a removed "secret-menu" verb that can cascade on dependent
    phases.  Its post-hoc log line must REINFORCE that danger (operator
    decision) rather than read as a routine choice.  We pin the load-bearing
    cue ('cascade') on the SKIP line and leave exact wording free.  Catches
    SKIP regressing to neutral/normalizing copy."""
    # Anchor on the prose case (trailing ` {`) — the badge-color switch has a
    # one-line `case 'escalation_resolve': return ...` we must NOT match here.
    block = _slice(html, "case 'escalation_resolve': {", "case 'status_changed':")
    m = re.search(r"SKIP:\s*'([^']*)'", block)
    assert m, "SKIP must be a string-valued entry in the escalation_resolve cmds map"
    assert "cascade" in m.group(1).lower(), (
        "the SKIP escalation_resolve rendering must carry a cascade-risk cue "
        "so the historical log reinforces SKIP's danger (operator requirement); "
        f"found: {m.group(1)!r}"
    )


# ---------------------------------------------------------------------------
# 2 — reviewer_verdict contract-shape verdicts (audit gap #6)
# ---------------------------------------------------------------------------

REVIEWER_VERDICT_NEW = ["BEHAVIORAL_UNVERIFIED", "REGRESSION_UNVERIFIED"]


@pytest.mark.parametrize("verdict", REVIEWER_VERDICT_NEW)
def test_reviewer_verdict_handles_contract_shape_verdict(html, verdict):
    """The reviewer fires ``reviewer_verdict`` for EVERY verdict, including
    the contract-shape BEHAVIORAL/REGRESSION_UNVERIFIED checks.  Without a
    case they render the generic ``Verdict: X``.  Catches either verdict
    regressing to that flat fallback (mirrors the existing VISUAL line)."""
    # Prose case (trailing ` {`); the badge-color switch also has a bare
    # `case 'reviewer_verdict':` fallthrough we must not match.
    block = _slice(html, "case 'reviewer_verdict': {", "case 'stamp_init_failed':")
    assert verdict in block, (
        f"reviewer_verdict prose must handle {verdict!r} instead of "
        f"'Verdict: {verdict}'"
    )


# ---------------------------------------------------------------------------
# 3 — P3_LAST_ERROR_CODE_TITLES coverage (audit gap #5)
# ---------------------------------------------------------------------------

NEW_ERROR_CODE_TITLES = [
    "ERR_REGRESSION_PRIOR_PHASE",
    "ERR_REGRESSION_UNVERIFIED",
    "ERR_BEHAVIORAL_UNVERIFIED",
    "ERR_BEHAVIORAL_ARTIFACTS_MISSING",
    "ERR_VISUAL_UNVERIFIED",
    "ERR_MISSING_BASE_COMMIT",
    "ERR_TDD_COVERAGE_MISMATCH",
    "ERR_MANIFEST_FILE_MISSING",
    "ERR_PATH_TRAVERSAL",
    "ERR_STATUS_NOT_COMPLETE",
]


@pytest.mark.parametrize("code", NEW_ERROR_CODE_TITLES)
def test_p3_last_error_code_titles_has_code(html, code):
    """These codes are stamped onto ``gate_fail.detail.last_error_code`` by
    the gates (``record_error_code_only``) and reach the feed, but render the
    generic default title without a ``P3_LAST_ERROR_CODE_TITLES`` entry.
    Catches a real failure code rendering generically instead of its reason."""
    block = _slice(
        html, "const P3_LAST_ERROR_CODE_TITLES", "function getLastErrorCodeTitle"
    )
    assert code in block, (
        f"P3_LAST_ERROR_CODE_TITLES must include {code!r} so the activity feed "
        f"shows its specific reason instead of the generic default title"
    )


# ---------------------------------------------------------------------------
# 4 — escalation_command_invalid full four-map coverage (audit gap #3)
# ---------------------------------------------------------------------------


def test_escalation_command_invalid_badge_color_is_warning_family(html):
    """escalation_command_invalid is a recoverable safety-heal (garbage
    command -> defaulted to STOP).  It must get a warning-family badge, not
    the dark default.  Catches it falling through to the default dark badge."""
    body = _slice(html, "function getEventBadgeColor", "const EVENT_TYPE_DISPLAY")
    assert re.search(
        r"case\s+'escalation_command_invalid':\s*return\s+'bg-(amber|orange)-\d+",
        body,
    ), (
        "getEventBadgeColor must map escalation_command_invalid to an "
        "amber/orange warning class (recoverable heal), not the dark default"
    )


def test_escalation_command_invalid_has_display_label(html):
    """Without an EVENT_TYPE_DISPLAY entry the badge falls through to
    ``humanizeSnakeCase`` and shows raw 'Escalation Command Invalid'."""
    body = _slice(html, "const EVENT_TYPE_DISPLAY", "const EVENT_TYPE_DESCRIPTION")
    assert "escalation_command_invalid" in body, (
        "EVENT_TYPE_DISPLAY must label escalation_command_invalid so the badge "
        "shows a curated label, not raw snake-case"
    )


def test_escalation_command_invalid_has_description(html):
    """Without an EVENT_TYPE_DESCRIPTION entry the badge hover tooltip is
    empty for this event."""
    body = _slice(html, "const EVENT_TYPE_DESCRIPTION", "function getEventDescription")
    assert "escalation_command_invalid" in body, (
        "EVENT_TYPE_DESCRIPTION must describe escalation_command_invalid so the "
        "badge hover tooltip is non-empty"
    )


def test_escalation_command_invalid_has_prose_case(html):
    """Without a humanizeSummary case the detail renders as a raw JSON dump
    via the default ``formatPipelineEventDetail`` fallback."""
    body = _slice(html, "function humanizeSummary", "function humanizeSnakeCase")
    assert "escalation_command_invalid" in body, (
        "humanizeSummary must have a case for escalation_command_invalid so the "
        "detail renders as prose instead of a raw JSON dump"
    )


# ---------------------------------------------------------------------------
# 5 — abort_verify_failed STALE drift-guard (audit gap #1 — highest severity)
# ---------------------------------------------------------------------------


def _abort_verify_failed_description(html):
    desc_body = _slice(
        html, "const EVENT_TYPE_DESCRIPTION", "function getEventDescription"
    )
    m = re.search(r"abort_verify_failed:\s*'([^']*)'", desc_body)
    if not m:
        pytest.fail("abort_verify_failed entry not found in EVENT_TYPE_DESCRIPTION")
    return m.group(1)


def _abort_verify_failed_prose_case(html):
    # Prose case (trailing ` {`); the badge-color switch has a bare
    # `case 'abort_verify_failed':` fallthrough we must not match.
    return _slice(html, "case 'abort_verify_failed': {", "case 'reviewer_verdict':")


def test_abort_verify_failed_description_no_halt(html):
    """STALE bug: the hover description said the pipeline 'halts', but
    ``_handle_stall_outcome`` SOFT-CONTINUES (launches the next attempt).
    The description must not claim a halt.  Catches the misleading copy
    silently returning."""
    desc = _abort_verify_failed_description(html).lower()
    assert "halt" not in desc, (
        "abort_verify_failed description must not say the pipeline halts — "
        "P1 soft-continues (launches the next attempt)"
    )
    assert "soft-continue" in desc, (
        "abort_verify_failed description should state the soft-continue behavior"
    )


def test_abort_verify_failed_prose_no_halt_or_stacking(html):
    """STALE bug (highest severity in this phase): the prose said 'pipeline
    halted to avoid stacking attempts' — the opposite of the soft-continue
    behavior.  Both the 'halt' and 'stacking' wording must be gone and
    replaced with soft-continue prose."""
    prose = _abort_verify_failed_prose_case(html).lower()
    assert "halt" not in prose, (
        "abort_verify_failed prose must not say 'halted' — P1 launches the "
        "next attempt anyway (soft-continue)"
    )
    assert "stacking" not in prose, (
        "delete the stale 'stacking attempts' wording — it described the old "
        "halt behavior that no longer exists"
    )
    assert "soft-continue" in prose, (
        "abort_verify_failed prose should state the soft-continue behavior"
    )
