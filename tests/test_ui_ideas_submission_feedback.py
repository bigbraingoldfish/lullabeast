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


def test_submit_message_uses_optimistic_append():
    content = load_index_html()
    func_body = extract_function_body(content, "IdeasScreen")
    assert func_body is not None
    assert (
        "setMessages([" in func_body or "setMessages((prev) => [" in func_body
    ), "submitMessage should optimistically append via setMessages"
    assert "pending: true" in func_body


def test_submit_message_clears_input_immediately():
    content = load_index_html()
    func_body = extract_function_body(content, "IdeasScreen")
    assert func_body is not None
    assert "setInputText(\"\")" in func_body


def test_document_pane_has_explicit_working_banner():
    content = load_index_html()
    func_body = extract_function_body(content, "IdeasScreen")
    assert func_body is not None
    # PRD pane only (no duplicate header strip); chat uses pending bubble
    assert "Updating PRD draft" in func_body
