"""Tests for FirstRunScreen component and App first-run gating in ui/index.html."""

import re

import pytest


INDEX_HTML_PATH = "ui/index.html"


def load_html():
    with open(INDEX_HTML_PATH, "r") as f:
        return f.read()


def extract_function(html, func_name):
    """Extract the body of a named JS function from HTML."""
    match = re.search(
        rf'\n([ \t]*)function {re.escape(func_name)}\s*\([^)]*\)\s*\{{',
        html,
    )
    if not match:
        return None
    indent = match.group(1)
    body_start = match.end()
    remainder = html[body_start:]

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


class TestFirstRunScreenExists:
    def test_first_run_screen_function_exists(self):
        html = load_html()
        body = extract_function(html, "FirstRunScreen")
        assert body is not None, "FirstRunScreen function not found in index.html"

    def test_first_run_screen_is_a_react_component(self):
        html = load_html()
        body = extract_function(html, "FirstRunScreen")
        assert body is not None
        # Must return JSX (contains a return with JSX)
        assert "return" in body


class TestFirstRunScreenContent:
    def test_first_run_screen_shows_welcome_text(self):
        html = load_html()
        body = extract_function(html, "FirstRunScreen")
        assert body is not None
        assert re.search(r'[Ww]elcome|AutoDev', body), \
            "FirstRunScreen should contain a welcome message"

    def test_first_run_screen_shows_install_command(self):
        html = load_html()
        body = extract_function(html, "FirstRunScreen")
        assert body is not None
        assert "install.sh" in body, \
            "FirstRunScreen should show the install.sh command"

    def test_first_run_screen_has_check_again_button(self):
        html = load_html()
        body = extract_function(html, "FirstRunScreen")
        assert body is not None
        assert re.search(r'[Cc]heck again|Check Again', body), \
            "FirstRunScreen should have a 'Check again' button"

    def test_first_run_screen_has_continue_button(self):
        html = load_html()
        body = extract_function(html, "FirstRunScreen")
        assert body is not None
        assert re.search(r'[Cc]ontinue', body), \
            "FirstRunScreen should have a 'Continue' button"

    def test_first_run_screen_renders_missing_items(self):
        html = load_html()
        body = extract_function(html, "FirstRunScreen")
        assert body is not None
        # Should use missingItems prop to render a list
        assert re.search(r'missingItems|missing_items', body), \
            "FirstRunScreen should render missing items from the status response"

    def test_first_run_screen_fetches_setup_status(self):
        html = load_html()
        assert "/api/setup/status" in html, \
            "/api/setup/status should be fetched somewhere in index.html"


class TestFirstRunScreenInteractivity:
    def test_first_run_screen_has_on_check_again_prop(self):
        html = load_html()
        body = extract_function(html, "FirstRunScreen")
        assert body is not None
        assert re.search(r'onCheckAgain|checkAgain', body), \
            "FirstRunScreen should use onCheckAgain callback"

    def test_first_run_screen_has_on_continue_prop(self):
        html = load_html()
        body = extract_function(html, "FirstRunScreen")
        assert body is not None
        assert re.search(r'onContinue|continue', body, re.IGNORECASE), \
            "FirstRunScreen should use onContinue callback"

    def test_first_run_screen_continue_disabled_when_items_missing(self):
        html = load_html()
        body = extract_function(html, "FirstRunScreen")
        assert body is not None
        # Continue button should be gated on missing items being empty
        assert re.search(r'missingItems\.length|missing.*length|disabled', body), \
            "FirstRunScreen should disable Continue button when missingItems is non-empty"


class TestAppFirstRunGating:
    def test_app_has_setup_complete_state(self):
        html = load_html()
        app_body = extract_function(html, "App")
        assert app_body is not None
        assert re.search(r'setupComplete|setup_complete', app_body), \
            "App should have setupComplete state"

    def test_app_fetches_api_state_on_mount(self):
        html = load_html()
        app_body = extract_function(html, "App")
        assert app_body is not None
        assert "useEffect" in app_body, "App should use useEffect for on-mount fetch"
        assert "/api/state" in app_body, "App should fetch /api/state on mount"

    def test_app_renders_first_run_screen_conditionally(self):
        html = load_html()
        app_body = extract_function(html, "App")
        assert app_body is not None
        assert "FirstRunScreen" in app_body, \
            "App should conditionally render FirstRunScreen"

    def test_app_has_setup_checking_or_loading_state(self):
        html = load_html()
        app_body = extract_function(html, "App")
        assert app_body is not None
        assert re.search(r'setupChecking|setupLoading|checking|loading', app_body, re.IGNORECASE), \
            "App should track loading state for setup status check"

    def test_app_shows_normal_content_when_setup_complete(self):
        html = load_html()
        app_body = extract_function(html, "App")
        assert app_body is not None
        # When setupComplete is true/not false, show normal pipeline screens
        assert "PipelineScreen" in app_body or "Sidebar" in app_body, \
            "App should still render normal content when setup is complete"

    def test_app_fails_open_on_api_error(self):
        html = load_html()
        app_body = extract_function(html, "App")
        assert app_body is not None
        # catch block should set setupComplete to true (fail open) so user isn't blocked
        assert re.search(r'catch.*true|\.catch', app_body, re.DOTALL), \
            "App should fail open (show normal UI) if /api/state fetch fails"
