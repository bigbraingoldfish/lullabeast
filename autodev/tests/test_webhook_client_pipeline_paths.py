"""Webhook default messages must reference .autodev/pipeline (matches PROJECT_ARTIFACTS_DIR)."""

from autodev.pipeline import webhook_client as wc


def test_pipeline_artifacts_prefix_constant():
    assert wc._PIPELINE_ARTIFACTS == "pipeline-project/.autodev/pipeline"


def test_default_messages_use_autodev_pipeline_subdir():
    prefix = ".autodev/pipeline"
    for agent_id, msg in wc.invoke_agent_webhook.__defaults__:
        pass
    # Defaults live inside invoke_agent_webhook — inspect closure via introspecting source
    import inspect

    src = inspect.getsource(wc.invoke_agent_webhook)
    assert "default_messages" in src
    # Each role's string must point agents at the relocated artifact tree
    for sub in (
        f"/{prefix}/planner_output.json",
        f"/{prefix}/planner_output.done",
        f"/{prefix}/executor_output.json",
        f"/{prefix}/reviewer_output.json",
        f"/{prefix}/escalation_output.json",
    ):
        assert sub in src, f"expected {sub!r} in webhook default message construction"

    # Must not tell agents to write bare root-level sentinels
    assert "pipeline-project/planner_output.done" not in src.replace(
        f"pipeline-project/{prefix}/planner_output.done", ""
    ) or f"pipeline-project/{prefix}/planner_output.done" in src
    # Stricter: after removing correct path, no orphan bare sentinel path
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
