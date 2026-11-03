"""Cost figures that are still list-price estimates carry an "est." label.

OpenRouter publishes a call's billed cost a minute or two after the call, and
other providers report none, so part of a run's cost can be a list-price
estimate (``cost_estimated`` from /api/metrics-summary, /api/state and the
queue endpoints). Static contracts on ``ui/index.html``, the same approach as
the sibling ``tests/test_ui_*.py``.
"""
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parent.parent / "ui" / "index.html"


def _html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _window(html, anchor, size=2500):
    i = html.find(anchor)
    assert i != -1, f"anchor {anchor!r} not found in index.html"
    return html[i : i + size]


def test_cost_figure_labels_the_estimated_part():
    body = _window(_html(), "function CostFigure(", 1500)
    assert "est." in body
    assert "fmtUSD(value)" in body
    assert "title=" in body and "list price" in body


def test_run_total_cost_is_labeled():
    body = _window(_html(), 'data-testid="run-total-cost-toggle"', 1500)
    assert "<CostFigure value={metricsSummary.total_cost} estimated={metricsSummary.cost_estimated}" in body


def test_live_phase_spend_is_labeled():
    body = _window(_html(), 'data-testid="live-phase-spend"', 1500)
    assert "<CostFigure value={liveCost} estimated={liveTok && liveTok.cost_estimated}" in body


def test_phase_table_cost_cells_are_labeled():
    body = _window(_html(), "function PhaseMetricsTable(", 3500)
    assert "<CostFigure value={phase.cost_total} estimated={phase.cost_estimated}" in body


def test_completion_total_cost_is_labeled():
    body = _window(_html(), "Total Cost</div>", 600)
    assert "<CostFigure value={totalCost} estimated={metricsSummary.cost_estimated}" in body


def test_phase_by_agent_cost_is_labeled():
    body = _window(_html(), 'title="By agent"', 900)
    assert "phaseMeta.cost_estimated" in body


def test_queue_cost_chip_and_snapshot_are_labeled():
    html = _html()
    chip = _window(html, 'data-testid="queue-metrics-chip-cost"', 900)
    assert "<CostFigure value={entry.cost_total} estimated={entry.cost_estimated}" in chip
    snap = _window(html, "const costValue = snap", 400)
    assert "snap.cost_estimated" in snap
