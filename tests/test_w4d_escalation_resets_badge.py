"""W4-D: escalation_resets badge in CurrentPhasePanel.

Static content checks — no server needed.
"""
from pathlib import Path
import re


def _html() -> str:
    return (Path(__file__).parent.parent / "ui" / "index.html").read_text(encoding="utf-8")


def test_reset_chip_guard_exists():
    """CurrentPhasePanel renders badge only when escalation_resets > 0."""
    html = _html()
    assert "escalation_resets > 0" in html


def test_reset_chip_copy_exists():
    """Badge displays 'Reset ×N' copy."""
    html = _html()
    assert "Reset ×" in html  # "Reset ×"


def test_reset_chip_in_current_phase_panel():
    """Badge is defined inside CurrentPhasePanel, not elsewhere."""
    html = _html()
    # Find the CurrentPhasePanel function body (between its definition and the next top-level function)
    start = html.index("function CurrentPhasePanel(")
    # RoadmapPanel follows CurrentPhasePanel
    end = html.index("const RoadmapPanel", start)
    panel_body = html[start:end]
    assert "Reset ×" in panel_body


def test_reset_chip_not_in_queue_action_hub():
    """Badge copy must not bleed into QueueActionHub (separate surface)."""
    html = _html()
    hub_start = html.index("function QueueActionHub(")
    hub_end = html.index("// ── Column 3:", hub_start - 200)  # approx end
    # QueueActionHub ends before PipelineScreen; just check the hub region doesn't carry the badge
    hub_region = html[hub_start : hub_start + 4000]
    # The hub shows escalation count but not the 'Reset ×' chip
    assert "Reset ×" not in hub_region
