"""Static markers for the per-phase model override control (Stage E UI).

The RoadmapPanel's expanded row exposes the override control on current and
upcoming phases only, a badge marks rows carrying an override, and mutations
go through POST/DELETE /api/phase-model-override. API behavior is covered in
test_phase_model_override_api.py; orchestrator consumption in
autodev/tests/test_phase_model_override.py.
"""


def _html():
    with open("ui/index.html", "r", encoding="utf-8") as f:
        return f.read()


class TestPhaseOverrideUi:
    def test_endpoint_wired(self):
        html = _html()
        assert "'/api/phase-model-override'" in html

    def test_control_gated_to_open_phases(self):
        html = _html()
        assert 'data-testid="phase-model-override-control"' in html
        assert "phase.status === 'in_progress' || phase.status === 'pending'" in html

    def test_badge_and_controls_present(self):
        html = _html()
        for tid in (
            "phase-override-badge", "phase-override-role", "phase-override-model",
            "phase-override-set", "phase-override-clear", "phase-override-error",
            "phase-override-warning",
        ):
            assert f'data-testid="{tid}"' in html, tid

    def test_only_the_three_phase_roles_offered(self):
        html = _html()
        assert '<option value="planner">planner</option>' in html
        assert '<option value="executor">executor</option>' in html
        assert '<option value="reviewer">reviewer</option>' in html

    def test_help_copy_is_honest_about_stalls(self):
        # No model-health probe exists; the control says what failure looks like.
        html = _html()
        assert "There is no availability check: an unreachable model" in html
        assert "surfaces as a stall, then an escalation" in html
        assert "clears when" in html

    def test_new_copy_has_no_em_dashes(self):
        html = _html()
        for snippet in (
            "Run this phase's planner, executor, or reviewer on a different model.",
            "surfaces as a stall, then an escalation.",
            "Could not set the override.",
            "Could not clear the override.",
        ):
            assert snippet in html, snippet
            assert "—" not in snippet
