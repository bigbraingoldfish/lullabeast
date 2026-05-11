"""TDD: roadmap generate/regenerate locked while Ideas chat reply is in flight (isLoading)."""

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


def _slice_const_arrow_function(func_body: str, name: str, until_marker: str):
    """Extract body of `const name = () => { ...` up to (but not including) until_marker."""
    start = func_body.find(f"const {name} = () => {{")
    if start == -1:
        return None
    end = func_body.find(until_marker, start + len(f"const {name} = () => {{"))
    if end == -1:
        return None
    return func_body[start:end]


ROADMAP_LOCK_USER_MESSAGE = (
    "Finish the assistant's reply before generating a roadmap so it matches the latest PRD."
)


def test_roadmap_generate_buttons_disabled_while_is_loading():
    content = load_index_html()
    func_body = extract_function_body(content, "IdeasScreen")
    assert func_body is not None
    assert func_body.count("disabled={convertLoading || roadmapActionsLockedByReply}") >= 2, (
        "Generate Roadmap (empty tab) and sticky-bar CTA should combine convertLoading with reply lock"
    )


def test_run_convert_bails_out_when_reply_in_flight():
    content = load_index_html()
    func_body = extract_function_body(content, "IdeasScreen")
    assert func_body is not None
    run_slice = _slice_const_arrow_function(func_body, "_runConvert", "const doConvert = () => {")
    assert run_slice is not None, "_runConvert block not found"
    guard_idx = run_slice.find("isLoading")
    ref_guard_idx = run_slice.find("isLoadingRef.current")
    assert guard_idx != -1 or ref_guard_idx != -1, "_runConvert should guard on isLoading or isLoadingRef"
    load_idx = run_slice.find("setConvertLoading(true)")
    assert load_idx != -1, "setConvertLoading(true) expected in _runConvert"
    assert (
        (guard_idx != -1 and guard_idx < load_idx)
        or (ref_guard_idx != -1 and ref_guard_idx < load_idx)
    ), "Reply-in-flight guard must appear before setConvertLoading(true) in _runConvert"


def test_do_convert_bails_out_when_reply_in_flight():
    content = load_index_html()
    func_body = extract_function_body(content, "IdeasScreen")
    assert func_body is not None
    do_slice = _slice_const_arrow_function(
        func_body, "doConvert", "const doAlignmentCheck = () => {"
    )
    assert do_slice is not None, "doConvert block not found"
    assert re.search(
        r"if\s*\(\s*isLoading\s*\)\s*return",
        do_slice,
    ), "doConvert should return early when isLoading before roadmap branching"


def test_user_visible_roadmap_lock_explanation_present():
    content = load_index_html()
    func_body = extract_function_body(content, "IdeasScreen")
    assert func_body is not None
    assert ROADMAP_LOCK_USER_MESSAGE in func_body, (
        "Sticky or tab copy should explain roadmap lock during assistant reply"
    )
