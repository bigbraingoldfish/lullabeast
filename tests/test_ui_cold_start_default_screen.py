"""Static contract tests: P0-01 cold idle defaults main shell to Project Ideas (ui/index.html)."""

import re

INDEX_HTML_PATH = "ui/index.html"


def load_html():
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


def extract_function(html, func_name):
    """Extract the body of a named JS function from HTML."""
    match = re.search(
        rf"\n([ \t]*)function {re.escape(func_name)}\s*\([^)]*\)\s*\{{",
        html,
    )
    if not match:
        return None
    indent = match.group(1)
    body_start = match.end()
    remainder = html[body_start:]

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


class TestColdStartHelper:
    def test_should_open_ideas_helper_exists_and_uses_queue_busy_check(self):
        html = load_html()
        assert "function shouldOpenIdeasOnColdBootstrap" in html, (
            "shouldOpenIdeasOnColdBootstrap must exist for P0-01 gating"
        )
        start = html.index("function shouldOpenIdeasOnColdBootstrap")
        snippet = html[start : start + 500]
        assert "queueEntriesHaveBusyLivePipeline" in snippet, (
            "shouldOpenIdeasOnColdBootstrap should call queueEntriesHaveBusyLivePipeline"
        )
        assert "IDLE" in snippet and "UNKNOWN" in snippet, (
            "shouldOpenIdeasOnColdBootstrap should treat IDLE and UNKNOWN as cold idle"
        )


class TestAppColdBootstrapRouting:
    def test_app_bootstrap_fetches_state_and_queue_together(self):
        html = load_html()
        app_body = extract_function(html, "App")
        assert app_body is not None
        assert "/api/state" in app_body
        assert "/api/queue" in app_body
        assert "Promise.all" in app_body

    def test_app_sets_ideas_from_bootstrap_path(self):
        html = load_html()
        app_body = extract_function(html, "App")
        assert app_body is not None
        assert 'setCurrentScreen("ideas")' in app_body or "setCurrentScreen('ideas')" in app_body

    def test_app_uses_ref_guard_for_current_screen(self):
        html = load_html()
        app_body = extract_function(html, "App")
        assert app_body is not None
        assert "currentScreenRef" in app_body
        assert "useRef" in app_body
        assert "coldBootstrapScreenAppliedRef" in app_body

    def test_app_should_open_ideas_used_in_effect(self):
        html = load_html()
        app_body = extract_function(html, "App")
        assert app_body is not None
        assert "shouldOpenIdeasOnColdBootstrap" in app_body
