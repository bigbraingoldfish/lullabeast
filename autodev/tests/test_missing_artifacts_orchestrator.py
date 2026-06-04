"""
Orchestrator MISSING_ARTIFACTS executor-branch directive-delivery tests.

When the reviewer gate returns MISSING_ARTIFACTS (the done-criteria phase archive
or metrics row is absent), the orchestrator re-invokes the EXECUTOR with a
corrective instruction. That instruction must be DELIVERED to the executor as the
webhook ``message=``, via the one-shot ``executor_retry_directive`` phase-state
field consumed by ``_invoke_executor`` — mirroring the reviewer's
``reviewer_retry_directive`` / ``_invoke_reviewer`` channel.

Regression guard: the instruction used to be written to a dead
``artifact_instruction`` phase-state field that NO code read, so the re-invoked
executor ran blind on its generic default message. These tests prove the
directive REACHES ``invoke_agent_webhook(message=...)`` — not merely that a
phase-state field was written (the dead-write trap that hid the original bug).

Mirrors test_contract_failure_orchestrator.py::TestReviewerDirectiveDelivery.

Spec Reference: PIPELINE-SPEC.md §7 "Gate Scripts > Reviewer Output Gate"
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


OPENCLAW_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE_SCRIPTS_DIR = os.path.join(OPENCLAW_DIR, "autodev", "pipeline", "gate_scripts")
for _p in [GATE_SCRIPTS_DIR, OPENCLAW_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

_PIPE = os.path.join(OPENCLAW_DIR, "autodev", "pipeline")


def _make_executor_orch(tmp_dir, initial_phase_state=None):
    """Return (orch, ps_path) with module-level constants patched to tmp_dir.

    Near-copy of test_contract_failure_orchestrator._make_contract_orch, but
    seeds ``current_agent="executor"`` (this branch exercises the executor
    re-invocation, not the reviewer one). Construction patches the module
    constants only so ``Orchestrator.__new__`` is safe; each test re-patches
    PHASE_STATE_FILE / SYMLINK_TARGET / invoke_agent_webhook for the call.
    """
    import orchestrator as orc_module

    ps_path = os.path.join(tmp_dir, "phase_state.json")
    state_file = os.path.join(tmp_dir, "pipeline_state.json")
    config_file = os.path.join(tmp_dir, "openclaw.json")

    with open(config_file, "w") as f:
        json.dump({"hooks": {"token": "test-tok"}}, f)

    if initial_phase_state is not None:
        with open(ps_path, "w") as f:
            json.dump(initial_phase_state, f)

    with (
        patch.object(orc_module, "STATE_FILE", state_file),
        patch.object(orc_module, "SYMLINK_TARGET", tmp_dir),
        patch.object(orc_module, "LOCK_FILE", os.path.join(tmp_dir, "pipeline.lock")),
        patch.object(orc_module, "CONFIG_FILE", config_file),
        patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
    ):
        from orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch.lock_fd = None
        orch.openclaw_config = {"hooks": {"token": "test-tok"}}
        orch.state = {
            "current_phase": 1,
            "current_phase_raw_id": "CORE-1",
            "current_agent": "executor",
            "planner_retries": 0,
            "executor_retries": 0,
            "reviewer_retries": 0,
            "pipeline_status": "RUNNING",
            "last_action": "",
            "last_action_timestamp": "",
        }
        orch.write_state = MagicMock()
        orch.transition_state = MagicMock()

    return orch, ps_path


_DIRECTIVE = "MISSING COMPLETION ARTIFACTS: produce the phase archive and metrics row"


class TestExecutorDirectiveDelivery:
    """The one-shot ``executor_retry_directive`` field (written by the
    reviewer-gate MISSING_ARTIFACTS handler) is delivered to the executor via the
    webhook ``message=`` on re-invoke, and is one-shot (cleared after delivery).

    R-C guard: prove the directive REACHES invoke_agent_webhook, not just that a
    phase_state field was written — the dead-write trap that hid the old
    ``artifact_instruction`` field."""

    def test_invoke_executor_delivers_directive_as_message(self, tmp_workspace):
        import orchestrator as orc_module

        orch, ps_path = _make_executor_orch(
            tmp_workspace, {"executor_retry_directive": _DIRECTIVE}
        )

        with (
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "invoke_agent_webhook") as mock_hook,
        ):
            mock_hook.return_value = "SUCCESS"
            orch._invoke_executor("pipeline:phase-1:CORE-1:executor-attempt-1", "tok")

        assert mock_hook.called, "_invoke_executor must call invoke_agent_webhook"
        _, kwargs = mock_hook.call_args
        assert kwargs.get("message") == _DIRECTIVE, (
            "the MISSING_ARTIFACTS directive must reach invoke_agent_webhook as "
            "message=, not merely be written to phase_state (the dead-write trap)"
        )

        with open(ps_path) as f:
            state = json.load(f)
        assert not state.get("executor_retry_directive"), (
            "executor_retry_directive must be CLEARED after delivery (one-shot)"
        )

    def test_invoke_executor_without_directive_passes_no_message(self, tmp_workspace):
        """A normal/self-failure executor invocation (no directive set) passes no
        message → the executor's default webhook message applies and it reads
        failure_context.json from disk as usual (that channel is unharmed)."""
        import orchestrator as orc_module

        orch, ps_path = _make_executor_orch(tmp_workspace, {})

        with (
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "invoke_agent_webhook") as mock_hook,
        ):
            mock_hook.return_value = "SUCCESS"
            orch._invoke_executor("pipeline:phase-1:CORE-1:executor-attempt-1", "tok")

        _, kwargs = mock_hook.call_args
        assert not kwargs.get("message"), (
            "with no directive, _invoke_executor must not inject a message "
            "(default executor message + failure_context.json path apply)"
        )

    def test_executor_directive_is_one_shot(self, tmp_workspace):
        """After one delivery the directive is consumed; a second invocation
        carries no message (a stale directive must not re-inject on a later
        normal/self-failure pass)."""
        import orchestrator as orc_module

        orch, ps_path = _make_executor_orch(
            tmp_workspace, {"executor_retry_directive": _DIRECTIVE}
        )

        with (
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "invoke_agent_webhook") as mock_hook,
        ):
            mock_hook.return_value = "SUCCESS"
            orch._invoke_executor("pipeline:phase-1:CORE-1:executor-attempt-1", "tok")
            orch._invoke_executor("pipeline:phase-1:CORE-1:executor-attempt-2", "tok")

        second_kwargs = mock_hook.call_args_list[1].kwargs
        assert not second_kwargs.get("message"), (
            "the directive must not be re-delivered on the second invocation"
        )

    def test_invoke_executor_targets_executor_role(self, tmp_workspace):
        """The webhook is invoked for the executor agent (first positional arg)."""
        import orchestrator as orc_module

        orch, ps_path = _make_executor_orch(
            tmp_workspace, {"executor_retry_directive": _DIRECTIVE}
        )

        with (
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
            patch.object(orc_module, "SYMLINK_TARGET", tmp_workspace),
            patch.object(orc_module, "invoke_agent_webhook") as mock_hook,
        ):
            mock_hook.return_value = "SUCCESS"
            orch._invoke_executor("pipeline:phase-1:CORE-1:executor-attempt-1", "tok")

        args, _ = mock_hook.call_args
        assert args and args[0] == "executor", (
            "_invoke_executor must invoke the executor agent webhook"
        )


def test_dead_artifact_instruction_field_removed():
    """Removal + self-containment guard.

    Proves the dead ``artifact_instruction`` phase-state write is fully retired,
    the live one-shot ``executor_retry_directive`` field is wired, and the
    MISSING_ARTIFACTS directive is self-contained (it re-asserts the preserved-
    work orientation, because delivering message= replaces the executor's default
    message). Catches a regression that reintroduces the dead write or reverts the
    directive to the terse, under-contextualized form.
    """
    text = (Path(_PIPE) / "orchestrator.py").read_text()
    assert "artifact_instruction" not in text, (
        "the dead artifact_instruction phase_state write must be fully retired"
    )
    assert "executor_retry_directive" in text, (
        "MISSING_ARTIFACTS must write the live one-shot executor_retry_directive field"
    )
    assert "PRESERVED on the branch" in text, (
        "the MISSING_ARTIFACTS directive must be self-contained (state that prior "
        "work is preserved) because delivery replaces the executor's default message"
    )
