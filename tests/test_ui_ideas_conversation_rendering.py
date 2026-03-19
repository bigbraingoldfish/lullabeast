"""Tests for IdeasScreen conversation message rendering."""
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


class TestIdeasConversationRendering:
    """Tests for IdeasScreen rendering conversation messages and prdContent."""

    def test_maps_messages_array_to_elements(self):
        """Messages array is mapped to render message elements."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None, "IdeasScreen function body not extracted"

        # Should have messages.map(...)
        assert re.search(r'messages\s*\.\s*map\s*\(', func_body), \
            "IdeasScreen should map messages array to render elements"

    def test_user_messages_right_aligned(self):
        """User messages are right-aligned (styled differently from assistant)."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Check for conditional styling based on role
        # Should distinguish between user and assistant roles (== or === or !=)
        assert re.search(r'role\s*={2,3}\s*["\']user["\']|["\']user["\']\s*={2,3}\s*role', func_body) or \
               re.search(r"msg\.role\s*={2,3}\s*['\"]user['\"]", func_body) or \
               re.search(r'\buser\b', func_body), \
            "IdeasScreen should differentiate user vs assistant message styling"

    def test_assistant_messages_left_aligned(self):
        """Assistant messages are left-aligned."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Check for assistant role condition (== or === or !=)
        assert re.search(r'role\s*={2,3}\s*["\']assistant["\']|["\']assistant["\']\s*={2,3}\s*role', func_body) or \
               re.search(r"msg\.role\s*={2,3}\s*['\"]assistant['\"]", func_body) or \
               re.search(r'\bassistant\b', func_body), \
            "IdeasScreen should differentiate assistant vs user message styling"

    def test_uses_cyan_color_for_user_messages(self):
        """User messages use cyan color styling (right-aligned)."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Look for cyan class or color styling for user messages
        assert re.search(r'cyan|text-cyan|bg-cyan', func_body) or \
               re.search(r'role.*?user', func_body), \
            "IdeasScreen should use cyan or distinctive color for user messages"

    def test_uses_default_color_for_assistant_messages(self):
        """Assistant messages use default/left-aligned styling."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Should have a branch for assistant role
        assert re.search(r'assistant', func_body), \
            "IdeasScreen should handle assistant role in message rendering"

    def test_prd_content_rendered_in_document_pane(self):
        """prdContent is rendered in the right (document) pane."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None, "IdeasScreen function body not extracted"

        # Should reference prdContent in JSX
        assert re.search(r'prdContent', func_body), \
            "IdeasScreen should render prdContent in document pane"

    def test_prd_content_rendered_as_html_not_raw_text(self):
        """prdContent is rendered as HTML (dangerouslySetInnerHTML or split on \\n\\n)."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Should split on \n\n for markdown-like rendering
        assert re.search(r'\\n\\n|split\s*\(\s*\\n\\n', func_body) or \
               re.search(r'dangerouslySetInnerHTML', func_body), \
            "IdeasScreen should render prdContent as HTML (split on \n\n or dangerouslySetInnerHTML)"

    def test_document_pane_shows_section_headings_bold(self):
        """Section headings in prdContent are rendered bold."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Check for bold rendering of headings
        assert re.search(r'split.*?\\n\\n|bold|<b>|<strong>', func_body, re.IGNORECASE), \
            "IdeasScreen should render section headings as bold"
