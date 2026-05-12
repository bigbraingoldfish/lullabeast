"""Tests for W5-F: Pipeline-complete metrics block + Generate Docs button.

Static HTML analysis — verifies strings and structures in index.html.
"""

INDEX_HTML_PATH = "ui/index.html"


def load_html():
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


class TestPipelineCompleteMetricsBlock:

    def test_generate_docs_button_present(self):
        """'Generate Docs' (or 'Generate') button text must appear in index.html (W5-F)."""
        html = load_html()
        assert "Generate Docs" in html or "Generate docs" in html, (
            "'Generate Docs' button text not found in index.html — W5-F not implemented"
        )

    def test_generate_docs_calls_completion_review_endpoint(self):
        """Generate Docs button must wire to /api/completion-review endpoint."""
        html = load_html()
        assert "/api/completion-review" in html, (
            "/api/completion-review endpoint reference not found in index.html — "
            "W5-F Generate Docs button not wired"
        )

    def test_pipeline_complete_metrics_block_conditional(self):
        """Metrics block must be gated on PIPELINE_COMPLETE status."""
        html = load_html()
        # Find Generate Docs vicinity and check for PIPELINE_COMPLETE guard
        idx = html.find("Generate Docs")
        if idx == -1:
            idx = html.find("Generate docs")
        assert idx != -1, "Generate Docs button not found"
        context = html[max(0, idx - 1200): idx + 400]
        assert "PIPELINE_COMPLETE" in context, (
            "Generate Docs button / metrics block must be gated on PIPELINE_COMPLETE"
        )

    def test_generate_button_gated_on_missing_report(self):
        """Generate Docs button must only show when completion report does not exist."""
        html = load_html()
        idx = html.find("Generate Docs")
        if idx == -1:
            idx = html.find("Generate docs")
        assert idx != -1, "Generate Docs button not found"
        # Near the button, 'found' check must appear (completionReport.found === false)
        context = html[max(0, idx - 600): idx + 200]
        assert "found" in context, (
            "Generate Docs button must be conditional on completionReport.found being false"
        )

    def test_want_a_summary_tagline_or_equivalent(self):
        """Tagline prompting user to generate docs must appear near the Generate button."""
        html = load_html()
        # Accept either the exact copy or shorter variants
        has_tagline = (
            "Want a summary" in html
            or "summary and next steps" in html
            or "generate documentation" in html.lower()
            or "generate completion" in html.lower()
        )
        assert has_tagline, (
            "Tagline text ('Want a summary', 'summary and next steps', etc.) "
            "not found near Generate Docs button — W5-F tagline not implemented"
        )

    def test_generate_docs_handler_calls_post_method(self):
        """handleGenerateDocs must use POST method when calling the completion-review endpoint."""
        html = load_html()
        idx = html.find("/api/completion-review/")
        assert idx != -1, "/api/completion-review/ not found in index.html"
        context = html[max(0, idx - 200): idx + 200]
        assert "POST" in context or "post" in context, (
            "completion-review fetch call must use method: 'POST'"
        )

    def test_completion_report_polled_after_trigger(self):
        """After successful trigger, UI must poll for the completion report."""
        html = load_html()
        idx = html.find("handleGenerateDocs")
        assert idx != -1
        func_end = html.find("\n            }", idx + 1)
        if func_end == -1:
            func_end = idx + 800
        body = html[idx:func_end]
        assert "fetchCompletionReport" in body, (
            "handleGenerateDocs must call fetchCompletionReport after successful POST "
            "so the UI picks up the generated report"
        )
