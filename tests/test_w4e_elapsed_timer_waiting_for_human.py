"""W4-E: ElapsedTimer shows elapsed time when WAITING_FOR_HUMAN + waiting_for_human_at.

Static content checks — no server needed.
"""
from pathlib import Path


def _html() -> str:
    return (Path(__file__).parent.parent / "ui" / "index.html").read_text(encoding="utf-8")


def _elapsed_timer_body(html: str) -> str:
    start = html.index("function ElapsedTimer(")
    end = html.index("function splitApiDetail(", start)
    return html[start:end]


def _current_phase_panel_body(html: str) -> str:
    start = html.index("function CurrentPhasePanel(")
    end = html.index("const RoadmapPanel", start)
    return html[start:end]


def test_waiting_for_human_at_prop_in_elapsed_timer():
    """ElapsedTimer accepts a waitingForHumanAt prop."""
    body = _elapsed_timer_body(_html())
    assert "waitingForHumanAt" in body


def test_waiting_for_human_tick_anchor_logic():
    """WAITING_FOR_HUMAN state uses waitingForHumanAt as tick anchor."""
    body = _elapsed_timer_body(_html())
    assert 'pipeline_status === "WAITING_FOR_HUMAN"' in body
    assert "waitingForHumanAt" in body


def test_no_unconditional_null_return_for_waiting_for_human():
    """ElapsedTimer no longer returns null unconditionally for WAITING_FOR_HUMAN."""
    body = _elapsed_timer_body(_html())
    # The old pattern was: if (pipeline_status === "WAITING_FOR_HUMAN") return null;
    # This must no longer be a plain unconditional early return
    assert 'if (pipeline_status === "WAITING_FOR_HUMAN") return null;' not in body


def test_waiting_for_human_at_threaded_through_current_phase_panel():
    """CurrentPhasePanel receives and passes through waiting_for_human_at."""
    panel = _current_phase_panel_body(_html())
    assert "waiting_for_human_at" in panel
    assert "waitingForHumanAt" in panel


def test_waiting_for_human_at_wired_at_call_site():
    """pState.waiting_for_human_at is passed to CurrentPhasePanel."""
    html = _html()
    assert "waiting_for_human_at={pState.waiting_for_human_at}" in html
