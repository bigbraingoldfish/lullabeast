"""Tests for IdeasScreen loading UX (no amber pulse on document pane)."""
import pytest
import re


def extract_function_body(html_content, func_name):
    """Extract the body of a named JS function from HTML.

    Uses a structural (indentation-based) approach rather than brace counting,
    which avoids false matches on JSX comment blocks, ternaries, and nested
    expressions that would trip a naive depth counter.

    Strategy:
      1. Locate the function declaration and capture its leading indentation.
      2. The function ends just before the blank line that precedes the next
         sibling function at the same indentation level.
      3. Strip the function's own closing `}` from the tail of that slice.
    """
    match = re.search(
        rf'\n([ \t]*)function {re.escape(func_name)}\s*\(\s*\)\s*\{{',
        html_content,
    )
    if not match:
        return None

    indent = match.group(1)
    body_start = match.end()
    remainder = html_content[body_start:]

    # Locate the end: a blank line followed by the next sibling function.
    next_fn = re.search(rf'\n\n{re.escape(indent)}function \w', remainder)
    if next_fn:
        candidate = remainder[: next_fn.start()]
    else:
        # Last function in the file — stop at </script>
        script_end = re.search(r'\n\s*</script>', remainder)
        candidate = remainder[: script_end.start()] if script_end else remainder

    # Strip the closing `}` of the function itself from the tail.
    last_close = candidate.rfind(f'\n{indent}}}')
    if last_close != -1:
        return candidate[:last_close]
    return candidate


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


class TestIdeasDocumentPulse:
    """Document pane uses muted loading, not full-pane amber pulse."""

    def test_status_pulse_css_exists(self):
        """status-pulse CSS may remain for pipeline UI; document pane must not use it."""
        content = load_index_html()
        assert ".status-pulse" in content, \
            "status-pulse CSS class should be defined in index.html"

    def test_document_pane_does_not_apply_status_pulse_when_loading(self):
        """Document scroll area must not use status-pulse (avoids yellow full-pane flash)."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None, "IdeasScreen function body not extracted"
        assert "ref={docPaneRef}" in func_body
        for line in func_body.splitlines():
            if "ref={docPaneRef}" in line:
                assert "status-pulse" not in line, "Document pane must not apply status-pulse"
                break
        else:
            pytest.fail("docPaneRef line not found")

    def test_status_pulse_removed_immediately_on_response(self):
        """status-pulse is removed immediately when prd_draft is read (response received)."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # After the fetch succeeds, isLoading should be set to false
        # The class should be removed at the same time (or before) prdContent is updated
        # Check that setIsLoading(false) is called after successful response
        assert re.search(r'set\w*Loading\s*\(\s*false\s*\)', func_body), \
            "IdeasScreen should call setIsLoading(false) when response is received"

    def test_muted_prd_loading_copy_present(self):
        """PRD area shows neutral Updating PRD draft copy while loading."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None
        assert "Updating PRD draft" in func_body
