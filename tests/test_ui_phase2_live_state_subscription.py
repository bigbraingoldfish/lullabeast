"""Phase 2 — live state subscription: static-source contract tests.

UI REVIEW roadmap Phase 2 fixes three "the screen doesn't subscribe to live state"
bugs:

* 0.1 — global ``pipelineStatus`` was written only by ``PipelineScreen`` (stale on
  every other screen). Ownership moves to ``App`` so ``ctx.pipelineStatus`` stays
  live regardless of which screen is mounted. ``QueueScreen`` keeps reading it from
  context and deliberately still does NOT fetch ``/api/state`` itself (the per-entry
  advisory stays snapshot-sourced — guarded by
  ``test_ui_queue_escalation_uses_shared_panel.py``).
* 0.2 — ``QueueScreen`` only fetched on mount + after mutations, so it went stale as
  the orchestrator advanced the queue. It now polls, skipping while a reorder is
  in flight.
* 0.3 — ``fetchMetricsSummary`` was one-shot on mount, so cost/metrics froze mid-run.
  It now refreshes on an interval.

These are static contracts on ``ui/index.html`` (single-file CDN-React, no JS build or
test runner — same approach as the other ``tests/test_ui_*.py``). They guard against
regressions to the per-screen / one-shot patterns that caused the bugs.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"


def _index_text() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _block(html: str, start_pat: str, end_pat: str) -> str:
    """Return the source of a top-level component, from ``start_pat`` up to ``end_pat``."""
    m = re.search(start_pat + r"(.*?)(?=" + end_pat + r")", html, re.DOTALL)
    assert m, f"block {start_pat!r}..{end_pat!r} not found in index.html"
    return m.group(1)


def _app_block(html: str) -> str:
    return _block(html, r"function App\(\)", r"ReactDOM\.createRoot")


def _queue_block(html: str) -> str:
    return _block(html, r"function QueueScreen\(\)", r"\n\s+function PipelineScreen")


def _pipeline_block(html: str) -> str:
    return _block(html, r"function PipelineScreen\(\)", r"\n\s+function FirstRunScreen")


# ── 0.1 — global pipeline status owned by App, live on every screen ───────────

def test_app_owns_live_pipeline_status_poll():
    """App must run a recurring ``/api/state`` poll that writes ``setPipelineStatus``,
    so the shared ``ctx.pipelineStatus`` is fresh on every screen — not only while
    ``PipelineScreen`` happens to be mounted. Regresses to the stale-off-Pipeline bug
    if this poll is removed."""
    app = _app_block(_index_text())
    assert "setInterval" in app, "App must have a recurring poll"
    assert ('fetch("/api/state")' in app) or ("fetch('/api/state')" in app), (
        "App's status poll must fetch /api/state"
    )
    assert "setPipelineStatus(" in app, "App must call setPipelineStatus (it owns the writer)"


def test_pipeline_screen_no_longer_writes_pipeline_status():
    """The ``PipelineScreen``-scoped writer (the 0.1 bug source) must be gone — App owns
    it now. A ``setPipelineStatus`` anywhere in PipelineScreen re-introduces the
    single-screen-writer that left other screens stale."""
    pipe = _pipeline_block(_index_text())
    assert "setPipelineStatus" not in pipe, (
        "PipelineScreen must not write ctx.pipelineStatus — App owns the live poll now"
    )


# ── 0.2 — Queue screen live-refreshes (skipping reorder) ──────────────────────

def test_queue_screen_polls_for_live_refresh():
    """QueueScreen must poll so the list reflects orchestrator-driven advance
    (ACTIVE->COMPLETED, dependency clears, escalation) without a manual refresh.
    Regresses to the mount-only-fetch staleness if the interval is removed."""
    q = _queue_block(_index_text())
    assert "setInterval" in q, "QueueScreen must poll on an interval"
    idx = q.find("setInterval")
    window = q[idx : idx + 240]
    assert "fetchQueue" in window, "the Queue poll must call fetchQueue"


def test_queue_poll_skips_while_reordering():
    """The poll must not clobber an in-flight reorder (or spam the reorder-dirty error),
    so the interval callback guards on ``reorderModeRef``."""
    q = _queue_block(_index_text())
    idx = q.find("setInterval")
    assert idx != -1, "no setInterval in QueueScreen"
    window = q[idx : idx + 240]
    assert "reorderModeRef" in window, (
        "Queue poll must skip while reordering (guard on reorderModeRef.current)"
    )


# ── 0.3 — Pipeline metrics refresh mid-run ────────────────────────────────────

def test_pipeline_metrics_summary_is_polled():
    """``fetchMetricsSummary`` must be on the interval so cost/metrics update during a
    run. Regresses to the one-shot-on-mount stale-cost bug if dropped from the interval."""
    pipe = _pipeline_block(_index_text())
    assert re.search(r"setInterval\(\s*fetchMetricsSummary", pipe), (
        "fetchMetricsSummary must be polled on an interval, not only fetched on mount"
    )
