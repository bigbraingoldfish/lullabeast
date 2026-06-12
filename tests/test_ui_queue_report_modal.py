"""METRICS-E4 — "View report" on COMPLETED queue rows → durable report modal.

Static contracts on ``ui/index.html`` (single-file CDN-React, no JS build or
test runner — same approach as the sibling ``tests/test_ui_*.py``).

The modal renders the SAME ``PipelineCompletePanel`` the Pipeline Monitor
shows at PIPELINE_COMPLETE, fed per-project by ``GET /api/queue/{id}/report``
— so a completed project's summary stays recallable after the queue moves on,
instead of being a one-time Monitor snapshot.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"


def load_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def extract_function(html, func_name):
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


def window(html, anchor, size=2200):
    i = html.find(anchor)
    assert i != -1, f"anchor {anchor!r} not found in index.html"
    return html[i : i + size]


def test_completed_action_hub_has_view_report():
    html = load_html()
    body = window(html, "// COMPLETED", 1800)
    assert 'data-testid="queue-view-report"' in body
    assert "handleViewReport" in body


def test_view_report_fetches_report_endpoint():
    html = load_html()
    body = window(html, "const handleViewReport", 900)
    assert "/report" in body and "/api/queue/" in body


def test_report_modal_renders_complete_panel():
    """The modal reuses PipelineCompletePanel — one completion-summary markup
    source for both the Monitor and the Queue — fed phaseCounts (the modal has
    no roadmap array) and the entry's completion_report."""
    html = load_html()
    body = extract_function(html, "QueueReportModal")
    assert body is not None, "QueueReportModal component must exist"
    assert "PipelineCompletePanel" in body
    assert "phaseCounts" in body
    assert "metrics_summary" in body
    # Found reports render their markdown through the shared renderer.
    assert "renderCompletionMarkdown" in body
    # Generate-docs is Monitor-only (it targets the ACTIVE project's webhook):
    # the modal must NOT pass onGenerateDocs.
    assert "onGenerateDocs" not in body


def test_complete_panel_phase_counts_fallback():
    """PipelineCompletePanel accepts phaseCounts overriding the roadmap-derived
    header counts (the queue modal has counts, not a roadmap array)."""
    body = extract_function(load_html(), "PipelineCompletePanel")
    assert "phaseCounts" in body


def test_complete_panel_gates_generate_docs_on_handler():
    """The generate-docs CTA only renders when an onGenerateDocs handler is
    provided — the queue modal passes none, the Monitor keeps passing one."""
    html = load_html()
    body = extract_function(html, "PipelineCompletePanel")
    assert re.search(r"\{onGenerateDocs\s*&&", body), (
        "generate-docs block must be gated on the onGenerateDocs prop"
    )
    # Monitor regression — CurrentPhasePanel still wires the handler through.
    assert "onGenerateDocs={onGenerateDocs}" in html
