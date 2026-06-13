"""Queue expansion redesign (QT-2/3/4) — static contracts on ui/index.html.

Design (operator mock, 2026-06-12):
- QT-3 — the row expansion gains sub-tabs: OVERVIEW (default) | COST & TOKENS.
  A metrics chip click quick-opens the Cost & Tokens tab; a plain row click
  opens Overview. The Overview stretches on wide windows (higher width cap,
  responsive 4-card stat grid with a Tokens card; cost/tokens cards link
  "view breakdown ›" into the tab).
- QT-4 — Cost & Tokens tab: a Monitor-style header strip (PHASES RECORDED +
  ACTIVE TIME, plus Total cost / Total tokens pills → split cards; cost
  full-width, tokens by-type + by-agent). Below: SPEND BY PHASE — legend,
  tokens/$ toggle ($ suppressed when no cost), timeline/top-spend sort,
  per-phase share-by-agent bar, value, % of project, exec count, duration,
  outcome badge; clicking a phase reveals the BY AGENT + BY TOKEN TYPE cards
  honoring the tokens/$ toggle.
- QT-2 — outcome badges are a shared pill component (PhaseOutcomeBadge) used
  by BOTH the spend table and the completion report's per-phase table.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"


def load_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def window(html, anchor, size=3000):
    i = html.find(anchor)
    assert i != -1, f"anchor {anchor!r} not found in index.html"
    return html[i : i + size]


# ── QT-3: sub-tabs + overview stretch ────────────────────────────────────────

def test_expansion_has_subtabs():
    """Tab buttons are emitted from a [['overview',…],['metrics',…]] map with a
    templated testid (queue-tab-{id})."""
    html = load_html()
    assert "queue-tab-${id}" in html
    bar = window(html, "queue-tab-${id}", 700)
    assert "setExpansionTab(id)" in bar
    tabs = window(html, "[['overview', 'Overview'], ['metrics', 'Cost & Tokens']]", 200)
    assert "Cost & Tokens" in tabs


def test_chip_click_opens_metrics_tab():
    """The row's metric chips quick-open the Cost & Tokens tab (replacing the
    former QueueMetricsBreakout view, which is gone)."""
    html = load_html()
    handler = window(html, "const openMetricsBreakout", 700)
    assert "setExpandedId(entry.id)" in handler
    assert "'metrics'" in handler
    assert "QueueMetricsBreakout" not in html, "superseded by the Cost & Tokens tab"
    assert "metricsViewId" not in html, "superseded by expansionTab"


def test_row_click_defaults_to_overview():
    html = load_html()
    assert "'overview'" in window(html, "setExpandedId(prev => prev === entry.id ? null : entry.id)", 600)


def test_overview_stretches_on_wide_windows():
    """Width cap raised + responsive stat grid (2 cols → 4 on large screens)."""
    html = load_html()
    pane = window(html, "{/* The WRAPPER fills", 1400)
    assert "max-w-2xl" not in pane, "the old narrow cap must be gone"
    grid = window(html, "{cards.map((c, i) =>", 600)
    assert "lg:grid-cols-4" in window(html, "cards.length >= 4", 600)


def test_overview_cards_link_to_breakdown():
    """Cost and Tokens stat cards carry a 'view breakdown ›' action that jumps
    to the Cost & Tokens tab."""
    html = load_html()
    assert html.count("view breakdown ›") >= 2
    card_zone = window(html, "view breakdown ›", 1200)
    assert "setExpansionTab('metrics')" in window(html, "const breakdownLink", 700)


# ── QT-4: header strip + spend by phase ──────────────────────────────────────

def test_metrics_tab_header_strip():
    html = load_html()
    strip = window(html, 'data-testid="queue-metrics-header-strip"', 4200)
    assert "Phases recorded" in strip
    assert "Active time" in strip
    assert 'data-testid="queue-total-cost-toggle"' in strip
    assert 'data-testid="queue-total-tokens-toggle"' in strip


def test_metrics_tab_totals_panels():
    """Cost pill → one full-width by-agent card; tokens pill → by-type +
    by-agent pair. Project-level splits are client-summed from the per-phase
    projection (QT-1 keys)."""
    html = load_html()
    cost = window(html, "{queueTotalsView === 'cost' && totalCost > 0 && (", 900)
    assert "SplitStatCard" in cost
    assert "sumPhases('planner_cost')" in cost
    toks = window(html, "{queueTotalsView === 'tokens' && totalTokens > 0 && (", 1600)
    assert toks.count("SplitStatCard") >= 2
    assert "sumPhases('planner_tokens')" in toks


def test_spend_by_phase_controls():
    html = load_html()
    block = window(html, 'data-testid="queue-spend-by-phase"', 5000)
    assert "Spend by phase" in block
    # Legend — one dot per role, generated from the shared role colors.
    assert "Object.entries(ROLE_SPLIT_COLORS)" in block
    # tokens/$ toggle, $ suppressed without cost; timeline/top-spend sort.
    assert 'data-testid="queue-spend-mode-toggle"' in block
    assert 'data-testid="queue-spend-sort-toggle"' in block
    assert "timeline" in block and "top spend" in block


def test_spend_rows_render_share_bar_and_metrics():
    html = load_html()
    rows = window(html, 'data-testid="queue-spend-row"', 3500)
    # Share-by-agent stacked bar segments (role colors).
    assert "ROLE_SPLIT_COLORS" in window(html, "const spendRoleVals", 1500) or \
           "roleSplitRows" in window(html, "const spendRoleVals", 1500)
    # Value · % of project · exec ×count · duration · outcome badge.
    assert "fmtPct" in rows
    assert "PhaseOutcomeBadge" in rows
    assert "formatDuration" in rows


def test_spend_sort_modes():
    html = load_html()
    body = window(html, "const sortedSpendPhases", 700)
    assert "spendSort === 'top'" in body


def test_spend_phase_click_reveals_breakout_cards():
    """Clicking a spend row reveals the BY AGENT + BY TOKEN TYPE cards (same
    SplitStatCard pair as the Monitor), honoring the tokens/$ toggle."""
    html = load_html()
    block = window(html, "{spendExpandedPhase === p.phase", 3200)
    assert block.count("SplitStatCard") >= 2
    assert "spendModeEffective" in block or "spendMode" in block


# ── QT-2: shared outcome badge ───────────────────────────────────────────────

def test_phase_outcome_badge_shared():
    html = load_html()
    assert re.search(r"function PhaseOutcomeBadge\s*\(", html)
    # Single-word labels, detail in tooltip.
    badge = window(html, "function phaseOutcome", 1100)
    assert "'escalated'" in badge and "'retried'" in badge and "'clean'" in badge
    assert "escalated + retried" in badge, "compound detail lives in the tooltip"
    # Completion report's table uses the same badge (no bespoke labels left).
    table = window(html, "function PhaseMetricsTable", 4200)
    assert "PhaseOutcomeBadge" in table
    assert "outcomeBadge" not in table, "bespoke outcome markup replaced by the shared badge"
