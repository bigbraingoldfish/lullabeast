"""Tests for W5-D: Synthetic 'Completion Report' phase entry in pipeline monitor.

Uses static HTML analysis (no browser) — same pattern as test_ui_p0_03_preflight_step_track.py.
"""

import re

INDEX_HTML_PATH = "ui/index.html"


def load_html():
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


class TestCompletionReportSyntheticPhase:

    def test_completion_report_label_exists_in_html(self):
        """'Completion Report' string must appear somewhere in index.html (W5-D)."""
        html = load_html()
        assert "Completion Report" in html, (
            "'Completion Report' label not found in index.html — W5-D not implemented"
        )

    def test_completion_report_subtitle_present(self):
        """The subtitle telegraphing the report contents must be present."""
        html = load_html()
        assert "What was built" in html, (
            "Completion Report subtitle 'What was built' not found — W5-D not implemented"
        )

    def test_synthetic_phase_gated_on_pipeline_complete(self):
        """Synthetic row must be conditional on PIPELINE_COMPLETE status."""
        html = load_html()
        # The string 'Completion Report' must appear near a PIPELINE_COMPLETE guard
        # Find the block containing 'Completion Report'
        idx = html.find("Completion Report")
        assert idx != -1, "Completion Report not found"
        # Check nearby context (within 800 chars before the label) for PIPELINE_COMPLETE
        context = html[max(0, idx - 800): idx + 200]
        assert "PIPELINE_COMPLETE" in context, (
            "Completion Report synthetic phase must be gated on PIPELINE_COMPLETE — "
            "not found within 800 chars before the label"
        )

    def test_synthetic_phase_gated_on_found_flag(self):
        """Synthetic row must also check completionReport.found (file existence guard)."""
        html = load_html()
        idx = html.find("Completion Report")
        assert idx != -1, "Completion Report not found"
        context = html[max(0, idx - 800): idx + 200]
        # Look for .found check in the vicinity
        assert "found" in context, (
            "Completion Report synthetic phase must check .found flag — not found near label"
        )

    def test_completion_report_api_endpoint_referenced(self):
        """index.html must reference the /api/completion-report endpoint (W5-C consumer)."""
        html = load_html()
        assert "/api/completion-report" in html, (
            "/api/completion-report API call not found in index.html"
        )

    def test_previous_run_tag_logic_present(self):
        """'from previous run' or equivalent staleness tag logic must be in index.html."""
        html = load_html()
        assert "previous run" in html.lower() or "from previous" in html.lower(), (
            "'previous run' staleness tag logic not found in index.html — "
            "W5-D visibility rule (mtime vs started_at) not implemented"
        )


class TestCompletionReportUXImprovements:
    """UX improvements: non-collapsible, taller, auto-scroll, per-codeblock Copy button.

    Static-HTML assertions — same pattern as the class above. See plan sections 1 and 3.
    """

    def _completion_block(self, html):
        """Return ~2500 chars of HTML centred on the JSX synthetic phase row.

        Anchor on `aria-label="Completion Report"` (unique to the JSX row's outer
        div, never appears in the parser helper or copy-button component).
        """
        anchor = 'aria-label="Completion Report"'
        idx = html.find(anchor)
        assert idx != -1, (
            f"Anchor {anchor!r} not found — search anchor missing"
        )
        return html[max(0, idx - 500): idx + 2500]

    def test_completion_report_row_is_not_clickable(self):
        """The completion-report row must not toggle on click — drag-selecting CLI
        commands inside used to slam the section closed."""
        html = load_html()
        block = self._completion_block(html)
        # The outer row div (the one carrying our testid) must not declare onClick
        # or onKeyDown. We scope the check to the ~600 chars after the testid (the
        # outer div's attribute list and immediate children), not the whole block,
        # so a Copy button's onClick inside the body does not trigger this.
        idx = block.find('data-testid="completion-report-phase-row"')
        outer_div_region = block[idx: idx + 600]
        assert "onClick" not in outer_div_region, (
            "completion-report-phase-row must not have an onClick handler — the "
            "row is now non-collapsible (see plan §1)"
        )
        assert "onKeyDown" not in outer_div_region, (
            "completion-report-phase-row must not have an onKeyDown handler"
        )
        assert 'aria-expanded' not in outer_div_region, (
            "completion-report-phase-row must not declare aria-expanded — it is "
            "always expanded now"
        )

    def test_completion_report_no_collapse_chevron(self):
        """The ▼ / ▶ chevron characters must not appear inside the completion-
        report markup — there is nothing to collapse."""
        html = load_html()
        block = self._completion_block(html)
        assert "▼" not in block, "▼ chevron must be removed from completion-report block"
        assert "▶" not in block, "▶ chevron must be removed from completion-report block"

    def test_completion_report_taller_max_height(self):
        """The content scroller must be ≥ 75 vh and the old max-h-64 must be gone."""
        html = load_html()
        block = self._completion_block(html)
        # Accept any max-h-[…vh] of 75 or more, or any max-h-[NNN…px] of 600+.
        assert (
            "max-h-[75vh]" in block
            or "max-h-[80vh]" in block
            or "max-h-[85vh]" in block
            or "max-h-[90vh]" in block
        ), (
            "Completion report content area must use max-h-[75vh] (or taller) — "
            "see plan §1. Old max-h-64 is too short."
        )
        assert "max-h-64" not in block, (
            "Old max-h-64 must be removed from the completion-report block"
        )

    def test_completion_report_has_scroll_into_view(self):
        """index.html must call scrollIntoView for the completion-report row so the
        section is not hidden under the phase list when the pipeline finishes."""
        html = load_html()
        assert "scrollIntoView" in html, (
            "scrollIntoView call missing from index.html — auto-scroll on completion "
            "report appearance is not wired (plan §1)"
        )
        # The scroll must target the completion-report-phase-row data-testid.
        # We don't require strict adjacency, but the testid string should appear
        # in a querySelector or getElementById context.
        assert (
            'querySelector(\'[data-testid="completion-report-phase-row"]\')' in html
            or "querySelector(`[data-testid=\"completion-report-phase-row\"]`)" in html
            or 'querySelector("[data-testid=\\"completion-report-phase-row\\"]")' in html
        ), (
            "scrollIntoView target must be the completion-report-phase-row testid"
        )

    def test_completion_report_has_copy_button_markup(self):
        """Each fenced code block must render a Copy button using the clipboard API."""
        html = load_html()
        assert 'data-testid="completion-report-copy-button"' in html, (
            "Per-codeblock Copy button missing — expected data-testid="
            '"completion-report-copy-button" (plan §1)'
        )
        assert "navigator.clipboard.writeText" in html, (
            "Copy button must use navigator.clipboard.writeText"
        )

    def test_completion_report_uses_markdown_renderer(self):
        """Completion report content must be rendered as full markdown (headers,
        bold, lists, links, tables) using the shared `marked` library and the
        repo's `.msg-md` style — same pattern as PRD / chat panes."""
        html = load_html()
        block = self._completion_block(html)
        # `marked.parse(` must be referenced for the completion report content.
        # We grep on the broader file because the marked call may live in a
        # helper just outside the JSX block we sliced.
        assert "marked.parse" in html, (
            "Markdown renderer not wired — expected `marked.parse(...)` call "
            "(see existing usage at lines ~4775 / ~5183 / ~5199)"
        )
        # The completion-report card must adopt the shared markdown styles.
        assert "msg-md" in block, (
            "Completion-report block must apply className=\"msg-md\" so headers, "
            "lists, bold, links etc. inherit the repo's markdown styling"
        )

    def test_completion_report_code_renderer_keeps_copy_button(self):
        """Switching to full markdown must NOT regress the per-codeblock Copy
        button. The marked renderer override must still emit a Copy button per
        fenced code block."""
        html = load_html()
        # Renderer override for code blocks must exist. We allow either the
        # marked.Renderer pattern or marked.use({ renderer: { code: ... } }).
        assert (
            "Renderer()" in html
            or "renderer:" in html
            or "marked.use" in html
        ), (
            "Expected a marked renderer override for code blocks so the Copy "
            "button can be injected — none found"
        )
