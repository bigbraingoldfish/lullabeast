"""METRICS-E3 — Queue metric chips + per-phase breakout expansion.

Static contracts on ``ui/index.html`` (single-file CDN-React, no JS build or
test runner — same approach as the sibling ``tests/test_ui_*.py``).

Design (binding, from the observability pass):
- Each queue row carries compact metric chips for cost and tokens — quiet,
  scannable summaries; NO per-phase data inline in the collapsed row.
- Clicking a chip expands that project's row in a metrics-breakout view:
  per-phase cost/token table plus summary, matching the Pipeline Monitor's
  presentation (the table markup is SHARED via ``PhaseMetricsTable``).
- The expansion is the only place per-phase detail appears on the Queue.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"


def load_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def window(html, anchor, size=2200):
    i = html.find(anchor)
    assert i != -1, f"anchor {anchor!r} not found in index.html"
    return html[i : i + size]


def test_fmt_tokens_compact_helper_defined():
    """Chip-sized token formatting: 11_393_463 → '11.4M' (full toLocaleString
    is too wide for a row chip). Null/0 contract mirrors fmtTokens."""
    html = load_html()
    assert re.search(r"function fmtTokensCompact\s*\(", html), (
        "fmtTokensCompact must be defined for the row chips"
    )
    body = window(html, "function fmtTokensCompact", 800)
    assert "1e6" in body and "1e3" in body, "compact M/k thresholds expected"


def test_queue_header_metrics_column():
    """The COST column header becomes METRICS (it now hosts both chips)."""
    html = load_html()
    body = window(html, "<span className=\"min-w-0\">PROJECT</span>", 800)
    assert "METRICS" in body
    assert ">COST<" not in body


def test_queue_row_renders_metric_chips():
    html = load_html()
    assert 'data-testid="queue-metrics-chip-cost"' in html
    assert 'data-testid="queue-metrics-chip-tokens"' in html
    # Token chip reads the enrichment's tokens_total via compact formatting.
    chip = window(html, 'data-testid="queue-metrics-chip-tokens"', 700)
    assert "fmtTokensCompact(entry.tokens_total)" in chip


def test_chip_click_opens_metrics_breakout():
    """Chip click expands the row AND switches the expansion to the Cost &
    Tokens tab (QT-3 superseded the standalone breakout view with the tab;
    stopPropagation so the row's own click toggle doesn't fight it). Both
    chips share the openMetricsBreakout handler."""
    html = load_html()
    handler = window(html, "const openMetricsBreakout", 700)
    assert "stopPropagation" in handler
    assert "setExpansionTab('metrics')" in handler
    assert "setExpandedId(entry.id)" in handler
    for chip_id in ("queue-metrics-chip-cost", "queue-metrics-chip-tokens"):
        chip = window(html, f'data-testid="{chip_id}"', 400)
        assert "openMetricsBreakout" in chip
    # Plain row click returns to the overview tab.
    assert html.count("setExpansionTab('overview')") >= 1


def test_breakout_renders_shared_phase_table():
    """The Cost & Tokens tab (QT-4, superseding QueueMetricsBreakout) consumes
    snapshot.metrics_phases; the completion panel keeps the shared
    PhaseMetricsTable — one per-phase data source across surfaces."""
    html = load_html()
    assert re.search(r"function PhaseMetricsTable\s*\(", html), (
        "the per-phase metrics table must be a shared component"
    )
    tab = window(html, "function QueueCostTokensTab", 3500)
    assert "metrics_phases" in tab


def test_completion_panel_uses_shared_phase_table():
    """PipelineCompletePanel's per-phase table now routes through the shared
    component — the duplicated inline markup is gone."""
    html = load_html()
    body = window(html, "function PipelineCompletePanel", 9000)
    assert "PhaseMetricsTable" in body


def test_collapsed_row_has_no_per_phase_data():
    """Binding design rule: no per-phase metrics inline in the collapsed row —
    the chips are totals only (the breakout owns the detail)."""
    html = load_html()
    chip_zone = window(html, 'data-testid="queue-metrics-chip-cost"', 1200)
    assert "metrics_phases" not in chip_zone
