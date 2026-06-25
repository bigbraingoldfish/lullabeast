"""P2-B — Pipeline-screen live telemetry rendering (consumes P2-A's /api/state + metrics).

Regex/substring assertions over ``ui/index.html`` (the single-file React frontend has
no build step; the established UI-test idiom is source assertions, mirroring
``test_ui_current_phase_panel.py`` / ``test_ui_monitor_redesign.py``).

Covers the four observability surfaces (the last observability work before MVP):
- **C1** — the monitor strip gains a live in-progress-phase spend suffix from
  ``/api/state.current_phase_tokens`` (additive to the completed-phase totals).
- **C2** — ``CurrentPhasePanel`` gains an agent-liveness pulse from
  ``agent_activity_age_seconds`` (ambers as it nears the stall threshold).
- **C3** — ``CurrentPhasePanel`` shows the dense ``last_attempt_summary`` /
  ``last_poll_reason`` line.
- **C5** — collapsed roadmap rows show ⚠/↻/⛔ chips from the now-passed-through
  per-phase pain signals; the expansion lists the codes.
- ``operator_action`` (P1-C events) is registered in all four feed registries so the
  activity feed renders it with a label/colour/tooltip/prose instead of the generic fallback.
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


def _roadmap_panel_slice(html):
    start = html.find("function RoadmapPanel")
    assert start != -1, "RoadmapPanel not found"
    # RoadmapPanel runs up to RoadmapPanel's React.memo close; slice generously to
    # the next top-level component to scope assertions to this component.
    end = html.find("function PipelineCompletePanel", start)
    if end == -1 or end < start:
        end = start + 60000
    return html[start:end]


def _current_phase_panel_slice(html):
    start = html.find("function CurrentPhasePanel")
    assert start != -1, "CurrentPhasePanel not found"
    end = html.find("function makeCompletionRenderer", start)
    assert end != -1, "CurrentPhasePanel end boundary not found"
    return html[start:end]


# ── C1 — live-phase spend on the strip ──────────────────────────────────────

def test_roadmap_panel_accepts_current_phase_tokens(html):
    """RoadmapPanel must destructure ``currentPhaseTokens`` so the strip can show the
    in-progress phase's live spend alongside the completed-phase totals."""
    sig = html[html.find("function RoadmapPanel"): html.find("function RoadmapPanel") + 300]
    assert "currentPhaseTokens" in sig, "RoadmapPanel must accept currentPhaseTokens prop"


def test_roadmap_call_site_binds_current_phase_tokens(html):
    """The Pipeline screen feeds the live tokens from /api/state into RoadmapPanel."""
    assert re.search(r"currentPhaseTokens=\{pState\.current_phase_tokens\}", html), (
        "RoadmapPanel render must bind currentPhaseTokens={pState.current_phase_tokens}"
    )


def test_live_phase_spend_element_present(html):
    """The strip renders a live in-progress-phase spend element, gated on an active run
    (RUNNING / WAITING_FOR_SENTINEL) and computed from currentPhaseTokens — additive to
    the completed-phase totals (no double-count: the live phase is not yet a metrics row)."""
    rp = _roadmap_panel_slice(html)
    assert 'data-testid="live-phase-spend"' in rp, "live-phase-spend element missing from the strip"
    assert "currentPhaseTokens" in rp
    assert "WAITING_FOR_SENTINEL" in rp and "RUNNING" in rp, (
        "live spend must be gated on an active run status"
    )
    # Shows tokens and/or cost via the existing compact formatters.
    assert "fmtTokensCompact" in rp and "fmtUSD" in rp


# ── C2 — agent liveness pulse ───────────────────────────────────────────────

def test_current_phase_panel_accepts_liveness_and_attempt_props(html):
    """CurrentPhasePanel must accept the P2-A live fields it renders."""
    _cpp_start = html.find("function CurrentPhasePanel")
    sig = html[_cpp_start: html.find(") {", _cpp_start)]  # full destructured signature
    for prop in ("agent_activity_age_seconds", "last_attempt_summary", "last_poll_reason"):
        assert prop in sig, f"CurrentPhasePanel must destructure {prop}"


def test_current_phase_panel_call_site_binds_new_props(html):
    """The Pipeline screen feeds the new /api/state fields into CurrentPhasePanel."""
    assert re.search(r"agent_activity_age_seconds=\{pState\.agent_activity_age_seconds\}", html)
    assert re.search(r"last_attempt_summary=\{pState\.last_attempt_summary\}", html)
    assert re.search(r"last_poll_reason=\{pState\.last_poll_reason\}", html)


def test_agent_liveness_indicator_present(html):
    """A liveness indicator reads agent_activity_age_seconds, pulses when fresh, and
    ambers as the age approaches the stall threshold (the working-vs-stalled cue)."""
    cpp = _current_phase_panel_slice(html)
    assert 'data-testid="agent-liveness"' in cpp, "agent-liveness indicator missing"
    assert "agent_activity_age_seconds" in cpp
    assert "animate-pulse" in cpp, "fresh-liveness dot should pulse"
    assert re.search(r"amber", cpp), "liveness should amber as it nears the stall threshold"


# ── C3 — last attempt summary line (removed per operator request) ────────────

def test_last_attempt_summary_line_absent(html):
    """The dense last_attempt_summary line was removed from CurrentPhasePanel — the
    raw "phase=… agent=… attempt=… reason=…" string was operator-facing noise."""
    cpp = _current_phase_panel_slice(html)
    assert 'data-testid="last-attempt-summary"' not in cpp, "last-attempt-summary line should be gone"


# ── C5 — collapsed-row chips + expanded code list ───────────────────────────

def test_phase_row_chips_present(html):
    """Collapsed roadmap rows show compact ⚠/↻/⛔ chips driven by the per-phase pain
    signals now passed through /api/metrics-summary (gate_warnings / executor_attempts /
    escalations)."""
    rp = _roadmap_panel_slice(html)
    assert 'data-testid="phase-chips"' in rp, "phase-chips element missing from the row header"
    assert "gate_warnings" in rp, "⚠ chip must read gate_warnings"
    assert "executor_attempts" in rp, "↻ chip must read executor_attempts"
    assert "escalations" in rp, "⛔ chip must read escalations"
    for glyph in ("⚠", "↻", "⛔"):
        assert glyph in rp, f"chip glyph {glyph!r} not found"


def test_phase_expansion_lists_warning_codes(html):
    """The expanded Run Metrics block lists the gate-warning codes / reachability so the
    chips are explainable on drill-in."""
    rp = _roadmap_panel_slice(html)
    assert 'data-testid="phase-advisory-detail"' in rp, "phase-advisory-detail block missing"
    assert "reachability_summary" in rp


# ── operator_action — feed registry registration (P1-C events) ──────────────

def test_operator_action_registered_in_badge_color(html):
    """getEventBadgeColor must classify operator_action (not fall through to default)."""
    start = html.find("function getEventBadgeColor")
    end = html.find("const EVENT_TYPE_DISPLAY", start)
    assert start != -1 and end != -1
    assert "'operator_action'" in html[start:end], "operator_action missing a badge-colour case"


def test_operator_action_registered_in_display_and_description(html):
    """operator_action has a short feed label and a hover description."""
    disp_start = html.find("const EVENT_TYPE_DISPLAY")
    disp_end = html.find("const EVENT_TYPE_DESCRIPTION", disp_start)
    assert "operator_action:" in html[disp_start:disp_end], "operator_action missing from EVENT_TYPE_DISPLAY"
    desc_start = html.find("const EVENT_TYPE_DESCRIPTION")
    desc_end = html.find("function getEventDescription", desc_start)
    assert "operator_action:" in html[desc_start:desc_end], "operator_action missing from EVENT_TYPE_DESCRIPTION"


def test_operator_action_humanized(html):
    """humanizeSummary renders operator_action as readable prose keyed off detail.action
    (e.g. stop / queue_add / launch) rather than raw JSON."""
    hs = html.find("function humanizeSummary(event)")
    assert hs != -1
    block = html[hs: hs + 9000]
    assert "case 'operator_action'" in block, "humanizeSummary missing operator_action case"
    # keyed off the action verb, covering representative actions
    assert "d.action" in block
    for action in ("stop", "queue_add", "launch"):
        assert action in block, f"operator_action humanize should map the {action!r} action"
