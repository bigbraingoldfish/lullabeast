"""Tests for IdeasScreen wiring: React state + API calls."""
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


class TestIdeasScreenWired:
    """Tests for IdeasScreen having React state and API wiring."""

    def test_has_messages_state(self):
        """IdeasScreen declares messages state variable."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None, "IdeasScreen function body not extracted"

        # Should have useState for messages
        assert re.search(r'useState\s*\(\s*\[\s*\]\s*\)', func_body), \
            "IdeasScreen should initialize messages with useState([])"

    def test_has_prd_content_state(self):
        """IdeasScreen declares prdContent state variable."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Should have useState for prdContent (initial empty string)
        assert re.search(r'useState\s*\(\s*["\']\s*["\']?\s*\)', func_body) or \
               re.search(r'useState\s*\(\s*["\']["\']\s*\)', func_body), \
            "IdeasScreen should initialize prdContent with useState('')"

    def test_has_is_loading_state(self):
        """IdeasScreen declares isLoading state variable."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Should have useState for isLoading (initial false)
        assert re.search(r'useState\s*\(\s*false\s*\)', func_body), \
            "IdeasScreen should initialize isLoading with useState(false)"

    def test_current_idea_id_starts_null_and_auto_selects(self):
        """IdeasScreen initializes currentIdeaId to null (no phantom draft)."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        assert re.search(r"useState\s*\(\s*null\s*\)", func_body), \
            "IdeasScreen should initialize currentIdeaId with useState(null)"
        assert re.search(r"ideasList\[0\]\.id", func_body), \
            "IdeasScreen should auto-select first idea from list when currentIdeaId is null"
        assert re.search(r"sessionStorage\.getItem\s*\(\s*LAST_IDEA_KEY\s*\)", func_body), \
            "IdeasScreen should restore last-selected idea from sessionStorage when valid"

    def test_has_use_effect_for_session_restore(self):
        """IdeasScreen has useEffect that calls GET /api/ideas/{id}/session on mount."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Should have useEffect with empty deps array (mount-only)
        assert re.search(r'useEffect\s*\(\s*\(\s*\)\s*=>\s*\{', func_body), \
            "IdeasScreen should have useEffect for API call"

        # Should call fetch with session endpoint (literal string or template literal)
        assert re.search(r'fetch\s*\(\s*["\'\`]\/api\/ideas\/', func_body) or \
               re.search(r'fetch\s*\(\s*`[^`]*\/api\/ideas\/', func_body), \
            "IdeasScreen should call fetch for /api/ideas/{id}/session"

    def test_has_input_with_enter_key_handler(self):
        """IdeasScreen input has onKeyDown handler that triggers POST on Enter."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Should have onKeyDown on the input
        assert re.search(r'onKeyDown', func_body), \
            "IdeasScreen input should have onKeyDown handler"

        # Should POST to /api/ideas/{id}/message (literal or template literal)
        assert re.search(r'fetch\s*\(\s*["\'\`][^"\'`]*\/api\/ideas\/[^"\'`]*\/message', func_body) or \
               re.search(r'fetch\s*\(\s*`[^`]*message', func_body), \
            "IdeasScreen should POST to /api/ideas/{id}/message"

    def test_sets_is_loading_true_on_submit(self):
        """Submitting a message sets isLoading=true."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Should have setIsLoading(true) or setLoading(true)
        assert re.search(r'set\w*Loading\s*\(\s*true\s*\)', func_body), \
            "IdeasScreen should set isLoading to true when submitting message"

    def test_appends_assistant_response_on_success(self):
        """On success, assistant response is appended to messages array."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Should set messages with previous messages spread + new assistant message
        assert re.search(r'set\w*Messages.*?\[\s*\.\.\..*?messages', func_body, re.DOTALL), \
            "IdeasScreen should spread previous messages when appending new response"

    def test_resets_is_loading_false_on_response(self):
        """After receiving response, isLoading is set to false."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Should have setIsLoading(false)
        assert re.search(r'set\w*Loading\s*\(\s*false\s*\)', func_body), \
            "IdeasScreen should set isLoading to false after receiving response"
