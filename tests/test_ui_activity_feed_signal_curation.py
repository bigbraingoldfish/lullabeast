"""Activity Feed signal curation.

A finished agent turn used to render as "{Agent} attempt N succeeded" twice
(``poll_outcome`` + ``attempt_end`` share one prose branch) directly above the
``gate_fail`` row, reading as success-then-failure. Two fixes, pinned here:

* The finished-turn prose says what actually happened ("finished, gate check
  next") instead of claiming success before the gate has run.
* Rows that restate an adjacent row or record a no-op are hidden from the
  Activity tab (``isFeedVisibleEvent``); they remain in pipeline_events.jsonl,
  ``/api/events``, and the Pipeline log tab. ``gate_fail`` prose covers every
  reviewer verdict so hiding ``reviewer_verdict`` loses no routing detail.

Grep-based assertions on the single-file React app, same pattern as
``test_ui_activity_feed_humanize.py``.
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


# ---------------------------------------------------------------------------
# 1 — A finished turn is not reported as a success
# ---------------------------------------------------------------------------


def test_succeeded_poll_reason_renders_as_finished(html):
    """The reason==='succeeded' branch must render 'finished, gate check next'
    so a turn that ends right before a gate_fail no longer reads as a pass."""
    assert "gate check next" in html, (
        "poll_outcome/attempt_end succeeded prose must say the gate check is "
        "still pending ('gate check next')"
    )


def test_succeeded_poll_reason_no_success_claim(html):
    """The old template rendered the raw poll reason as display copy
    ('... attempt N succeeded (42s)'). Pin its removal."""
    assert "succeeded${" not in html, (
        "humanizeSummary must not render 'succeeded' as the attempt verb — "
        "'succeeded' is the poll reason (turn ended), not a work verdict"
    )


# ---------------------------------------------------------------------------
# 2 — Feed curation: duplicate/no-op rows hidden from the Activity tab
# ---------------------------------------------------------------------------


def test_feed_hidden_event_types_set_exists(html):
    assert "FEED_HIDDEN_EVENT_TYPES" in html, "FEED_HIDDEN_EVENT_TYPES set not found"


@pytest.mark.parametrize(
    "event",
    ["poll_outcome", "reviewer_verdict", "reachability_not_applicable"],
)
def test_feed_hides_duplicate_or_noop_event(html, event):
    block = re.search(
        r"FEED_HIDDEN_EVENT_TYPES\s*=\s*new Set\(\[.*?\]\)", html, re.DOTALL
    )
    assert block, "FEED_HIDDEN_EVENT_TYPES set literal not found"
    assert event in block.group(0), (
        f"{event!r} must be feed-hidden — it restates an adjacent row or "
        f"records a no-op"
    )


def test_feed_hides_skipped_idle_aborts_only(html):
    """abort_attempted is hidden only for the no-op skipped_idle result;
    real interrupts (ok / FAILED / unconfirmed) stay visible."""
    fn = re.search(
        r"function\s+isFeedVisibleEvent\([^)]*\)\s*\{.*?\n\s*\}", html, re.DOTALL
    )
    assert fn, "isFeedVisibleEvent function not found"
    body = fn.group(0)
    assert "abort_attempted" in body and "skipped_idle" in body, (
        "isFeedVisibleEvent must hide abort_attempted rows only when "
        "detail.result === 'skipped_idle'"
    )


def test_activity_feed_renders_curated_events(html):
    """ActivityFeedPanel must filter through isFeedVisibleEvent and map the
    curated list, keeping raw events in state for the Escalation tab."""
    assert "events.filter(isFeedVisibleEvent)" in html, (
        "ActivityFeedPanel must derive the curated list via isFeedVisibleEvent"
    )
    assert "feedEvents.map(" in html, (
        "The Activity tab must render the curated feedEvents list"
    )


def test_hidden_events_keep_display_metadata(html):
    """Hidden types stay in EVENT_TYPE_DISPLAY so historical tooling and the
    expanded raw view still label them (they are curated, not deleted)."""
    block = re.search(r"EVENT_TYPE_DISPLAY\s*=\s*\{.*?\};", html, re.DOTALL)
    assert block
    for event in ("poll_outcome", "reviewer_verdict"):
        assert event in block.group(0)


# ---------------------------------------------------------------------------
# 3 — gate_fail prose covers every reviewer verdict (reviewer_verdict is hidden)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verdict",
    [
        "ROUTE_PLANNER",
        "CONTRACT_FAILURE",
        "VISUAL_UNVERIFIED",
        "BEHAVIORAL_UNVERIFIED",
        "REGRESSION_UNVERIFIED",
    ],
)
def test_gate_fail_prose_covers_reviewer_verdict(html, verdict):
    block = re.search(r"case 'gate_fail':.*?case 'phase_complete'", html, re.DOTALL)
    assert block, "gate_fail case block not found in humanizeSummary"
    assert verdict in block.group(0), (
        f"gate_fail prose must handle {verdict!r} — the reviewer_verdict row "
        f"is feed-hidden, so this row must carry the routing decision"
    )


# ---------------------------------------------------------------------------
# 4 — Same-second events survive dedup (event ts has 1s resolution)
# ---------------------------------------------------------------------------


def test_event_id_key_distinguishes_same_second_events(html):
    """poll_outcome and attempt_end land in the same second; the composed id
    must include agent + detail so one is not treated as the other."""
    fn = re.search(r"function\s+ensureEventId\([^)]*\)\s*\{.*?\n\s*\}", html, re.DOTALL)
    assert fn, "ensureEventId function not found"
    body = fn.group(0)
    assert "event.agent" in body and "event.detail" in body, (
        "ensureEventId key must include agent and detail — ts|type|phase alone "
        "collides for same-second events"
    )


def test_sse_dedup_requires_matching_id(html):
    """The SSE duplicate check must compare the composed id, not the bare
    timestamp — otherwise the attempt_end row is silently dropped whenever
    poll_outcome shares its second."""
    assert "e.id === newEvent.id" in html, (
        "SSE dedup must require id equality alongside the timestamp check"
    )


def test_polling_dedup_uses_event_ids(html):
    fetch_block = re.search(
        r"function\s+fetchEventsPolling\b.*?(?=function\s+\w)", html, re.DOTALL
    )
    assert fetch_block, "fetchEventsPolling function not found"
    assert "existingIds" in fetch_block.group(0), (
        "Polling dedup must key on event ids, not timestamps alone"
    )
