"""Tests for IdeasScreen document pane status-pulse animation."""
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
    """Tests for IdeasScreen document pane status-pulse animation."""

    def test_status_pulse_css_exists(self):
        """status-pulse CSS class is defined in index.html."""
        content = load_index_html()
        assert ".status-pulse" in content, \
            "status-pulse CSS class should be defined in index.html"

    def test_document_pane_wrapper_gets_status_pulse_class_when_loading(self):
        """Document pane wrapper div gets status-pulse class when isLoading=true."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None, "IdeasScreen function body not extracted"

        # The right pane (document pane) should conditionally get status-pulse
        # Look for ternary or && expression that applies status-pulse based on isLoading
        # Pattern: className={..., isLoading ? 'status-pulse' : ''}
        # or: className={`...${isLoading ? ' status-pulse' : ''}`}
        # or: isLoading && 'status-pulse'
        assert re.search(r'status-pulse', func_body), \
            "IdeasScreen should reference 'status-pulse' class for document pane"

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

    def test_document_pane_wrapper_is_target_of_pulse_animation(self):
        """The document pane (right panel) wrapper div is the element receiving status-pulse."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Find the right pane div — it should have className conditional expression
        # involving status-pulse
        # Pattern: flex-1 flex flex-col bg-[#141618] + conditional class
        right_pane_pattern = r'className=\{[^}]*status-pulse[^}]*\}'
        assert re.search(right_pane_pattern, func_body, re.DOTALL), \
            "Right pane div should have conditional className including status-pulse"
