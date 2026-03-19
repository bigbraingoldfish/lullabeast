import pytest
import re


def extract_function_body(html_content, func_name):
    """Extract the body of a JavaScript function from HTML."""
    pattern = rf'function {func_name}\s*\(\)\s*\{{(.*?)\n\s*\}}\s*;?\s*$'
    match = re.search(pattern, html_content, re.MULTILINE | re.DOTALL)
    if not match:
        # Try alternate pattern with closing );
        pattern2 = rf'function {func_name}\s*\(\)\s*\{{(.*?)\n\s*\}}\s*,?\s*$'
        match = re.search(pattern2, html_content, re.MULTILINE | re.DOTALL)
    if match:
        return match.group(1)
    return None


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
        """IdeasScreen renders two side-by-side panels with left w-[38%] and right flex-1."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None, "IdeasScreen function body not extracted"

        # Check outer container uses flex h-full
        assert "flex h-full" in func_body, "Outer container should have 'flex h-full'"

        # Check left pane: w-[38%] flex-shrink-0 with right border separator
        assert re.search(r'w-\[38\%\].*?flex-shrink-0', func_body, re.DOTALL), \
            "Left pane should have w-[38%] flex-shrink-0"
        assert "border-r border-[#1a1d21]" in func_body, \
            "Separator should be border-r border-[#1a1d21]"

        # Check right pane: flex-1
        assert re.search(r'flex-1.*?(?:overflow-hidden|overflow-y-auto)', func_body, re.DOTALL), \
            "Right pane should have flex-1"

    def test_left_pane_structure(self):
        """Left pane has flex-1 overflow-y-auto div for messages and a border-t border-[#1a1d21] pinned input area."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Left pane should have flex flex-col bg-[#141618] overflow-hidden
        assert re.search(r'flex\s+flex-col\s+bg-\[#141618\]\s+overflow-hidden', func_body), \
            "Each pane should have 'flex flex-col bg-[#141618] overflow-hidden'"

        # Left inner: flex-1 overflow-y-auto p-4 (messages area)
        assert re.search(r'flex-1\s+overflow-y-auto\s+p-4', func_body), \
            "Messages area should have 'flex-1 overflow-y-auto p-4'"

        # Input area: border-t border-[#1a1d21] p-3
        assert re.search(r'border-t\s+border-\[#1a1d21\]\s+p-3', func_body), \
            "Input area should have 'border-t border-[#1a1d21] p-3'"

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
            # Check for <h2 ...>{header}</h2>
            assert re.search(rf'<h2[^>]*>\s*{re.escape(header)}\s*</h2>', func_body), \
                f"Missing PRD header: {header}"

    def test_right_pane_has_all_placeholder_texts(self):
        """Right pane has all 12 placeholder lines with italic dim text."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        placeholder = "text-slate-600 italic text-sm"
        assert func_body.count(placeholder) == 12, \
            f"Expected 12 placeholder lines with '{placeholder}', found {func_body.count(placeholder)}"

    def test_both_panes_have_flex_flex_col_bg(self):
        """Both panes have flex flex-col bg-[#141618] and scroll independently."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None

        # Both panes share these classes
        assert "flex flex-col bg-[#141618]" in func_body, \
            "Both panes should have 'flex flex-col bg-[#141618]'"

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
