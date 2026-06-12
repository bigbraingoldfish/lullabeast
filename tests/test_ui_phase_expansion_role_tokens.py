"""METRICS-E2 — per-role token data in the Run Metrics phase expansion.

Static contracts on ``ui/index.html`` (single-file CDN-React, no JS build or
test runner — same approach as the sibling ``tests/test_ui_*.py``).

History: E2 first surfaced the per-phase role-token split
(``planner_tokens`` / ``executor_tokens`` / ``reviewer_tokens`` from
``/api/metrics-summary``) as a muted ``fmtTokenRoleSplit`` sub-line. The
monitor redesign (2026-06-12) superseded that sub-line with the BY AGENT
breakout card (shared ``SplitStatCard``; layout pinned in
``tests/test_ui_monitor_redesign.py``); the formatter was removed with its
only consumer. These keep pinning the E2 *data* contract: the expansion
consumes all three per-phase role-token keys, and the dead formatter stays
dead.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"


def load_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def window(html, anchor, size=4500):
    i = html.find(anchor)
    assert i != -1, f"anchor {anchor!r} not found in index.html"
    return html[i : i + size]


def test_phase_expansion_consumes_role_token_keys():
    """The BY AGENT card reads the per-phase role-token fields E2 added to
    /api/metrics-summary — dropping any of them silently blanks a row."""
    block = window(load_html(), 'data-testid="phase-breakout-cards"')
    for key in ("planner_tokens", "executor_tokens", "reviewer_tokens"):
        assert f"phaseMeta.{key}" in block, (
            f"the BY AGENT breakout must consume phaseMeta.{key}"
        )


def test_role_split_formatter_stays_removed():
    """fmtTokenRoleSplit was deleted with its only consumer (the sub-line the
    BY AGENT card replaced) — it must not quietly return as dead code."""
    assert "fmtTokenRoleSplit" not in load_html()
