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
