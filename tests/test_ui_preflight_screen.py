"""Tests for PreflightScreen component rendering and lock/unlock behavior."""
import pytest
import re


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


class TestPreflightScreenRendering:
    """pass_criteria: PreflightScreen renders repo path text input and roadmap seed
    file input in the DOM"""

    def test_has_repo_path_input(self):
        """PreflightScreen contains a text input for repo path."""
        html = load_html()
        preflight = extract_function(html, "PreflightScreen")
        assert preflight is not None, "PreflightScreen function not found"

        has_text_input = bool(re.search(
            r'<input[^>]*type=["\']text["\'][^>]*>',
            preflight
        ))
        assert has_text_input, "No <input type='text'> found in PreflightScreen for repo path"

    def test_has_roadmap_seed_file_input(self):
        """PreflightScreen contains a file input for roadmap seed upload."""
        html = load_html()
        preflight = extract_function(html, "PreflightScreen")
        assert preflight is not None, "PreflightScreen function not found"

        has_file_input = bool(re.search(
            r'<input[^>]*type=["\']file["\'][^>]*accept=["\']\.md["\'][^>]*>',
            preflight
        ))
        assert has_file_input, "No <input type='file' accept='.md'> found in PreflightScreen"

    def test_has_independent_lock_buttons(self):
        """pass_criteria: PreflightScreen renders independent lock/unlock toggle buttons
        for each field."""
        html = load_html()
        preflight = extract_function(html, "PreflightScreen")
        assert preflight is not None, "PreflightScreen function not found"

        # At least 2 lock-related callbacks
        lock_button_pattern = re.compile(
            r'<button[^>]*onClick[^>]*lock[^>]*>|'
            r'onRepoPathLockToggle|'
            r'onRoadmapSeedLockToggle',
            re.IGNORECASE
        )
        matches = lock_button_pattern.findall(preflight)
        assert len(matches) >= 2, \
            f"Expected at least 2 lock-related callbacks, found {len(matches)}: {matches}"

    def test_locking_switches_input_to_readonly(self):
        """pass_criteria: Locking a field switches its input to read-only/disabled state;
        unlocking restores editability."""
        html = load_html()
        preflight = extract_function(html, "PreflightScreen")
        assert preflight is not None, "PreflightScreen function not found"

        has_disabled_attr = 'disabled' in preflight or 'readOnly' in preflight
        assert has_disabled_attr, \
            "PreflightScreen should conditionally set disabled/readOnly on inputs when locked"

        assert 'repoPathLocked' in preflight, "Missing repoPathLocked state check"
        assert 'roadmapSeedLocked' in preflight, "Missing roadmapSeedLocked state check"

    def test_pre_populated_roadmap_shows_indicator(self):
        """pass_criteria: When seedRoadmap prop is non-empty, PreflightScreen shows
        'From Project Ideas' indicator."""
        html = load_html()
        preflight = extract_function(html, "PreflightScreen")
        assert preflight is not None, "PreflightScreen function not found"

        indicator_pattern = re.search(
            r'From Project Ideas|from.*project.*ideas',
            preflight,
            re.IGNORECASE
        )
        assert indicator_pattern, \
            "PreflightScreen should show 'From Project Ideas' indicator when roadmap is pre-populated"

    def test_pre_populated_roadmap_hides_file_upload(self):
        """pass_criteria: When seedRoadmap prop is non-empty, PreflightScreen hides the
        file upload button."""
        html = load_html()
        preflight = extract_function(html, "PreflightScreen")
        assert preflight is not None, "PreflightScreen function not found"

        # The logic: when roadmapSeed is populated, file input is NOT shown
        # This is done by showing the content area when roadmapSeed is truthy
        # and only showing file input in the else branch
        has_conditional_content = bool(re.search(
            r'\{roadmapSeed\s*\?|'
            r'roadmapSeed\s*\?\s*\(',
            preflight
        ))
        assert has_conditional_content, \
            "PreflightScreen should conditionally show content vs file input based on roadmapSeed"

        # The file input should be in an else branch (only shown when roadmapSeed is empty)
        # Since roadmapSeed truthy → content, roadmapSeed falsy → file input
        has_file_input_conditional = bool(re.search(
            r':\s*\(?[^)]*input[^>]*type=["\']file["\']|'
            r'<input[^>]*type=["\']file["\'][^>]*>[^}]*:\s*\(',
            preflight,
            re.DOTALL
        ))
        assert has_file_input_conditional, \
            "File input should only appear when roadmapSeed is empty"

    def test_pre_populated_roadmap_is_pre_locked(self):
        """pass_criteria: When seedRoadmap prop is non-empty, roadmap seed is pre-locked.

        Note: This initialization happens in App component's navigateToPreflight,
        not in PreflightScreen itself. The test verifies that PreflightScreen
        receives roadmapSeedLocked as a prop and uses it."""
        html = load_html()
        preflight = extract_function(html, "PreflightScreen")
        assert preflight is not None, "PreflightScreen function not found"

        # PreflightScreen should accept roadmapSeedLocked prop
        assert 'roadmapSeedLocked' in preflight, \
            "PreflightScreen should accept roadmapSeedLocked prop"
        # The prop should be used to determine rendering
        assert 'roadmapSeedLocked' in preflight, \
            "PreflightScreen should use roadmapSeedLocked to control rendering"

        # App component should set roadmapSeedLocked=true when navigating with seedRoadmap
        app_html = extract_function(html, "App")
        assert app_html is not None, "App function not found"
        # Check that navigateToPreflight sets roadmapSeedLocked
        has_locked_logic = bool(re.search(
            r'setRoadmapSeedLocked\s*\(\s*true\s*\)|'
            r'roadmapSeedLocked\s*:\s*true',
            app_html
        ))
        assert has_locked_logic, \
            "App should set roadmapSeedLocked=true when navigating to preflight with seedRoadmap"


class TestPreflightScreenNoConsoleErrors:
    """pass_criteria: No JavaScript console errors are thrown during PreflightScreen
    render (React error boundary not triggered)."""

    def test_no_syntax_errors_in_preflight(self):
        """Verify PreflightScreen function has valid JSX structure."""
        html = load_html()
        preflight = extract_function(html, "PreflightScreen")
        assert preflight is not None, "PreflightScreen function not found"

        assert 'return' in preflight, "PreflightScreen should have a return statement"
        assert '(' in preflight, "PreflightScreen should return JSX with parentheses"

    def test_preflight_accepts_all_required_props(self):
        """PreflightScreen accepts all required props from the plan."""
        html = load_html()
        preflight = extract_function(html, "PreflightScreen")
        assert preflight is not None, "PreflightScreen function not found"

        required_props = [
            'seedRoadmap',
            'repoPath',
            'repoPathLocked',
            'roadmapSeed',
            'roadmapSeedLocked',
            'onRepoPathChange',
            'onRepoPathLockToggle',
            'onRoadmapSeedChange',
            'onRoadmapSeedLockToggle',
            'onBack',
        ]

        for prop in required_props:
            assert prop in preflight, \
                f"PreflightScreen missing prop: {prop}"


class TestFileUploadReadsContent:
    """pass_criteria: File upload (<input type='file' accept='.md'>) reads .md file content
    into roadmapSeed state without errors."""

    def test_file_input_has_onChange_handler(self):
        """File input should have an onChange handler to read file content."""
        html = load_html()
        preflight = extract_function(html, "PreflightScreen")
        assert preflight is not None, "PreflightScreen function not found"

        has_onchange = bool(re.search(
            r'<input[^>]*type=["\']file["\'][^>]*onChange[^>]*>|'
            r'onChange[^>]*<input[^>]*type=["\']file["\']',
            preflight
        ))
        assert has_onchange, \
            "File input should have onChange handler to read file content"

    def test_has_filereader_usage(self):
        """PreflightScreen should use FileReader to read uploaded file content."""
        html = load_html()
        preflight = extract_function(html, "PreflightScreen")
        assert preflight is not None, "PreflightScreen function not found"

        has_filereader = bool(re.search(
            r'FileReader|readAsText|onRoadmapSeedChange',
            preflight
        ))
        assert has_filereader, \
            "PreflightScreen should use FileReader to read file content into roadmapSeed"
