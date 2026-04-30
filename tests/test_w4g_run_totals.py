"""W4-G: "Run so far" totals line below progress bar in RoadmapPanel.

Static content checks — no server needed.
"""
from pathlib import Path


def _html() -> str:
    return (Path(__file__).parent.parent / "ui" / "index.html").read_text(encoding="utf-8")


def _roadmap_panel_body(html: str) -> str:
    start = html.index("const RoadmapPanel")
    end = html.index("// ─── Activity Feed", start)
    return html[start:end]


def test_run_so_far_copy():
    """'Run so far:' string is present in RoadmapPanel."""
    body = _roadmap_panel_body(_html())
    assert "Run so far:" in body


def test_total_phases_guard():
    """Line only renders when total_phases > 0."""
    body = _roadmap_panel_body(_html())
    assert "total_phases > 0" in body


def test_total_executor_attempts_shown():
    """total_executor_attempts referenced in the run totals line."""
    body = _roadmap_panel_body(_html())
    assert "total_executor_attempts" in body


def test_duration_formatted():
    """total_duration_seconds passed through formatDuration."""
    body = _roadmap_panel_body(_html())
    assert "formatDuration(metricsSummary.total_duration_seconds)" in body


def test_metrics_summary_guard():
    """Whole block gated on metricsSummary being truthy."""
    body = _roadmap_panel_body(_html())
    # Should have both metricsSummary and total_phases guards
    assert "metricsSummary" in body
    assert "total_phases > 0" in body
