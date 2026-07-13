"""Static markers for the Settings "Model roles" card (roster redesign).

The card is a self-fetching Settings section over GET/PUT /api/models/roles
and PUT /api/models/properties, redesigned as a dense agent roster: one row
per agent with checkbox multi-select and a bulk-assign bar, a rich model
picker popover with capability and cost chips, an inline pending-changes bar
that applies and restarts (polling provider-status), a per-model property
editor in a modal behind a footer link, and a read-only bare-metal variant.
Backend behavior is covered in test_models_roles_api.py; these tests pin the
UI wiring and copy.
"""


def _html():
    with open("ui/index.html", "r", encoding="utf-8") as f:
        return f.read()


def _card_segment():
    """The ModelRolesCard source (role metadata through the component body)."""
    html = _html()
    start = html.index("const MODEL_ROLE_GROUPS")
    end = html.index("function SettingsScreen")
    return html[start:end]


class TestModelRolesCard:
    def test_card_rendered_in_settings(self):
        html = _html()
        assert 'data-testid="settings-model-roles-card"' in html
        assert "<ModelRolesCard />" in html

    def test_endpoints_wired(self):
        seg = _card_segment()
        assert '"/api/models/roles"' in seg
        assert '"/api/models/properties"' in seg

    def test_restart_polls_provider_status(self):
        seg = _card_segment()
        assert '"/api/setup/provider-status"' in seg
        assert "applying" in seg
        assert 'data-testid="model-roles-restarting"' in seg

    def test_all_six_roles_described(self):
        seg = _card_segment()
        for role in ("planner", "executor", "reviewer", "prd-creator", "roadmap-converter", "escalation"):
            assert f'"{role}"' in seg

    def test_roles_grouped_in_workflow_order(self):
        # Document agents first (they produce the PRD and roadmap), then the
        # four pipeline agents, introduced as such.
        seg = _card_segment()
        docs = 'roles: ["prd-creator", "roadmap-converter"]'
        pipeline = 'roles: ["planner", "executor", "reviewer", "escalation"]'
        assert docs in seg and pipeline in seg
        assert seg.index(docs) < seg.index(pipeline)
        assert "Pipeline agents" in seg
        assert "phase by phase from the roadmap" in seg

    def test_roster_rows_with_checkbox_multiselect(self):
        # One dense row per agent with a checkbox, plus a select-all header box.
        seg = _card_segment()
        assert 'data-testid={`model-role-row-${role}`}' in seg
        assert 'data-testid={`model-role-check-${role}`}' in seg
        assert 'data-testid="model-roles-select-all"' in seg
        assert "toggleAll" in seg

    def test_bulk_assign_bar(self):
        # Selecting rows raises a bar that assigns one model to the whole
        # selection; vision applies if any selected role needs image input.
        seg = _card_segment()
        assert 'data-testid="model-roles-bulk-bar"' in seg
        assert 'testid="model-roles-bulk-select"' in seg
        assert "selected" in seg and "bulkAssign" in seg
        assert "bulkVision" in seg

    def test_agent_identity_cell(self):
        # Name + hint, a click-toggle info popover with the model suggestion,
        # and the image-required badge with its tooltip on vision roles.
        seg = _card_segment()
        assert 'testid={`model-role-info-${role}`}' in seg
        assert 'data-testid={testid}' in seg  # the shared atoms render the id
        assert "ms-info__pop" in seg
        assert "Suggested" in seg
        assert "ms-req__tip" in seg
        assert "must run on a" in seg  # image-required tooltip copy

    def test_recommendation_notes_present(self):
        seg = _card_segment()
        assert "verifies screenshots as its core gate duty" in seg
        assert "errors here compound through every later phase" in seg
        assert "an economical model is fine" in seg

    def test_rich_picker_popover(self):
        # A searchable popover grouped by tier, not a native select: friendly
        # name, provider, capability and cost chips per option.
        seg = _card_segment()
        assert 'testid={`model-role-select-${role}`}' in seg
        assert "Search models" in seg
        assert "ms-pop__glabel" in seg
        assert '{ key: "cloud", label: "Cloud" }' in seg
        assert '{ key: "local", label: "Local" }' in seg
        assert "ms-chip" in seg
        assert "ctx" in seg

    def test_picker_blocks_text_only_for_vision_roles(self):
        seg = _card_segment()
        assert "visionRequired && isTextOnly(m.id)" in seg
        assert "needs image input" in seg

    def test_local_tier_derived_from_provider(self):
        # The catalog has no tier field; local models are recognised by the
        # provider segment so they group under Local with the green dot.
        seg = _card_segment()
        assert "MODEL_LOCAL_PROVIDER_HINTS" in seg
        assert '"llamacpp"' in seg

    def test_vision_constraint_blocks_save_with_warning(self):
        seg = _card_segment()
        assert 'data-testid="model-roles-vision-warning"' in seg
        assert "visionBlocked" in seg
        assert "visionBlocked.length > 0" in seg  # apply disabled while blocked
        assert 'MODEL_VISION_ROLES = ["executor", "reviewer", "prd-creator"]' in seg

    def test_pending_bar_with_review_diff(self):
        # Changes queue inline: a count, a reviewable from/to diff, discard,
        # and the apply button; no separate confirm modal.
        seg = _card_segment()
        assert 'data-testid="model-roles-pending"' in seg
        assert 'data-testid="model-roles-review-toggle"' in seg
        assert 'data-testid="model-roles-discard"' in seg
        assert 'data-testid="model-roles-save"' in seg
        assert "pending change" in seg
        assert "ms-diff__from" in seg and "ms-diff__to" in seg

    def test_apply_notes_session_interruption(self):
        # The review note carries the restart warning the old confirm modal held.
        seg = _card_segment()
        assert "restarts the OpenClaw gateway" in seg
        assert "interrupted" in seg
        assert "Saves are refused while the pipeline is running." in seg

    def test_property_editor_in_modal_behind_footer_link(self):
        seg = _card_segment()
        assert 'data-testid="model-props-toggle"' in seg
        assert "Adjust model properties" in seg
        assert 'data-testid="model-props-modal"' in seg

    def test_property_editor_fields_present(self):
        seg = _card_segment()
        for tid in (
            "model-props-toggle", "model-props-picker", "model-props-vision",
            "model-props-reasoning", "model-props-context-window",
            "model-props-cost-input", "model-props-cost-output",
            "model-props-cost-cache-read", "model-props-cost-cache-write",
            "model-props-advanced-toggle", "model-props-temperature",
            "model-props-top-p", "model-props-max-tokens",
        ):
            assert f'data-testid="{tid}"' in seg, tid

    def test_property_editor_is_one_model_at_a_time(self):
        # A picker, not a row per registered model: the catalog can be long.
        seg = _card_segment()
        assert "pick a model to edit" in seg
        assert "expandedModel && catalogById[expandedModel]" in seg

    def test_cost_source_of_truth_note(self):
        seg = _card_segment()
        assert 'data-testid="model-props-note"' in seg
        assert "If cost figures look wrong, correct them here first." in seg

    def test_reset_to_defaults_present(self):
        seg = _card_segment()
        assert 'data-testid="model-roles-reset-defaults"' in seg
        assert "Reset to defaults" in seg

    def test_bare_metal_read_only_variant(self):
        seg = _card_segment()
        assert 'data-testid="model-roles-readonly"' in seg
        assert "deploy/.env" in seg

    def test_save_error_surface_present(self):
        # 409 details (pipeline running / setup mode / bare metal) land here.
        seg = _card_segment()
        assert 'data-testid="model-roles-save-error"' in seg
        assert "d.detail" in seg

    def test_pinned_role_reads_from_backend_flag(self):
        # The GET exposes a per-role `pinned` flag (deploy/.env pin); the card
        # consumes it to gate interaction.
        seg = _card_segment()
        assert "rolePinned" in seg
        assert ".pinned" in seg

    def test_pinned_role_picker_is_read_only(self):
        # A pinned role's picker renders static (no popover), and its checkbox
        # is disabled so it cannot be selected or bulk-assigned.
        seg = _card_segment()
        assert "readOnly={pinned}" in seg
        assert "ms-select--static" in seg
        assert "disabled={restarting || pinned}" in seg
        assert "assignableRoles" in seg  # selection is over non-pinned roles

    def test_pinned_role_row_note_and_explainer(self):
        # Per-row note plus a card-level explainer, both naming deploy/.env and
        # the remedy (edit it, restart the container).
        seg = _card_segment()
        assert 'data-testid={`model-role-pinned-${role}`}' in seg
        assert "Pinned in deploy/.env" in seg
        assert 'data-testid="model-roles-pinned-explainer"' in seg
        assert "restart the container" in seg

    def test_no_op_save_is_reported_not_silent(self):
        # The post-restart re-read diffs what we tried to save against what
        # landed; anything overridden (a deploy/.env pin) is named in an
        # explicit surface instead of a bare success.
        seg = _card_segment()
        assert "attemptRef" in seg
        assert 'data-testid="model-roles-not-applied"' in seg
        assert "pinned in deploy/.env" in seg
        assert "not changed" in seg

    def test_no_em_dashes_in_card(self):
        # UI copy standard: commas and periods, never em dashes.
        assert "—" not in _card_segment()
