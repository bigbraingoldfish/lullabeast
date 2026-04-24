import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


def extract_function_body(html_content, func_name):
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


def test_prd_document_uses_marked_html_rendering():
    content = load_index_html()
    func_body = extract_function_body(content, "IdeasScreen")
    assert func_body is not None
    assert "dangerouslySetInnerHTML" in func_body
    assert "marked.parse(prdContent" in func_body or "marked.parse(sectionMarkdown" in func_body


def test_prd_document_not_rendered_as_plain_prewrap_text():
    content = load_index_html()
    func_body = extract_function_body(content, "IdeasScreen")
    assert func_body is not None
    assert "whitespace-pre-wrap mb-6 border-b" not in func_body


def test_action_row_has_overflow_menu_for_downloads():
    content = load_index_html()
    func_body = extract_function_body(content, "IdeasScreen")
    assert func_body is not None
    assert "showActionMenu" in func_body
    assert "Download PRD" in func_body
    assert "Download Roadmap" in func_body
    assert "Generate Roadmap" in func_body


def test_delete_removed_from_conversation_header():
    content = load_index_html()
    func_body = extract_function_body(content, "IdeasScreen")
    assert func_body is not None
    assert "Delete session" not in func_body


def test_chat_rows_have_kebab_delete_entry():
    content = load_index_html()
    func_body = extract_function_body(content, "IdeasScreen")
    assert func_body is not None
    assert "activeIdeaMenuId" in func_body
    assert "Rename idea" in func_body
    assert "renamingIdeaId" in func_body
    assert "setRenameDraft" in func_body
    assert "Delete idea" in func_body
    assert "Idea actions" in func_body


def test_prd_completeness_checklist_present():
    content = load_index_html()
    func_body = extract_function_body(content, "IdeasScreen")
    assert func_body is not None
    assert "checklistRows" in func_body
    assert "PRD_SECTION_TITLES" in func_body


def test_sidebar_uses_header_chevron_toggle():
    content = load_index_html()
    nav_body = extract_function_body(content, "PrimaryNavColumn")
    ideas_body = extract_function_body(content, "IdeasScreen")
    assert nav_body is not None
    assert ideas_body is not None
    assert "setSidebarCollapsed" in nav_body
    assert "Expand sidebar" in nav_body
    assert "‹" in nav_body
    assert ">AD</span>" in nav_body
    assert "side-divider-btn" not in content


P3_SIDEBAR_IDEAS_TAB_TITLE_EXPECTED = "Draft PRDs with the PRD agent."


def test_sidebar_ideas_nav_uses_p3_title_hint_h28():
    content = load_index_html()
    assert P3_SIDEBAR_IDEAS_TAB_TITLE_EXPECTED in content
    assert "P3_SIDEBAR_IDEAS_TAB_TITLE" in content
    nav_body = extract_function_body(content, "PrimaryNavColumn")
    assert nav_body is not None
    assert "navTitle" in nav_body
    assert "title={item.navTitle ?? item.label}" in nav_body
    assert "key: 'ideas'" in nav_body or "key: \"ideas\"" in nav_body
    assert "P3_SIDEBAR_IDEAS_TAB_TITLE" in nav_body
