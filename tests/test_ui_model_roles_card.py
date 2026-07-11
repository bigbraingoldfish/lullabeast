"""Static markers for the Settings "Model roles" card (Stage C).

The card is a self-fetching Settings section over GET/PUT /api/models/roles
and PUT /api/models/properties: per-role pickers with capability chips and
recommendation notes, a per-model property editor backed by the overlay, a
save-and-restart confirm flow that polls provider-status, and a read-only
bare-metal variant. Backend behavior is covered in test_models_roles_api.py;
these tests pin the UI wiring and copy.
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

    def test_recommendation_sits_under_the_description(self):
        seg = _card_segment()
        assert seg.index("{info.blurb &&") < seg.index("{info.note &&") < seg.index("data-testid={`model-role-select-")

    def test_recommendation_notes_present(self):
        seg = _card_segment()
        assert "verifies screenshots as its core gate duty" in seg
        assert "errors here compound through every later phase" in seg
        assert "an economical model is fine" in seg

    def test_vision_constraint_blocks_save_with_warning(self):
        seg = _card_segment()
        assert 'data-testid="model-roles-vision-warning"' in seg
        assert "visionBlocked" in seg
        assert "visionBlocked.length > 0" in seg  # save disabled while blocked
        assert 'MODEL_VISION_ROLES = ["executor", "reviewer", "prd-creator"]' in seg

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

    def test_confirm_modal_notes_session_interruption(self):
        seg = _card_segment()
        assert 'data-testid="model-roles-confirm-modal"' in seg
        assert 'data-testid="model-roles-confirm-save"' in seg
        assert "restarts the OpenClaw gateway" in seg
        assert "interrupted" in seg

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

    def test_no_em_dashes_in_card(self):
        # UI copy standard: commas and periods, never em dashes.
        assert "—" not in _card_segment()
