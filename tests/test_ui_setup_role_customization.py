"""Static markers for the setup screen's per-role customization (Stage D).

Both setup paths expose a collapsed "Customize model roles (optional)"
disclosure over the shared SetupRolePicker: the cloud path prefills the
audited defaults from GET /api/models/roles and sends only changed roles
with the key; the local path drafts per-role swaps over the probed server
models and sends only roles that diverge from the primary pick. Endpoint
behavior is covered in test_setup_provider_key.py / test_setup_local_models.py.
"""


def _html():
    with open("ui/index.html", "r", encoding="utf-8") as f:
        return f.read()


class TestSetupRolePickerShared:
    def test_component_and_toggle_present(self):
        html = _html()
        assert "function SetupRolePicker(" in html
        assert "function SetupCustomizeToggle(" in html
        assert "Customize model roles (optional)" in html

    def test_picker_reuses_settings_role_metadata(self):
        # Workflow order and plain-language copy come from the Stage C card's
        # single source of truth, not a second role list.
        html = _html()
        assert "MODEL_ROLE_GROUPS.map((group)" in html
        assert "MODEL_ROLE_INFO[role]" in html

    def test_per_role_selects_and_vision_warning_templates(self):
        html = _html()
        assert "data-testid={`${idPrefix}-role-select-${role}`}" in html
        assert "data-testid={`${idPrefix}-role-vision-warning`}" in html


class TestCloudPathCustomization:
    def test_disclosure_present_and_lazy(self):
        html = _html()
        assert 'data-testid="setup-key-customize"' in html
        # The toggle testid rides through SetupCustomizeToggle's prop.
        assert 'testid="setup-key-customize-toggle"' in html
        assert "data-testid={testid}" in html
        # Catalog fetch happens on first expand, not on mount.
        assert "const toggleCloudCustomize = () => {" in html

    def test_only_changed_roles_ride_the_submit(self):
        html = _html()
        assert "if (Object.keys(cloudChangedRoles).length > 0) payload.roles = cloudChangedRoles;" in html

    def test_submit_blocked_while_a_vision_role_is_text_only(self):
        html = _html()
        assert "cloudVisionBlocked" in html
        assert "disabled={submitting || !keyValue.trim() || cloudVisionBlocked}" in html

    def test_restarting_screen_points_at_settings(self):
        html = _html()
        assert "You can change each role's model later in Settings." in html


class TestLocalPathCustomization:
    def test_disclosure_gated_to_multi_model_servers(self):
        html = _html()
        assert 'data-testid="setup-local-customize"' in html
        assert 'testid="setup-local-customize-toggle"' in html
        assert "server.models.length > 1 && (" in html

    def test_unset_roles_follow_the_primary_pick(self):
        html = _html()
        assert "const effModel = (role) => roleDraft[role] || model;" in html

    def test_only_diverging_roles_ride_the_submit(self):
        html = _html()
        assert "if (Object.keys(customized).length > 0) payload.roles = customized;" in html

    def test_button_label_reflects_customization(self):
        html = _html()
        assert "Use this model for all roles" in html
        assert "Use these role assignments" in html


class TestCopyStandards:
    def test_new_copy_has_no_em_dashes(self):
        html = _html()
        for snippet in (
            "Customize model roles (optional)",
            "Prefilled with the audited defaults. Changes are saved with the",
            "You can change each role's model later in Settings.",
            "Every role uses the model picked above unless you swap",
            "The executor, reviewer, and PRD creator need a model that",
        ):
            assert snippet in html, snippet
            assert "—" not in snippet
