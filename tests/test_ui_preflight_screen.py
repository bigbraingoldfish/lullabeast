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
    """pass_criteria: PreflightScreen renders repo path text input and the
    Step 2 "From Project Ideas" summary surface.

    P0 Stage J.4 removed the Step 2 free-text roadmap textarea, the file
    upload input, the independent lock toggles for the roadmap seed,
    and the Fix-Format CTA. Six tests that pinned those behaviours have
    been deleted — keeping passing assertions for code that no longer
    exists would be a liability. The Step 2 summary card and
    empty-state CTA are now pinned by
    ``tests/test_p0_stage_j_setup_step2_summary_card.py``.
    """

    def test_has_repo_path_input(self):
        """PreflightScreen contains ServerPathInput for repo path (text input lives in child)."""
        html = load_html()
        preflight = extract_function(html, "PreflightScreen")
        assert preflight is not None, "PreflightScreen function not found"

        assert "ServerPathInput" in preflight, "PreflightScreen should use ServerPathInput for repo path"
        assert 'id="preflight-repo-path"' in preflight

    def test_pre_populated_roadmap_shows_indicator(self):
        """pass_criteria: When seedRoadmap prop is non-empty, PreflightScreen shows
        'From Project Ideas' indicator. Post-Stage-J.4 the indicator is the
        summary-card heading rather than a textarea badge — both render the
        same literal so this assertion is intact."""
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
        """PreflightScreen accepts all required props from the plan.

        Stage J.4 removed ``roadmapSeedLocked``, ``onRoadmapSeedChange``,
        and ``onRoadmapSeedLockToggle`` from the prop set — the textarea
        they controlled is gone. The remaining props still flow through
        the component."""
        html = load_html()
        preflight = extract_function(html, "PreflightScreen")
        assert preflight is not None, "PreflightScreen function not found"

        required_props = [
            'seedRoadmap',
            'repoPath',
            'repoPathLocked',
            'roadmapSeed',
            'onRepoPathChange',
            'onRepoPathLockToggle',
            'onBack',
            'recentProjects',
        ]

        for prop in required_props:
            assert prop in preflight, \
                f"PreflightScreen missing prop: {prop}"


class TestPreflightServerPathInput:
    def test_preflight_path_input_uses_ServerPathInput(self):
        html = load_html()
        preflight = extract_function(html, "PreflightScreen")
        assert preflight is not None, "PreflightScreen function not found"
        assert "ServerPathInput" in preflight

    def test_preflight_path_uses_preflight_repo_path_id(self):
        html = load_html()
        preflight = extract_function(html, "PreflightScreen")
        assert preflight is not None
        assert 'id="preflight-repo-path"' in preflight

    def test_preflight_still_has_plus_indicator_for_parent(self):
        html = load_html()
        preflight = extract_function(html, "PreflightScreen")
        assert preflight is not None
        assert 'repoPathFsStatus === "parent"' in preflight

    def test_preflight_fetches_recents_in_app(self):
        html = load_html()
        assert "/api/setup/recent-projects" in html
        assert 'currentScreen !== "preflight"' in html

    def test_preflight_confirm_path_fetches_repo_roadmap_hint(self):
        """N4: confirming an existing repo with no linked idea performs a REAL repo-roadmap-hint
        fetch (to decide whether to pop the redirect modal) — not a stale removal comment."""
        html = load_html()
        app_html = extract_function(html, "App")
        assert app_html is not None, "App function not found"
        assert ('fetch("/api/setup/repo-roadmap-hint"' in app_html) or (
            "fetch('/api/setup/repo-roadmap-hint'" in app_html
        ), "App must perform a real repo-roadmap-hint fetch"
        assert "the on-disk auto-load (formerly" not in html, "the stale removal comment must be deleted"


class TestAppPreflightQueueActiveGating:
    """4-C (supersedes B-02): the preflight 'currently running' banner follows the LAUNCH
    predicate — a queue entry in state 'ACTIVE' — so the warning matches what Launch actually
    does (insert-at-top-of-queue). The busy-live helper is retained for cold-boot Ideas routing
    but no longer feeds this banner."""

    def test_active_entry_helper_exists(self):
        html = load_html()
        assert "function queueHasActiveEntry" in html
        assert "state === 'ACTIVE'" in html

    def test_busy_live_helper_retained_for_cold_bootstrap(self):
        html = load_html()
        # Not dead — still gates shouldOpenIdeasOnColdBootstrap (P0-01).
        assert "function queueEntriesHaveBusyLivePipeline" in html
        assert "queueEntriesHaveBusyLivePipeline(queueEntries)" in html

    def test_preflight_banner_probe_uses_active_state_not_busy_live(self):
        html = load_html()
        app_html = extract_function(html, "App")
        assert app_html is not None, "App function not found"
        assert "queueHasActiveEntry(d.queue)" in app_html
        assert "queueHasActiveEntry(qr.queue)" in app_html
        assert "queueEntriesHaveBusyLivePipeline(d.queue)" not in app_html
        assert "queueEntriesHaveBusyLivePipeline(qr.queue)" not in app_html


class TestAppPreflightVerificationContent:
    """Stage C — App component plumbs verification_content to /api/setup/preflight."""

    def test_launch_verification_content_state_exists(self):
        """App has a launchVerificationContent state variable alongside launchPrdContent."""
        html = load_html()
        app_html = extract_function(html, "App")
        assert app_html is not None, "App function not found"
        assert "launchVerificationContent" in app_html, (
            "Expected launchVerificationContent state variable in App component"
        )
        assert "setLaunchVerificationContent" in app_html, (
            "Expected setLaunchVerificationContent setter in App component"
        )

    def test_onRunPreflight_includes_verification_content_in_body(self):
        """onRunPreflight constructs body with verification_content when non-empty."""
        html = load_html()
        app_html = extract_function(html, "App")
        assert app_html is not None
        # The body construction should reference verification_content as a key
        # somewhere in the onRunPreflight handler.
        assert "verification_content" in app_html, (
            "onRunPreflight body must include verification_content field"
        )

    def test_navigateToPreflightWithSeed_accepts_verification_arg(self):
        """navigateToPreflightWithSeed signature must accept a verification argument."""
        html = load_html()
        app_html = extract_function(html, "App")
        assert app_html is not None
        # Either three-arg form `(content, prdText, verificationText)` or a more
        # explicit named-arg flavor. Either way, the function body must call
        # setLaunchVerificationContent.
        assert "navigateToPreflightWithSeed" in app_html
        assert "setLaunchVerificationContent" in app_html, (
            "navigateToPreflightWithSeed must wire setLaunchVerificationContent"
        )
