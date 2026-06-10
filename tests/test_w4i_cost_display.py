"""W4-I: Cost display with mandatory zero-suppression across three surfaces.

Static content checks — no server needed. All surfaces zero-suppress
since no existing API endpoint exposes cost_total (no server.py changes
in Wave 4). Code ships now; suppression handles 0/null/absent silently.
"""
from pathlib import Path


def _html() -> str:
    return (Path(__file__).parent.parent / "ui" / "index.html").read_text(encoding="utf-8")


def _snapshot_body(html: str) -> str:
    start = html.index("function QueueProjectSnapshot(")
    end = html.index("// ── Row expansion: Action Hub", start)
    return html[start:end]


def _roadmap_panel_body(html: str) -> str:
    start = html.index("const RoadmapPanel")
    end = html.index("// ─── Activity Feed", start)
    return html[start:end]


def test_no_zero_dollar_string():
    """$0.00 must never appear — zero-suppression is mandatory."""
    assert "$0.00" not in _html()


def test_cost_zero_suppression_guard_present():
    """cost_total > 0 guard is present (at least once) for zero-suppression."""
    html = _html()
    assert "cost_total > 0" in html


def test_cost_formatted_to_two_decimal_places():
    """Cost uses .toFixed(2) for currency formatting."""
    html = _html()
    assert ".toFixed(2)" in html


# Surface 1: Queue snapshot cost
def test_queue_snapshot_cost_surface():
    """QueueProjectSnapshot conditionally shows cost when snap.cost_total > 0."""
    snapshot = _snapshot_body(_html())
    assert "cost_total" in snapshot
    assert "cost_total > 0" in snapshot


# Surface 2: W4-G "Run so far" line extended with cost
def test_run_so_far_cost_extension():
    """RoadmapPanel 'Run so far' line includes cost when metricsSummary.cost_total > 0."""
    body = _roadmap_panel_body(_html())
    assert "cost_total" in body


# Surface 3: Per-phase cost in phase detail dropdown
def test_phase_detail_cost_surface():
    """Phase detail metrics grid shows per-phase cost when phaseMeta.cost_total > 0."""
    body = _roadmap_panel_body(_html())
    assert "phaseMeta.cost_total" in body


def test_metrics_global_not_referenced():
    """metrics-global must not appear in index.html (guard test)."""
    assert "metrics-global" not in _html()
