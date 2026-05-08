"""Ideas screen: last-selected idea persisted in sessionStorage and restored on reload."""

import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


def extract_function_body(html_content, func_name):
    """Same structural extraction as tests/test_ui_ideas_screen_wired.py."""
    match = re.search(
        rf'\n([ \t]*)function {re.escape(func_name)}\s*\(\s*\)\s*\{{',
        html_content,
    )
    if not match:
        return None

    indent = match.group(1)
    body_start = match.end()
    remainder = html_content[body_start:]

    next_fn = re.search(rf'\n\n{re.escape(indent)}function \w', remainder)
    if next_fn:
        candidate = remainder[: next_fn.start()]
    else:
        script_end = re.search(r'\n\s*</script>', remainder)
        candidate = remainder[: script_end.start()] if script_end else remainder

    last_close = candidate.rfind(f'\n{indent}}}')
    if last_close != -1:
        return candidate[:last_close]
    return candidate


def test_selectIdeaFromRail_writes_session_storage():
    """Selecting an idea persists LAST_IDEA_KEY for restore after full reload."""
    body = extract_function_body(load_index_html(), "IdeasScreen")
    assert body is not None
    rail = re.search(
        r"const selectIdeaFromRail\s*=\s*\([^)]*\)\s*=>\s*\{([\s\S]*?)\n\s*\};",
        body,
    )
    assert rail, "selectIdeaFromRail not found"
    fn_body = rail.group(1)
    assert re.search(r"sessionStorage\.setItem\s*\(\s*LAST_IDEA_KEY", fn_body), (
        "selectIdeaFromRail must call sessionStorage.setItem(LAST_IDEA_KEY, ...)"
    )


def test_newIdea_writes_session_storage_on_success():
    """Creating a new idea stores its id so the next reload opens that draft."""
    body = extract_function_body(load_index_html(), "IdeasScreen")
    assert body is not None
    new_idea = re.search(
        r"const newIdea\s*=\s*\(\)\s*=>\s*\{([\s\S]*?)\n\s*\};",
        body,
    )
    assert new_idea, "newIdea not found"
    fn_body = new_idea.group(1)
    assert "sessionStorage.setItem" in fn_body and "LAST_IDEA_KEY" in fn_body, (
        "newIdea success path must persist LAST_IDEA_KEY via sessionStorage.setItem"
    )


def test_executeDelete_clears_session_storage_for_deleted_id():
    """Deleting the active idea removes stored last id when it matches."""
    body = extract_function_body(load_index_html(), "IdeasScreen")
    assert body is not None
    ex = re.search(
        r"const executeDelete\s*=\s*\([^)]*\)\s*=>\s*\{([\s\S]*?)\n\s*\};",
        body,
    )
    assert ex, "executeDelete not found"
    fn_body = ex.group(1)
    assert re.search(r"sessionStorage\.removeItem\s*\(\s*LAST_IDEA_KEY", fn_body), (
        "executeDelete must call sessionStorage.removeItem(LAST_IDEA_KEY) when appropriate"
    )


def test_auto_select_reads_session_storage_before_list_zero():
    """Auto-select effect consults sessionStorage and ideasList membership before [0]."""
    html = load_index_html()
    assert re.search(
        r"sessionStorage\.getItem\s*\(\s*LAST_IDEA_KEY\s*\)",
        html,
    ), "IdeasScreen must read LAST_IDEA_KEY from sessionStorage"
    assert re.search(
        r"ideasList\.some\s*\(\s*\(\s*it\s*\)\s*=>\s*it\.id\s*===\s*stored",
        html,
    ), "Auto-select must validate stored id is still in ideasList"
    assert "ideasList[0].id" in html, "Fallback to ideasList[0].id must remain"
