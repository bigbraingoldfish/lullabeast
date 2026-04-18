"""Static contract tests for L-17–L-19: Ideas empty state + PRD readiness strip labels (ui/index.html)."""

import re

INDEX_HTML_PATH = "ui/index.html"


def load_html():
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


def extract_function_body(html_content, func_name):
    match = re.search(
        rf"\n([ \t]*)function {re.escape(func_name)}\s*\(\s*\)\s*\{{",
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


def _ideas_body():
    html = load_html()
    body = extract_function_body(html, "IdeasScreen")
    assert body is not None, "IdeasScreen function not found"
    return body


def test_l17_empty_state_copy():
    body = _ideas_body()
    assert "No projects yet. Click + New to start a PRD conversation." in body
    assert "No ideas yet. Click + New." not in body


def test_l18_l19_readiness_strip_labels():
    body = _ideas_body()
    assert "PRD readiness:" in body
    assert "Roadmap confidence:" in body
    assert "readinessData.conversion_confidence" in body
    assert "Readiness: {typeof readinessData.score" not in body
    assert "Conversion confidence:" not in body
