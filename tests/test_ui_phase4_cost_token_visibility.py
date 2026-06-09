"""UI REVIEW Phase 4 (findings 3-B / 3-C / 3-D / 3-E) — Pipeline Monitor cost &
token visibility. Static contracts on ``ui/index.html`` (single-file CDN-React, no
JS build or test runner — same approach as the sibling ``tests/test_ui_*.py``).

* 3-B — run-total + per-phase token counts render: the RoadmapPanel "Run so far"
  line, the completion Total-Tokens card, and a per-phase Tokens column.
* 3-C — per-phase role cost (planner/executor/reviewer), already returned by
  ``/api/metrics-summary`` but previously unread, is now consumed in the
  RoadmapPanel expanded phase detail.
* 3-D — the figures read from ``metricsSummary``, which is polled @5s (Phase 2);
  ``test_metrics_summary_still_polled`` guards that the live interval stays.
* 3-E — ``COMMAND_LABELS`` gains PROCEED / NUCLEAR_RESET.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"


def load_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def extract_function(html, func_name):
    """Body of a named top-level JS function (same helper as the sibling UI tests)."""
    match = re.search(rf"\n([ \t]*)function {re.escape(func_name)}\s*\([^)]*\)\s*\{{", html)
    if not match:
        return None
    indent = match.group(1)
    body_start = match.end()
    remainder = html[body_start:]
    next_fn = re.search(rf"\n\n{re.escape(indent)}function \w", remainder)
    candidate = remainder[: next_fn.start()] if next_fn else remainder
    last_close = candidate.rfind(f"\n{indent}}}")
    return candidate[:last_close] if last_close != -1 else candidate


def window(html, anchor, size=1800):
    i = html.find(anchor)
    assert i != -1, f"anchor {anchor!r} not found in index.html"
    return html[i : i + size]


def test_run_so_far_renders_tokens():
    """3-B — the RoadmapPanel 'Run so far' line surfaces ``total_tokens`` so total
    spend-in-tokens is visible live during a run (it rides the @5s metrics poll)."""
    body = window(load_html(), "Run so far:", 600)
    assert "total_tokens" in body, (
        "the Run-so-far line must surface metricsSummary.total_tokens"
    )


def test_completion_panel_total_tokens():
    """3-B — PipelineCompletePanel renders the run-total token count and the per-role
    token totals (parallel to the existing per-role cost card)."""
    body = extract_function(load_html(), "PipelineCompletePanel")
    assert body is not None, "PipelineCompletePanel not found"
    assert "total_tokens" in body
    for key in ("planner_tokens_total", "executor_tokens_total", "reviewer_tokens_total"):
        assert key in body, f"completion panel must read {key}"


def test_completion_per_phase_token_column():
    """3-B — the completion per-phase table gains a Tokens column, gated like the
    Cost column (``hasTokenColumn`` mirrors ``hasCostColumn``)."""
    body = extract_function(load_html(), "PipelineCompletePanel")
    assert "hasTokenColumn" in body, "per-phase Tokens column gate missing"
    assert "tokens_total" in body


def test_roadmap_panel_per_phase_role_cost():
    """3-C — the RoadmapPanel expanded phase detail consumes the per-phase role-cost
    keys that ``/api/metrics-summary`` already returns (previously orphaned)."""
    # The per-phase role-cost keys are net-new consumers, unique to the RoadmapPanel
    # expanded "Run Metrics" detail (the orphan was the API output, never read in UI),
    # so a whole-source assertion is both robust and precise.
    html = load_html()
    for key in ("planner_cost", "executor_cost", "reviewer_cost"):
        assert f"phaseMeta.{key}" in html, (
            f"expanded phase detail must render phaseMeta.{key}"
        )


def test_command_labels_include_proceed_and_nuclear():
    """3-E — COMMAND_LABELS maps PROCEED / NUCLEAR_RESET to friendly labels (reused
    verbatim from ESCALATION_CMD_DEFS) instead of rendering the raw token."""
    body = window(load_html(), "const COMMAND_LABELS", 600)
    assert "PROCEED: 'Mark Complete'" in body
    assert "NUCLEAR_RESET: 'Reset Everything & Restart Phase'" in body


def test_metrics_summary_still_polled():
    """3-D regression guard — the new token/cost figures only update live because
    ``fetchMetricsSummary`` is on an interval (Phase 2). Complements
    ``test_ui_phase2_live_state_subscription``."""
    assert re.search(r"setInterval\(\s*fetchMetricsSummary", load_html()), (
        "fetchMetricsSummary must remain on an interval for live cost/token updates"
    )
