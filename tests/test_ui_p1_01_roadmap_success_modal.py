"""Static contract tests for P1-01: post-roadmap success modal + low-readiness generate guard (ui/index.html)."""

import re

INDEX_HTML_PATH = "ui/index.html"


def load_html():
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


def extract_function_body(html_content, func_name):
    """Extract IdeasScreen (or other) function body — same strategy as test_ui_ideas_screen_wired.py."""
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


def test_roadmap_generated_modal_testid():
    assert 'data-testid="ideas-roadmap-generated-modal"' in _ideas_body()


def test_success_modal_action_copy():
    body = _ideas_body()
    # JSX in HTML uses entity for ampersand in text nodes
    assert "Setup &amp; Preflight" in body or "Setup & Preflight" in body
    assert "Continue to Setup" in body
    assert "Alignment" in body
    assert "Adversarial" in body


def test_success_modal_readiness_below_eight_conditional():
    body = _ideas_body()
    assert "readinessData.score" in body
    assert "readinessData.score < 8" in body
    assert "showQualityCheckModal" in body


def test_low_readiness_generate_confirm_present():
    body = _ideas_body()
    assert 'data-testid="ideas-low-readiness-generate-confirm"' in body
    assert "showLowReadinessGenerateConfirm" in body
    assert "beginConvertAfterGuards" in body


def test_low_readiness_guard_predicate():
    body = _ideas_body()
    assert 'readinessStatus === "ready"' in body
    assert "readinessData.score < 8" in body
