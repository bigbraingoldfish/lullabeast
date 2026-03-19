"""Tests for PRD upload UI in IdeasScreen."""
import pytest
import re


def extract_function_body(html_content, func_name):
    """Extract the body of a named JS function from HTML.

    Handles both `function name() {}` and `function name(args) {}` forms.
    """
    match = re.search(
        rf'\n([ \t]*)function {re.escape(func_name)}\s*\([^)]*\)\s*\{{',
        html_content,
    )
    if not match:
        return None
    indent = match.group(1)
    body_start = match.end()
    remainder = html_content[body_start:]
    next_fn = re.search(rf'\n\n{re.escape(indent)}function \w', remainder)
    if next_fn:
        candidate = remainder[:next_fn.start()]
    else:
        script_end = re.search(r'\n\s*</script>', remainder)
        candidate = remainder[:script_end.start()] if script_end else remainder
    last_close = candidate.rfind(f'\n{indent}}}')
    if last_close != -1:
        return candidate[:last_close]
    return candidate


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


class TestUploadStateVariables:
    """pass_criteria: Frontend IdeasScreen file input accepts only .md files
    client-side; submitting a non-.md file does not call the upload endpoint."""

    def test_has_upload_error_state(self):
        """IdeasScreen declares uploadError state variable."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None, "IdeasScreen function body not extracted"
        assert "uploadError" in func_body, \
            "IdeasScreen should track uploadError state"

    def test_has_upload_spinner_state(self):
        """IdeasScreen declares uploadSpinner state variable."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert "uploadSpinner" in func_body, \
            "IdeasScreen should track uploadSpinner state"

    def test_has_clarity_pass_state(self):
        """IdeasScreen declares clarityPass state variable (null | true | false)."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert "clarityPass" in func_body, \
            "IdeasScreen should track clarityPass state"

    def test_has_clarity_issues_state(self):
        """IdeasScreen declares clarityIssues state variable."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert "clarityIssues" in func_body, \
            "IdeasScreen should track clarityIssues state"

    def test_has_clarity_missing_state(self):
        """IdeasScreen declares clarityMissing state variable."""
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert "clarityMissing" in func_body, \
            "IdeasScreen should track clarityMissing state"


class TestUploadClientSideValidation:
    """pass_criteria: Frontend IdeasScreen file input accepts only .md files
    client-side; submitting a non-.md file does not call the upload endpoint."""

    def test_file_input_has_accept_md(self):
        """File input has accept='.md' attribute."""
        content = load_index_html()
        assert re.search(r'accept\s*=\s*["\']\.md["\']', content), \
            "File input should have accept='.md' attribute"

    def test_handle_file_change_rejects_non_md(self):
        """handleFileChange validates .md extension and sets uploadError on non-.md."""
        content = load_index_html()
        func_body = extract_function_body(content, "handleFileChange")
        assert func_body is not None, "handleFileChange function not found"

        # Should check for .md extension
        assert re.search(r'\.md', func_body), \
            "handleFileChange should check for .md extension"

        # Should set uploadError for non-.md
        assert re.search(r'set\w*Error.*?\.md', func_body, re.DOTALL), \
            "handleFileChange should set uploadError for non-.md files"

    def test_non_md_early_return_before_fetch(self):
        """handleFileChange returns early for non-.md before any fetch call."""
        content = load_index_html()
        func_body = extract_function_body(content, "handleFileChange")
        assert func_body is not None

        # Find index of first fetch call
        fetch_match = re.search(r'fetch\s*\(', func_body)
        md_check_match = re.search(r'\.md', func_body)

        if fetch_match and md_check_match:
            # The .md check should come before the fetch call
            assert md_check_match.start() < fetch_match.start(), \
                "Extension check should come before fetch call"


class TestUploadApiCall:
    """IdeasScreen calls POST /api/ideas/{id}/upload on .md file submit."""

    def test_handle_file_change_posts_to_upload_endpoint(self):
        """handleFileChange fetches POST /api/ideas/{id}/upload."""
        content = load_index_html()
        func_body = extract_function_body(content, "handleFileChange")
        assert func_body is not None, "handleFileChange function not found"

        assert re.search(r'fetch\s*\(\s*`[^`]*\/upload`', func_body) or \
               re.search(r'fetch\s*\(\s*["\'][^"\']*\/upload', func_body), \
            "handleFileChange should POST to /api/ideas/{id}/upload"

    def test_uses_formdata_for_multipart_upload(self):
        """handleFileChange uses FormData for the upload."""
        content = load_index_html()
        func_body = extract_function_body(content, "handleFileChange")
        assert func_body is not None
        assert "FormData" in func_body, \
            "handleFileChange should use FormData for multipart upload"


class TestClarityCheckIntegration:
    """On 200 response (format_ok), IdeasScreen auto-invokes
    POST /api/ideas/{id}/clarity-check and polls for result."""

    def test_trigger_clarity_check_function_exists(self):
        """IdeasScreen has triggerClarityCheck function."""
        content = load_index_html()
        func_body = extract_function_body(content, "triggerClarityCheck")
        assert func_body is not None, "triggerClarityCheck function not found"

    def test_trigger_clarity_check_posts_to_clarity_check_endpoint(self):
        """triggerClarityCheck fetches POST /api/ideas/{id}/clarity-check."""
        content = load_index_html()
        func_body = extract_function_body(content, "triggerClarityCheck")
        assert func_body is not None

        assert re.search(r'fetch\s*\(\s*`[^`]*\/clarity-check`', func_body) or \
               re.search(r'fetch\s*\(\s*["\'][^"\']*\/clarity-check', func_body), \
            "triggerClarityCheck should POST to /api/ideas/{id}/clarity-check"

    def test_upload_success_triggers_clarity_check(self):
        """handleFileChange calls triggerClarityCheck on successful upload (200)."""
        content = load_index_html()
        func_body = extract_function_body(content, "handleFileChange")
        assert func_body is not None

        assert "triggerClarityCheck" in func_body, \
            "handleFileChange should call triggerClarityCheck on 200 response"


class TestReadyToConvertBadge:
    """pass_criteria: Frontend IdeasScreen shows 'ready to convert' badge
    after a successful clarity-check pass response."""

    def test_shows_badge_when_clarity_pass_true(self):
        """PRD pane header shows 'ready to convert' badge when clarityPass === true."""
        content = load_index_html()
        assert "ready to convert" in content, \
            "IdeasScreen should display 'ready to convert' badge text"

    def test_badge_conditional_on_clarity_pass_true(self):
        """'ready to convert' badge renders only when clarityPass === true."""
        content = load_index_html()
        assert re.search(r"clarityPass\s*===\s*true", content) or \
               re.search(r"clarityPass\s*===\s*!0", content), \
            "Badge should be conditional on clarityPass === true"


class TestClarityFailDisplay:
    """pass_criteria: Frontend IdeasScreen shows missing_sections list
    after a failing pass=false response."""

    def test_shows_needs_revision_badge_when_pass_false(self):
        """PRD pane shows 'needs revision' badge when clarityPass === false."""
        content = load_index_html()
        assert "needs revision" in content, \
            "IdeasScreen should display 'needs revision' badge text"

    def test_displays_missing_sections_list(self):
        """PRD pane shows missing_sections list after pass=false."""
        content = load_index_html()
        assert "clarityMissing" in content, \
            "IdeasScreen should display clarityMissing sections list"

    def test_displays_clarity_issues_list(self):
        """PRD pane shows issues list after pass=false."""
        content = load_index_html()
        assert "clarityIssues" in content, \
            "IdeasScreen should display clarityIssues list"
