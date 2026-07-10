"""Static contract tests: pending-bubble agent liveness (Phase 2, T2.2).

While a turn is pending the client polls the read-only
``GET /api/ideas/{id}/turn-status`` endpoint and renders the same signal the
pipeline operator gets from the dashboard's activity pulse: working (with
activity age), amber as silence approaches the stall threshold, an honest
"quiet" past it — instead of a bare elapsed clock.

Pattern mirrors the other ui_ideas contract suites: read index.html, regex the
required code shapes.
"""
import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


def test_liveness_poll_fetches_turn_status_while_a_turn_is_pending():
    """A dedicated effect keyed on the pending turn must poll turn-status."""
    html = load_index_html()
    assert re.search(
        r"/api/ideas/\$\{ideaId\}/turn-status\?turn=\$\{pendingTurn\}", html
    ), "Expected the liveness watch to GET /turn-status for the pending turn"
    assert re.search(
        r"useEffect\([\s\S]{0,1200}?turn-status[\s\S]{0,1000}?"
        r"\}\s*,\s*\[\s*currentIdeaId\s*,\s*pendingTurn\s*\]\s*\)",
        html,
    ), "Expected the liveness poll effect keyed on [currentIdeaId, pendingTurn]"


def test_liveness_poll_stops_when_no_turn_is_pending():
    """No pending turn → no background fetch loop and no stale snapshot."""
    html = load_index_html()
    assert re.search(
        r"if\s*\(\s*!currentIdeaId\s*\|\|\s*pendingTurn\s*==\s*null\s*\)\s*\{"
        r"[\s\S]{0,120}?setTurnLiveness\(null\)",
        html,
    ), "Expected the liveness effect to clear state and bail when nothing is pending"
    assert re.search(
        r"turnLivenessPollRef\.current\s*=\s*setInterval\(\s*tick\s*,\s*TURN_LIVENESS_POLL_MS\s*\)"
        r"[\s\S]{0,400}?clearInterval\(\s*turnLivenessPollRef\.current\s*\)",
        html,
    ), "Expected the liveness interval cleaned up in the effect cleanup"


def test_liveness_poll_guards_against_stale_idea_writes():
    html = load_index_html()
    assert re.search(
        r"turn-status[\s\S]{0,300}?ideaId\s*!==\s*currentIdeaIdRef\.current",
        html,
    ), "Expected the liveness resolve to drop writes for a non-foreground idea"


def test_pending_turn_renders_liveness_row_under_the_bubble():
    """While pending, the card shows only the loading backdrop (no placeholder
    prose, no in-card timer); the liveness line (left) and the elapsed timer
    (right) sit in a row under the bubble."""
    html = load_index_html()
    assert re.search(
        r"\{hasProse\s*&&\s*!msg\.pending\s*\?", html
    ), "Expected the pending bubble to suppress the placeholder prose (backdrop only)"
    assert re.search(
        r"msg\.pending\s*&&\s*\([\s\S]{0,400}?<PendingTurnLiveness\s+liveness=\{turnLiveness\}"
        r"[\s\S]{0,300}?<ChatElapsedSeconds",
        html,
    ), "Expected the under-bubble row: liveness left, elapsed timer right"


def test_liveness_copy_carries_no_em_dashes():
    """UI copy standard for this surface: plain punctuation, no em dashes."""
    html = load_index_html()
    m = re.search(r"function PendingTurnLiveness[\s\S]{0,3000}?ideas-turn-liveness", html)
    assert m, "Expected the PendingTurnLiveness component source"
    assert "—" not in m.group(0), "Liveness copy must not contain em dashes"


def test_liveness_copy_covers_working_amber_and_quiet():
    """Working shows activity age; nearing-stall goes amber on the server's
    threshold knob; past it the copy is honest about waiting for the verdict."""
    html = load_index_html()
    assert "last activity" in html, "Expected working-state copy with activity age"
    assert re.search(
        r"age\s*>=\s*threshold\s*\*\s*0?\.\d+[\s\S]{0,200}?bg-amber-400",
        html,
    ), "Expected the amber transition derived from idle_threshold_seconds"
    assert "waiting for the server's verdict" in html, (
        "Expected honest quiet-state copy (the server still owns resolution)"
    )
    assert re.search(r"idle_threshold_seconds", html), (
        "Expected the threshold read from the turn-status response (single source)"
    )


def test_stale_stamp_from_before_the_turn_reads_as_waiting():
    """The activity stamp is shared across Ideas flows; residue older than this
    turn must render the neutral waiting state, not a false working/quiet."""
    html = load_index_html()
    assert re.search(
        r"preTurn\s*=\s*age\s*==\s*null\s*\|\|\s*\(\s*elapsed\s*!=\s*null\s*&&\s*age\s*>\s*elapsed",
        html,
    ), "Expected a pre-turn residue guard comparing stamp age to turn elapsed time"
    assert "waiting for the agent's first activity" in html


def test_optimistic_user_row_carries_the_turn_number():
    """The liveness watch keys on ideas_turn from the preceding user row; the
    optimistic send row must carry it like the server-persisted row does."""
    html = load_index_html()
    assert re.search(
        r"const\s+userRow\s*=\s*\{[\s\S]{0,300}?ideas_turn:\s*turn",
        html,
    ), "Expected the optimistic user row to set ideas_turn"
