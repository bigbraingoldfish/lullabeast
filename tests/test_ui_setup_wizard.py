"""Static markers for the first-run setup wizard (ui/index.html).

The setup-mode screen is a four-step wizard: add model sources (cloud key
and/or a probed local server), assign agents on the Settings-style roster,
confirm model properties (suggested values vs needs-confirmation flags), then
one save. It reuses the Settings card's role metadata, picker, and chips so
the two surfaces never drift. Endpoint behavior is covered in
test_setup_provider_key.py and test_setup_local_models.py.
"""


def _html():
    with open("ui/index.html", "r", encoding="utf-8") as f:
        return f.read()


class TestWizardShell:
    def test_step_sequence_present(self):
        html = _html()
        assert 'data-testid="setup-wizard-steps"' in html
        for label in ('"Add models"', '"Assign agents"', '"Configure"', '"Finish"'):
            assert label in html, label
        assert 'data-testid="setup-wizard-next"' in html
        assert 'data-testid="setup-wizard-back"' in html

    def test_gates_are_soft_except_the_vision_constraint(self):
        # The only hard block mirrors what the backend refuses anyway: a
        # screenshot-reading agent on a text-only model.
        html = _html()
        assert 'data-testid="setup-wizard-gate"' in html
        assert "sit on a text-only model" in html

    def test_skip_stays_on_the_first_step(self):
        html = _html()
        assert 'data-testid="setup-skip-provider"' in html
        assert 'data-testid="setup-skip-confirm-modal"' in html


class TestAssignStep:
    def test_roster_reuses_settings_metadata_and_atoms(self):
        # Same single source of truth as the Settings card: role groups, the
        # identity cell with its suggestion popover, and the rich picker.
        html = _html()
        assert "function SetupAssignRoster(" in html
        assert "MODEL_ROLE_GROUPS.map((group)" in html
        assert "<ModelRoleLabel role={role} />" in html
        assert "testid={`${idPrefix}-select-${role}`}" in html

    def test_multiselect_and_bulk_bar(self):
        html = _html()
        assert 'data-testid={`${idPrefix}-check-${role}`}' in html
        assert 'data-testid={`${idPrefix}-select-all`}' in html
        assert 'data-testid={`${idPrefix}-bulk-bar`}' in html

    def test_vision_warning_template(self):
        html = _html()
        assert 'data-testid={`${idPrefix}-vision-warning`}' in html

    def test_only_changed_roles_ride_the_cloud_submit(self):
        html = _html()
        assert "changedCloudRoles" in html
        assert "if (Object.keys(changedCloudRoles).length > 0) payload.roles = changedCloudRoles;" in html


class TestConfigureStep:
    def test_cloud_form_covers_price_and_advanced_options(self):
        html = _html()
        for tid in (
            "setup-props-vision", "setup-props-reasoning",
            "setup-props-context-window", "setup-props-max-tokens",
            "setup-props-cost-input", "setup-props-cost-output",
            "setup-props-cost-cache-read", "setup-props-cost-cache-write",
            "setup-props-advanced-toggle", "setup-props-temperature",
            "setup-props-top-p",
        ):
            assert f'data-testid="{tid}"' in html, tid

    def test_suggested_vs_missing_presentation(self):
        # A suggestion is tap-to-apply and names its source; a missing value
        # carries an explicit needs-confirmation flag.
        html = _html()
        assert "function SuggestChips(" in html
        assert "function ModelMetaFlag(" in html
        assert "needs confirmation:" in html
        assert "(registered)" in html
        assert "(detected)" in html

    def test_ready_models_tucked_away(self):
        html = _html()
        assert 'data-testid="setup-configure-ready"' in html

    def test_property_confirmations_ride_the_key_submit(self):
        # Scoped to assigned models: an edited-then-unassigned model's props
        # do not ride the save.
        html = _html()
        assert "buildModelPropsBody(assignedEdits, catalogById)" in html
        assert "if (propsBody) payload.properties = propsBody;" in html


class TestLocalPath:
    def test_server_choice_and_recheck(self):
        html = _html()
        assert 'data-testid="setup-local-choose"' in html
        assert 'data-testid="setup-local-recheck"' in html

    def test_primary_is_the_most_assigned_model(self):
        html = _html()
        assert "localPrimary" in html
        assert "counts[b] - counts[a]" in html

    def test_only_diverging_roles_ride_the_submit(self):
        html = _html()
        assert "if (Object.keys(customized).length > 0) payload.roles = customized;" in html

    def test_every_assigned_local_model_is_configurable(self):
        # One config form per assigned model: the primary persists through the
        # LOCAL_MODEL_* fields, the others through the properties overlay.
        html = _html()
        assert "{uniqueAssigned.map((mid) => (" in html
        assert 'props["local/" + mid] = out;' in html
        assert "if (Object.keys(props).length > 0) payload.properties = props;" in html


class TestFinishSave:
    """The one-save finish must stay safe against the entrypoint's unlock
    watcher, which fires on the first non-empty provider key file poll."""

    def test_local_wiring_posts_before_the_key(self):
        # Both endpoints write the provider key file and an unlock can fire
        # between the two POSTs; the local write owns the role assignments,
        # so it must be the one an early unlock sees complete.
        html = _html()
        finish = html[html.index("const onFinish"):html.index("const onSkipConfirm")]
        assert finish.index("/api/setup/local-model") < finish.index("/api/setup/provider-key")

    def test_key_failure_after_local_wiring_warns_on_the_restart_screen(self):
        # The local unlock cannot be recalled, so a late key failure surfaces
        # as a warning during restart instead of stranding the wizard.
        html = _html()
        assert 'data-testid="setup-key-warning"' in html
        assert "the key was not saved" in html

    def test_a_server_with_no_models_is_not_selectable(self):
        # Selecting an empty server would leave nothing to assign and a
        # finish that never restarts; the choose button refuses instead.
        html = _html()
        assert "disabled={!chosen && (!server.models || server.models.length === 0)}" in html
        assert '(source === "local" && !localPrimary)' in html


class TestRequiredAnswers:
    """Unknown values are never defaulted: the probe's silence becomes an
    explicit choose-one, and the wizard refuses to continue past Configure
    while any flagged value is unanswered."""

    def test_unknown_image_support_has_no_default(self):
        html = _html()
        assert '{detectedVision === "" && <option value="">choose one</option>}' in html
        assert "Defaulted to Yes" not in html

    def test_undetected_reasoning_has_no_auto(self):
        html = _html()
        assert '<option value="">choose one</option>' in html
        assert "Auto (detected {detectedReasoning})" in html
        assert "Auto (not detected)" not in html

    def test_configure_gate_blocks_on_unanswered_values(self):
        html = _html()
        assert "Answer the flagged values to continue." in html
        assert 'step.key === "configure" && attentionIds.length > 0' in html

    def test_finish_has_no_confirm_later_deferral(self):
        # The wizard finishes what it starts; it never punts confirmation to a
        # different screen.
        html = _html()
        assert "confirm them later in Settings" not in html


class TestContextGuidance:
    def test_context_size_chips(self):
        html = _html()
        for v in ("98304", "131072", "200000", "262144"):
            assert v in html, v

    def test_under_64k_warning_shared_by_wizard_and_settings(self):
        html = _html()
        assert "MODEL_CTX_RECOMMENDED_MIN = 64000" in html
        assert "Under 64k is likely too low for pipeline work." in html
        assert 'data-testid="setup-local-ctx-low"' in html
        assert 'data-testid="setup-props-ctx-low"' in html
        assert 'data-testid="model-props-ctx-low"' in html


class TestCopyStandards:
    def test_new_copy_has_no_em_dashes(self):
        html = _html()
        for snippet in (
            "needs confirmation:",
            "Prefilled from the registered catalog.",
            "You can change each role's model later in Settings.",
            "Both sources set: your agents start on the local server",
        ):
            assert snippet in html, snippet
            assert "—" not in snippet
