"""Tests for IdeasScreen new-session UX and chat attachment (no upload-synthesis modal)."""
import re


def extract_function_body(html_content, func_name):
    """Extract the body of a named JS function from HTML.

    Handles both `function name() {}` and `function name(args) {}` forms.
    """
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


class TestNoSessionStartModal:
    """Dedicated upload modal and synthesis entry point removed."""

    def test_no_start_new_idea_modal_copy(self):
        content = load_index_html()
        assert "Start a new idea" not in content
        assert "Upload existing documentation" not in content

    def test_no_session_start_modal_component(self):
        content = load_index_html()
        assert "SessionStartModal" not in content

    def test_no_modal_state_in_ideas_screen(self):
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None
        assert "showSessionModal" not in func_body
        assert "sessionModalIdeaId" not in func_body
        assert "sessionModalBusy" not in func_body


class TestNewIdeaGoesStraightToConversation:
    """+ New opens chat with first-turn placeholder; no modal."""

    def test_new_idea_sets_user_first_turn_pending(self):
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None
        m = re.search(r"const newIdea\s*=\s*\(\)\s*=>\s*\{", func_body)
        assert m, "const newIdea = () => { not found inside IdeasScreen"
        chunk = func_body[m.start() : m.start() + 1500]
        assert "setUserFirstTurnPending(true)" in chunk
        assert "setShowSessionModal" not in chunk
        assert "setSessionModalIdeaId" not in chunk

    def test_empty_state_tell_us_about_idea(self):
        content = load_index_html()
        assert "Tell us about your idea" in content
        assert "userFirstTurnPending" in content and "messages.length === 0" in content


class TestNoUploadSynthesisClient:
    """No multipart /upload or clarity-check trigger in the frontend."""

    def test_no_handle_file_change(self):
        content = load_index_html()
        assert "handleFileChange" not in content

    def test_no_trigger_clarity_check(self):
        content = load_index_html()
        assert "triggerClarityCheck" not in content

    def test_no_ideas_multipart_upload_fetch(self):
        content = load_index_html()
        assert not re.search(r"/ideas/\$\{[^}]+\}/upload", content), (
            "IdeasScreen should not fetch /api/ideas/.../upload"
        )

    def test_no_clarity_pass_ui_state_in_ideas_screen(self):
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None
        assert "clarityPass" not in func_body
        assert "clarityIssues" not in func_body
        assert "clarityMissing" not in func_body
        assert "uploadError" not in func_body
        assert "uploadSpinner" not in func_body


class TestChatAttachmentPathPreserved:
    """Composer still stages .md and sends attachment with POST /message."""

    def test_attach_file_input_accept_md(self):
        content = load_index_html()
        assert "attachFileRef" in content
        m = re.search(
            r"attachFileRef[\s\S]{0,300}accept=\{?[\"']([^\"']+)[\"']\}?",
            content,
        )
        assert m, "attachFileRef file input with accept attribute not found"
        assert ".md" in m.group(1), "accept must still include .md"

    def test_submit_message_includes_attachment_snap(self):
        content = load_index_html()
        func_body = extract_function_body(content, "IdeasScreen")
        assert func_body is not None
        m = re.search(r"const submitMessage\s*=\s*\(", func_body)
        assert m, "const submitMessage not found inside IdeasScreen"
        chunk = func_body[m.start() : m.start() + 2200]
        assert "stagedAttachment" in chunk
        assert "attachmentSnap" in chunk
        assert "body.attachment" in chunk


class TestNoClarityBadgesInDom:
    """Upload-driven clarity badges removed from PRD pane."""

    def test_no_ready_to_convert_clarity_badge(self):
        content = load_index_html()
        assert "ready to convert" not in content

    def test_no_needs_revision_clarity_badge(self):
        content = load_index_html()
        assert "needs revision" not in content
