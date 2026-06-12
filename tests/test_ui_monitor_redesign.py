"""Pipeline Monitor redesign (MON-2/3/4) — static contracts on ui/index.html.

Design (operator mock, 2026-06-12):
- MON-2 — the "Run so far" prose line is replaced by a formal header strip:
  EXEC ATTEMPTS + WALL CLOCK stats, plus TOTAL COST / TOTAL TOKENS toggle
  pills (zero-suppressed). Cost expands ONE full-width by-agent panel; tokens
  expand BY TYPE + BY AGENT side by side. Toggles are mutually exclusive.
  Phases-complete is NOT repeated (the progress bar covers it).
- MON-3 — the phase row collapses ONLY from its name/header block; clicks in
  the expanded body (verification / run metrics) no longer collapse it.
  Collapsed rows show a muted right-side summary ("9m · 2.73M tok · 2 att").
- MON-4 — Run Metrics: header with skill + model badges, four stat boxes
  (Duration / Exec attempts / Reviewer passes / Escalations), then BY AGENT
  (tokens/$ toggle, $ hidden when cost is 0) + BY TOKEN TYPE breakout cards —
  stacked proportion bars + % of phase total, via the shared SplitStatCard.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"


def load_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def window(html, anchor, size=2500):
    i = html.find(anchor)
    assert i != -1, f"anchor {anchor!r} not found in index.html"
    return html[i : i + size]


# ── MON-2: header strip ──────────────────────────────────────────────────────

def test_run_so_far_line_removed():
    assert "Run so far:" not in load_html(), (
        "the prose Run-so-far line is replaced by the header strip"
    )


def test_header_strip_stats_and_toggles():
    html = load_html()
    strip = window(html, 'data-testid="roadmap-header-strip"', 4500)
    # One formal stat — Total time. Exec attempts and phases-complete are
    # deliberately absent (operator follow-up 2026-06-12: attempts live in
    # the per-phase Run Metrics boxes; the progress bar covers phases).
    assert "Total time" in strip
    assert "total_duration_seconds" in strip
    assert "total_executor_attempts" not in strip
    assert "total_phases} phases" not in strip
    # Zero-suppressed toggle pills.
    assert 'data-testid="run-total-cost-toggle"' in strip
    assert 'data-testid="run-total-tokens-toggle"' in strip
    assert "fmtTokensCompact(metricsSummary.total_tokens)" in strip


def test_header_toggles_mutually_exclusive():
    html = load_html()
    cost = window(html, 'data-testid="run-total-cost-toggle"', 700)
    assert "setShowRunCostSplit" in cost and "setShowRunTokenSplit(false)" in cost
    tok = window(html, 'data-testid="run-total-tokens-toggle"', 700)
    assert "setShowRunTokenSplit" in tok and "setShowRunCostSplit(false)" in tok


def test_header_panels_render_split_cards():
    """Cost → one full-width by-agent card; tokens → BY TYPE + BY AGENT pair."""
    html = load_html()
    cost_panel = window(html, "{showRunCostSplit && metricsSummary.total_cost > 0 && (", 900)
    assert "SplitStatCard" in cost_panel
    assert "planner_cost_total" in cost_panel
    tok_panel = window(html, "{showRunTokenSplit && metricsSummary.total_tokens > 0 && (", 1600)
    assert tok_panel.count("SplitStatCard") >= 2, "tokens expand two cards (by type + by agent)"
    assert "runClassRows" in tok_panel  # tokenClassRows(metricsSummary.tokens_breakdown)
    assert "planner_tokens_total" in tok_panel


# ── shared split card ────────────────────────────────────────────────────────

def test_split_stat_card_component():
    html = load_html()
    assert re.search(r"function SplitStatCard\s*\(", html)
    body = window(html, "function SplitStatCard", 2500)
    assert "fmtPct" in body, "rows carry % of total"
    # Stacked proportion bar segments are width-proportional and colored.
    assert "backgroundColor" in body
    # Zero rows are dropped (zero-suppression).
    assert ".filter(" in body


def test_fmt_pct_helper():
    html = load_html()
    body = window(html, "function fmtPct", 700)
    assert "toPrecision(2)" in body, "sub-10% keeps two significant figures"
    assert "return null" in body, "zero total yields null (caller skips)"


# ── MON-3: header-only collapse + collapsed summary ─────────────────────────

def test_phase_collapse_is_header_only():
    """handlePhaseClick is wired ONLY on the phase-row header block; the
    expanded body lives outside it so verification/run-metrics clicks don't
    collapse the row."""
    html = load_html()
    header = window(html, 'data-testid="phase-row-header"', 900)
    assert "handlePhaseClick(phase.id)" in header
    # Exactly one click site + one keyboard site for the toggle.
    assert html.count("handlePhaseClick(phase.id)") == 2
    # The run-metrics body is NOT nested inside the header button: the header
    # block closes before the expanded sections render.
    assert "Run Metrics" not in header


def test_collapsed_rows_carry_no_metrics():
    """Operator follow-up (2026-06-12): the collapsed-row glance summary
    ("9m · 2.73M tok · 2 att") was cut — collapsed rows show name + goal
    only; ALL metrics detail lives behind the expansion."""
    html = load_html()
    assert "collapsedSummary" not in html
    assert 'data-testid="phase-collapsed-summary"' not in html


# ── MON-4: run metrics block ─────────────────────────────────────────────────

def test_run_metrics_badges():
    """RUN METRICS header is immediately followed by the skill badge and the
    model badge(s) (models_used, one per distinct model, roles in tooltip)."""
    html = load_html()
    block = window(html, 'data-testid="run-metrics-header"', 1800)
    assert "skill:" in block and "skill_used" in block
    assert "model:" in block and "modelEntries" in block
    # The badge source is the MON-1 models_used row field.
    assert "phaseMeta.models_used" in window(html, "const modelEntries", 600)


def test_run_metrics_stat_boxes_order():
    """Four stat boxes: Duration → Exec attempts (+ retry sub) → Reviewer
    passes → Escalations."""
    html = load_html()
    block = window(html, 'data-testid="run-metrics-boxes"', 3200)
    i_dur = block.index("Duration")
    i_att = block.index("Exec attempts")
    i_rev = block.index("Reviewer passes")
    i_esc = block.index("Escalations")
    assert i_dur < i_att < i_rev < i_esc
    assert "retrySubParts" in block, "retry-source sub-line expected in the attempts box"
    # The sub-line is fed by the lifetime retry counters (computed just above).
    assert "executor_self_failures" in window(load_html(), "const retrySubParts", 700)


def test_phase_breakout_cards():
    """BY AGENT (per-role) + BY TOKEN TYPE cards consume the per-phase keys."""
    html = load_html()
    block = window(html, 'data-testid="phase-breakout-cards"', 4500)
    for key in ("planner_tokens", "executor_tokens", "reviewer_tokens",
                "planner_cost", "executor_cost", "reviewer_cost"):
        assert f"phaseMeta.{key}" in block, f"breakout must consume phaseMeta.{key}"
    assert "SplitStatCard" in block
    # The BY TOKEN TYPE rows come from the phase's class breakdown.
    assert "phaseMeta.tokens_breakdown" in window(html, "const phaseClassRows", 400)


def test_phase_agent_toggle_gated_on_cost():
    """The tokens/$ toggle renders only when the phase has BOTH tokens and
    cost — matching the established zero-suppression (no $ view when cost
    was never captured)."""
    html = load_html()
    toggle = window(html, 'data-testid="phase-agent-split-toggle"', 900)
    assert "hasPhaseCost" in window(html, "const hasPhaseCost", 400)
    gate = window(html, "hasPhaseCost && hasPhaseTokens", 300)
    assert "phase-agent-split-toggle" in gate or 'data-testid="phase-agent-split-toggle"' in window(html, "hasPhaseCost && hasPhaseTokens", 1200)
    assert "setPhaseAgentSplitMode" in toggle


# ── removals (dead helpers replaced by the split cards) ─────────────────────

def test_replaced_formatters_removed():
    html = load_html()
    assert "fmtTokenRoleSplit" not in html, "superseded by the BY AGENT card"
    assert "fmtCostBreakdown" not in html, "superseded by the cost split card"


def test_verification_section_unified():
    """Exit criteria + behavioral fields render under one VERIFICATION header
    (mock style); the three behavioral fields are preserved."""
    html = load_html()
    assert "Behavioral Verification:" not in html
    block = window(html, ">Verification</", 2600)
    for expr in ("behavioral_verification.user_observable",
                 "behavioral_verification.how_to_check",
                 "behavioral_verification.failure_language"):
        assert expr in block, f"verification block must keep {expr}"
