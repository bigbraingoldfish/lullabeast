"""Static markers for the retry-confirm model override (escalation surface).

The three recover confirmations (RESET_PHASE / RESET_EXECUTION /
RESET_REVIEWER) carry a collapsed override editor over the same
/api/phase-model-override mechanism as the roadmap row control. Monitor-only:
per-phase overrides act on the active project, so the Queue's deferred
dispatch never enables it. API behavior is covered in
test_phase_model_override_api.py; orchestrator consumption in
autodev/tests/test_phase_model_override.py.
"""


def _html():
    with open("ui/index.html", "r", encoding="utf-8") as f:
        return f.read()


class TestRetryOverrideSection:
    def test_section_and_controls_present(self):
        html = _html()
        assert "function RetryModelOverrideSection(" in html
        for tid in (
            "retry-model-override-section", "retry-override-toggle",
            "retry-override-role", "retry-override-model", "retry-override-set",
            "retry-override-clear", "retry-override-error", "retry-override-warning",
        ):
            assert f'data-testid="{tid}"' in html, tid

    def test_gated_to_the_three_recover_commands(self):
        html = _html()
        assert '["RESET_PHASE", "RESET_EXECUTION", "RESET_REVIEWER"].indexOf(command) !== -1' in html
        assert "{showRetryOverride && <RetryModelOverrideSection rawId={retryPhaseRawId} />}" in html

    def test_collapsed_by_default_but_noticeable(self):
        # A bordered toggle with a caret, plus an active-count marker so a set
        # override stays visible while collapsed.
        html = _html()
        assert "Retry on a different model (optional)" in html
        assert "· {activeCount} set" in html

    def test_monitor_only_wiring(self):
        # The Monitor passes the live phase id; the Queue's deferred dispatch
        # (targetProjectPath set) never enables the section.
        html = _html()
        assert "retryPhaseRawId={targetProjectPath ? null : current_phase_raw_id}" in html
        assert "current_phase_raw_id={pState.current_phase_raw_id}" in html

    def test_copy_is_honest_about_persistence_and_stalls(self):
        # Overrides land immediately and outlive a cancelled confirm; there is
        # still no model-health probe.
        html = _html()
        assert "It takes effect as soon as you set it, even if you" in html
        assert "cancel below. There is no availability check: an unreachable" in html

    def test_new_copy_has_no_em_dashes(self):
        html = _html()
        for snippet in (
            "Retry on a different model (optional)",
            "Runs this phase's planner, executor, or reviewer on a different",
            "model for every remaining attempt, then clears when the phase",
            "closes. It takes effect as soon as you set it, even if you",
        ):
            assert snippet in html, snippet
            assert "—" not in snippet
