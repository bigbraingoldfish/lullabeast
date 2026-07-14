"""Static grep-marker tests for the A1/A2 first-run UI surfaces (ui/index.html).

These follow the repo's existing static-marker style (a plain text scan of the
single-file React app): they pin that the setup-mode key screen and the welcome
walkthrough exist, that the queue-add step is gated on setup mode, and that the
localStorage first-visit flag is present.
"""
import re

INDEX_HTML_PATH = "ui/index.html"


def load_html():
    with open(INDEX_HTML_PATH, "r") as f:
        return f.read()


class TestSetupKeyScreen:
    def test_setup_key_screen_component_present(self):
        html = load_html()
        assert "function SetupKeyScreen(" in html
        assert 'data-testid="setup-key-screen"' in html

    def test_posts_provider_key_endpoint(self):
        html = load_html()
        assert "/api/setup/provider-key" in html
        assert "/api/setup/provider-status" in html

    def test_password_input_and_openrouter_link(self):
        html = load_html()
        assert 'data-testid="setup-key-input"' in html
        assert 'type="password"' in html
        assert "https://openrouter.ai/settings/keys" in html

    def test_provider_is_openrouter_only(self):
        html = load_html()
        assert 'const provider = "openrouter"' in html
        assert 'value="anthropic"' not in html
        assert 'data-testid="setup-key-provider"' not in html

    def test_polls_status_and_restart_state(self):
        html = load_html()
        assert 'data-testid="setup-key-restarting"' in html
        # 3-second poll interval on provider-status.
        assert "3000" in html


class TestWelcomeWalkthrough:
    def test_walkthrough_component_present(self):
        html = load_html()
        assert "function WelcomeWalkthrough(" in html
        assert 'data-testid="welcome-walkthrough"' in html

    def test_reads_demo_info_and_shows_three_artifacts(self):
        html = load_html()
        assert "/api/setup/demo-info" in html
        # Tabs are a template-literal test-id; the tab keys are enumerated in `tabs`.
        assert "welcome-tab-${key}" in html
        for key in ("prd", "roadmap", "verification"):
            assert f'"{key}"' in html
        assert 'data-testid="welcome-artifact-content"' in html

    def test_load_demo_stages_seeds_through_setup(self):
        html = load_html()
        # The tour stages the demo docs through the normal Ideas-to-Setup
        # handoff; there is no separate import endpoint.
        assert 'data-testid="welcome-load-demo"' in html
        assert "/api/setup/import-demo" not in html
        walkthrough = html.index("showWelcome && (")
        assert "navigateToPreflightWithSeed" in html[walkthrough:]

    def test_no_one_click_queue_add(self):
        html = load_html()
        assert 'data-testid="welcome-add-to-queue"' not in html

    def test_expectations_line_present(self):
        html = load_html()
        # Time + cost are stated once, calmly, before the load button.
        assert 'data-testid="welcome-expectations"' in html
        assert "30 to 60 minutes" in html

    def test_rebuild_confirm_guard(self):
        html = load_html()
        # A queued or COMPLETED demo entry triggers a confirm step, and the
        # drafted path is the server's conflict-free suggestion.
        assert 'data-testid="welcome-rebuild-confirm"' in html
        assert 'e.state === "COMPLETED"' in html
        assert "suggested_dest" in html

    def test_localstorage_first_visit_flag(self):
        html = load_html()
        assert 'WELCOME_DONE_KEY = "lb_welcome_done"' in html
        assert "WELCOME_DONE_KEY" in html

    def test_auto_show_recedes_after_first_run(self):
        html = load_html()
        # The auto-show effect checks run_started_at and skips once any run exists.
        assert "d.run_started_at" in html

    def test_show_welcome_tour_lives_on_settings(self):
        html = load_html()
        assert 'data-testid="show-welcome-tour"' in html
        settings = html.index("function SettingsScreen(")
        walkthrough = html.index("function WelcomeWalkthrough(")
        assert 'data-testid="show-welcome-tour"' in html[settings:walkthrough]
        # And it is gone from the Setup & Preflight screen.
        preflight = html.index("function PreflightScreen(")
        assert 'data-testid="show-welcome-tour"' not in html[preflight:settings]


class TestBareMetalHidden:
    def test_onboarding_supported_gate_exists(self):
        html = load_html()
        # Surfaces hide when neither provider config nor the demo is available.
        assert "onboardingSupported" in html


class TestReadmeFirstRun:
    def test_readme_no_longer_has_cp_r_step(self):
        with open("README.md", "r") as f:
            readme = f.read()
        assert "cp -r ../examples/first-run-snake" not in readme

    def test_readme_mentions_in_dashboard_key_and_projects_bind(self):
        with open("README.md", "r") as f:
            readme = f.read()
        assert "projects/first-run-snake" in readme
