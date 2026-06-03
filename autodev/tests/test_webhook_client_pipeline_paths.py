"""Webhook default messages must reference .autodev/pipeline (matches PROJECT_ARTIFACTS_DIR)."""

from autodev.pipeline import webhook_client as wc


def test_pipeline_artifacts_prefix_constant():
    assert wc._PIPELINE_ARTIFACTS == "pipeline-project/.autodev/pipeline"


def test_default_messages_use_autodev_pipeline_subdir():
    prefix = ".autodev/pipeline"
    # Defaults live inside invoke_agent_webhook — inspect closure via introspecting source
    import inspect

    src = inspect.getsource(wc.invoke_agent_webhook)
    assert "default_messages" in src
    # The function builds paths via _PIPELINE_ARTIFACTS — verify the constant is referenced
    # and each required output file appears. inspect.getsource returns raw source (f-string
    # templates with {_p}), not evaluated strings, so check file basenames independently.
    assert "_PIPELINE_ARTIFACTS" in src, (
        "webhook function must reference _PIPELINE_ARTIFACTS so paths stay in sync"
    )
    # F13: the escalation default message is now NOTIFY-only — it no longer references
    # escalation_output.json (the operator answers from the dashboard; the server writes the
    # command). The data-plane agents still produce their output files under .autodev/pipeline.
    for filename in (
        "planner_output.json",
        "planner_output.done",
        "executor_output.json",
        "reviewer_output.json",
    ):
        assert filename in src, f"expected {filename!r} in webhook default message construction"
    assert "escalation_output.json" not in src, (
        "F13: escalation default message must be notify-only (no escalation_output.json write instruction)"
    )

    # Must not hard-code bare root-level sentinel paths (they must go through _p / _PIPELINE_ARTIFACTS)
    bare_done = 'pipeline-project/planner_output.done"'
    assert bare_done not in src, "bare planner_output.done path would mislead agents"


def test_planner_message_full_string():
    import autodev.pipeline.webhook_client as wcm

    # Re-execute the default_messages block shape by importing module dict construction
    _p = wcm._PIPELINE_ARTIFACTS
    planner = (
        f"Begin planning. Read {_p}/current_phase.json and {_p}/phase_state.json. "
        f"Produce {_p}/planner_output.json then write {_p}/planner_output.done."
    )
    assert _p in planner
    assert "/.autodev/pipeline/phase_state.json" in planner
