"""UI REVIEW Phase 5 (R1) — "waiting for parent" visibility for ANY incomplete parent.

The queue must surface that a child is waiting whenever its parent is not COMPLETED — not
only when the child's state is literally ``DEPENDENCY_HOLD``. The single source of truth is
the ``isWaitingForParent(parentEntry)`` helper (mirrors the orchestrator selection-skip,
``parent_state != "COMPLETED"``). These are static substring checks on ``ui/index.html`` —
the house idiom, since no JSX transpiler runs in CI (render gates are pinned by substring).
"""
import re
from pathlib import Path

import pytest

INDEX_HTML = Path(__file__).parent.parent / "ui" / "index.html"


@pytest.fixture
def html():
    return INDEX_HTML.read_text(encoding="utf-8")


def _queue_action_hub_block(html):
    m = re.search(r"function QueueActionHub\(\)(.*?)(?=\n\s+function [A-Z])", html, re.DOTALL)
    assert m, "QueueActionHub not found in index.html"
    return m.group(0)


def _queue_row_region(html):
    """Column-1 queue-list render region (between the two column markers)."""
    start = html.index("Column 1: Queue list")
    end = html.index("Column 2: Project snapshot", start)
    return html[start:end]


# ── The shared predicate helper ───────────────────────────────────────────────

def test_helper_defined(html):
    assert "function isWaitingForParent" in html, (
        "the shared isWaitingForParent(parentEntry) helper must exist"
    )


def test_helper_excludes_completed_parent(html):
    """The helper must treat a COMPLETED parent as NOT waiting (else every child with a
    finished parent would read as waiting forever)."""
    i = html.index("function isWaitingForParent")
    body = html[i:i + 220]
    assert "parentEntry" in body
    assert "!== 'COMPLETED'" in body


# ── Action hub: one shared panel, rendered in both branches ───────────────────

def test_action_hub_shares_waiting_panel(html):
    """The 'Waiting for:' panel is a single shared const rendered in BOTH the
    DEPENDENCY_HOLD branch and the READY/SKIPPED_PENDING default branch — not forked into
    one branch only (the R1 gap where a READY child with an incomplete parent showed a bare
    'Depends on' selector that looked startable)."""
    hub = _queue_action_hub_block(html)
    assert "const waitingForParentPanel" in hub, (
        "the shared waitingForParentPanel const must be defined once in QueueActionHub"
    )
    assert hub.count("{waitingForParentPanel}") >= 2, (
        "waitingForParentPanel must be rendered in both the DEPENDENCY_HOLD and the "
        "READY/default action-hub branches"
    )


def test_action_hub_panel_gated_on_helper(html):
    hub = _queue_action_hub_block(html)
    assert "isWaitingForParent(" in hub, (
        "the action-hub waiting panel must be gated on isWaitingForParent(), not a state literal"
    )


# ── Queue row: muted 'Waiting for <parent>' sub-line ──────────────────────────

def test_queue_row_waiting_subline(html):
    """A child waiting on an incomplete parent shows a muted 'Waiting for <parent>'
    sub-line — gated on the helper and suppressed on DEPENDENCY_HOLD rows (those already
    show the orange 'Waiting on parent' pill, so a sub-line there would double up)."""
    row = _queue_row_region(html)
    assert "isWaitingForParent(parentEntry)" in row
    assert "Waiting for " in row
    assert "entry.state !== 'DEPENDENCY_HOLD'" in row
