import pytest
import re


def extract_function_body(html_content, func_name):
    """Extract the full body of a named function (handles nested JSX braces)."""
    match = re.search(
        rf"\n([ \t]*)function {re.escape(func_name)}\s*\([^)]*\)\s*\{{",
        html_content,
    )
    if not match:
        return None
    indent = match.group(1)
    body_start = match.end()
    remainder = html_content[body_start:]

    next_fn = re.search(rf"\n\n{re.escape(indent)}function \w", remainder)
    if next_fn:
        candidate = remainder[: next_fn.start()]
    else:
        script_end = re.search(r"\n\s*</script>", remainder)
        candidate = remainder[: script_end.start()] if script_end else remainder

    last_close = candidate.rfind(f"\n{indent}}}")
    if last_close != -1:
        return candidate[:last_close]
    return candidate


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


class TestIdeasScreenSplitPanel:
    """Tests for IdeasScreen split-panel scaffold."""

    def test_file_exists(self):
        """ui/index.html exists at project root."""
        import os
        assert os.path.exists("ui/index.html"), "ui/index.html not found at project root"

    def test_ideas_screen_exists(self):
        """IdeasScreen function exists in ui/index.html."""
        content = load_index_html()
        assert "function IdeasScreen" in content, "IdeasScreen function not found"

    def test_two_side_by_side_panels(self):
        """IdeasScreen: merged nav+chats column, conversation + PRD."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None, "IdeasScreen function body not extracted"

        assert "min-h-0 min-w-0" in func_body, "Outer container should use min-h-0 min-w-0"
        assert "PrimaryNavColumn" in func_body, "Nav + chats use PrimaryNavColumn"
        assert "sidebarCollapsed" in func_body, "Chats section follows sidebarCollapsed"
        assert "selectIdeaFromRail" in func_body, "Ideas should switch via rail, not only dropdown"
        assert "border-r border-[#1c1829]" in content, \
            "Column separators should use border-r border-[#1c1829]"
        assert re.search(r"flex-1.*?(?:overflow-hidden|overflow-y-auto)", func_body, re.DOTALL), \
            "Document/conversation columns should use flex-1"

    def test_left_pane_structure(self):
        """Conversation column has flex-1 message list and border-t composer area."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        assert re.search(r'flex\s+flex-col\s+min-w-0\s+bg-\[#15121f\]', func_body), \
            "Conversation column should be flex flex-col min-w-0 bg-[#15121f]"

        assert re.search(r'flex-1\s+overflow-y-auto\s+p-4', func_body), \
            "Messages area should have 'flex-1 overflow-y-auto p-4'"

        # M2 UI-5 composer: border-t + p-3; default border color is border-[#1c1829] (cyan when drag-over)
        assert re.search(
            r"flex-shrink-0\s+border-t\s+p-3[\s\S]{0,400}border-\[#1c1829\]",
            func_body,
        ), "Composer area should use border-t, p-3, and border-[#1c1829] default frame"

    def test_right_pane_has_all_prd_headers(self):
        """Right pane has all 12 PRD section headers as h2 elements."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        prd_headers = [
            "Problem Statement",
            "Goals & Success Metrics",
            "User Stories",
            "Functional Requirements",
            "Edge Cases",
            "Non-Functional Requirements",
            "Dependencies & Integrations",
            "Milestones & Timeline",
            "Risks & Mitigations",
            "Open Questions",
            "Glossary & Domain Terms",
            "Revision History",
        ]

        for header in prd_headers:
            # Titles live in PRD_SECTION_TITLES (module scope, above IdeasScreen)
            assert header in content, f"Missing PRD header string: {header}"
        assert "PRD_SECTION_TITLES.map" in func_body

    def test_right_pane_has_toggle_for_empty_placeholders(self):
        """Right pane hides empty PRD sections unless placeholders are enabled (flag in IdeasScreen)."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        assert "showEmptyPlaceholders" in func_body
        assert "sectionBodyEmpty" in func_body and "return null" in func_body
        assert "hasAnyPrdBody" in func_body

    def test_both_panes_have_flex_flex_col_bg(self):
        """Conversation and PRD columns use flex-col panel styling."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        assert func_body.count("flex flex-col min-w-0 bg-[#15121f]") >= 2, \
            "Conversation and PRD columns should use matching flex-col panels"

    def test_no_javascript_syntax_errors(self):
        """IdeasScreen function has balanced braces and parentheses."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None, "IdeasScreen function body not extracted"

        # Check balanced braces (excluding those in JSX attributes)
        # Simple heuristic: count unescaped braces in the extracted function
        # We just check the outer function braces are balanced
        open_braces = func_body.count('{')
        close_braces = func_body.count('}')
        assert open_braces == close_braces, \
            f"Unbalanced braces: {open_braces} open, {close_braces} close"

        # Check parentheses
        open_parens = func_body.count('(')
        close_parens = func_body.count(')')
        assert open_parens == close_parens, \
            f"Unbalanced parentheses: {open_parens} open, {close_parens} close"

    def test_function_returns_jsx_with_two_panes(self):
        """IdeasScreen returns JSX with outer flex container and two panes."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Should return a div with flex h-full
        assert re.search(r'return\s*\(\s*<div\s+className="[^"]*flex[^"]*h-full[^"]*"', func_body, re.DOTALL), \
            "IdeasScreen should return a div with className containing 'flex h-full'"
