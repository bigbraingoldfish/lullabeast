"""W4-G: run-totals surface below the progress bar in RoadmapPanel.

Originally the prose "Run so far:" line; the monitor redesign (2026-06-12)
replaced it with the formal header strip (EXEC ATTEMPTS / WALL CLOCK +
TOTAL COST / TOTAL TOKENS toggle pills — see tests/test_ui_monitor_redesign.py).
These keep pinning the W4-G data contract: attempts + duration render, gated
on a non-empty metrics summary. Static content checks — no server needed.
"""
from pathlib import Path


def _html() -> str:
    return (Path(__file__).parent.parent / "ui" / "index.html").read_text(encoding="utf-8")


def _roadmap_panel_body(html: str) -> str:
    start = html.index("const RoadmapPanel")
    end = html.index("// ─── Activity Feed", start)
    return html[start:end]


def test_run_so_far_copy():
    """The run-totals surface is the header strip; the prose line is gone."""
    body = _roadmap_panel_body(_html())
    assert 'data-testid="roadmap-header-strip"' in body
    assert "Run so far:" not in body


def test_total_phases_guard():
    """Line only renders when total_phases > 0."""
    body = _roadmap_panel_body(_html())
    assert "total_phases > 0" in body


def test_total_executor_attempts_shown():
    """Run-level exec attempts were cut from the header strip (operator
    follow-up 2026-06-12) — attempts render per phase in the Run Metrics
    boxes instead. Pin the removal so they don't quietly return."""
    body = _roadmap_panel_body(_html())
    assert "total_executor_attempts" not in body


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
