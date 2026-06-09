"""Tests for shared ServerPathInput component in ui/index.html."""
import re

INDEX_HTML_PATH = "ui/index.html"


def load_html():
    with open(INDEX_HTML_PATH, "r") as f:
        return f.read()


def extract_function(html, func_name):
    """Extract the body of a named JS function from HTML."""
    match = re.search(
        rf"\n([ \t]*)function {re.escape(func_name)}\s*\([^)]*\)\s*\{{",
        html,
    )
    if not match:
        return None
    indent = match.group(1)
    body_start = match.end()
    remainder = html[body_start:]

    next_fn = re.search(rf"\n\n{re.escape(indent)}function \w", remainder)
    if next_fn:
        candidate = remainder[: next_fn.start()]
    else:
        script_end = re.search(r"\n\s*</script>", remainder)
        candidate = remainder[: script_end.start()] if script_end else remainder

    last_close = candidate.rfind(f"\n{indent}}}")
    if last_close != -1:
        return candidate[: last_close]
    return candidate


class TestServerPathInput:
    def test_ServerPathInput_function_exists(self):
        html = load_html()
        assert re.search(r"function ServerPathInput\s*\(", html), (
            "ServerPathInput function not found"
        )

    def test_renders_clickable_recents_not_datalist(self):
        """Recents are a visible click-to-fill list (4-A), not a hidden <datalist> (which also
        re-introduced the N2 sticky-path autofill). Replaces the former test_has_datalist_element."""
        body = extract_function(load_html(), "ServerPathInput")
        assert body is not None
        assert "<datalist" not in body, "datalist removed in favour of the visible click-to-fill list"
        assert "recentsToDisplayPaths" in body, "recents rendered via the filtered display helper"
        assert "<button" in body, "recents are clickable buttons (click-to-fill)"

    def test_disables_browser_autofill(self):
        """N2 — the field must not browser-autofill a previously typed path on reopen."""
        body = extract_function(load_html(), "ServerPathInput")
        assert body is not None
        assert 'autoComplete="off"' in body

    def test_placeholder_is_example_path(self):
        html = load_html()
        body = extract_function(html, "ServerPathInput")
        assert body is not None
        assert 'placeholder={placeholder}' in body
        assert re.search(
            r'function ServerPathInput\(\{[^}]*placeholder = "/path/to/your-project/my-app"',
            html,
        ), "Default placeholder param on ServerPathInput"

    def test_has_exists_check_indicator(self):
        body = extract_function(load_html(), "ServerPathInput")
        assert body is not None
        assert 'fsStatus === "exists"' in body

    def test_has_bad_check_indicator(self):
        body = extract_function(load_html(), "ServerPathInput")
        assert body is not None
        assert 'fsStatus === "bad"' in body

    def test_no_parent_plus_indicator(self):
        body = extract_function(load_html(), "ServerPathInput")
        assert body is not None
        assert 'fsStatus === "parent"' not in body
        assert "text-amber" not in body, "ServerPathInput must not use amber + (parent-exists) styling"
