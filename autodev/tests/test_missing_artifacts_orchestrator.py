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
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


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


def _missing_artifacts_handler_window():
    """The run loop's ``elif gate_result == "MISSING_ARTIFACTS":`` branch."""
    text = (Path(_PIPE) / "orchestrator.py").read_text(encoding="utf-8")
    start = text.index('elif gate_result == "MISSING_ARTIFACTS":')
    end = text.index('elif gate_result == "CONTRACT_FAILURE":', start)
    return text[start:end]


def test_handler_clears_executor_succeeded_before_routing_to_the_executor():
    """The handler routes to the executor with ``executor_retries`` at 0. If the
    executor gate's ``executor_succeeded`` flag is still set, the executor
    branch's crash-recovery skip fires and the executor never runs: the reviewer
    is re-invoked on the same output, the gate finds the same artifacts missing,
    and the second miss escalates. A DeepSeek V4.1 Flash MultiLife run escalated
    its first phase this way. The ROUTE_EXECUTOR handler clears the flag for the
    same reason."""
    window = _missing_artifacts_handler_window()
    route = window.index('self.state["current_agent"] = "executor"')
    clear = window.find('.pop("executor_succeeded", None)')
    assert 0 <= clear < route, (
        "the MISSING_ARTIFACTS handler must clear executor_succeeded before it "
        "routes to the executor, or the crash-recovery skip bypasses the executor"
    )


def test_directive_does_not_promise_a_fresh_session():
    """The re-invoked executor keeps its attempt-1 session key (the handler
    resets executor_retries), so the directive must not claim a fresh session."""
    import orchestrator as orc_module

    assert "fresh session" not in orc_module._missing_artifacts_directive("CORE-E1")


def test_handler_routes_with_the_directive_builder():
    window = _missing_artifacts_handler_window()
    assert "_missing_artifacts_directive(" in window
    assert "MISSING COMPLETION ARTIFACTS" not in window, (
        "the directive text lives in _missing_artifacts_directive, so tests read "
        "what the executor receives"
    )


def test_directive_asks_for_the_two_artifacts_and_keeps_executor_output_json():
    """The executor gate re-checks executor_output.json on the re-invoked run.
    Asked to rewrite it, the executor rebuilds it from memory, and a dropped
    files_deleted entry fails the deletion guard, whose retry hard-resets the
    phase's uncommitted work. The JSON that passed the gate is kept instead
    (independent review), so the directive asks only for the two artifacts."""
    import orchestrator as orc_module

    directive = orc_module._missing_artifacts_directive("CORE-E1")
    assert ".autodev/pipeline/phases/CORE-E1.md" in directive
    assert ".autodev/pipeline/metrics.jsonl" in directive
    assert "PRESERVED on the branch" in directive
    assert "Do not rewrite .autodev/pipeline/executor_output.json" in directive
    assert directive.endswith("Write the archive first, metrics second, sentinel last.")


def test_handler_keeps_executor_output_for_the_reinvoked_run():
    window = _missing_artifacts_handler_window()
    keep = window.find('_ps_ma["executor_keep_output"] = True')
    route = window.index('self.state["current_agent"] = "executor"')
    assert 0 <= keep < route, (
        "the MISSING_ARTIFACTS handler must ask the executor branch to keep the "
        "executor_output.json that passed the gate"
    )


def test_executor_branch_honours_the_keep_flag_once():
    text = (Path(_PIPE) / "orchestrator.py").read_text(encoding="utf-8")
    cleanup = text.index(
        'cleanup_output_files(PROJECT_ARTIFACTS_DIR, "executor", keep_output_json=_keep_output)'
    )
    pop = text.rfind(
        '_keep_output = bool(_ps_keep.pop("executor_keep_output", False))', 0, cleanup
    )
    assert pop != -1 and cleanup - pop < 600, (
        "the executor branch must consume the one-shot keep flag right before "
        "its cleanup and pass that value to it"
    )
    write_back = text.find("self.write_phase_state_atomic(_ps_keep)", pop, cleanup)
    assert write_back != -1, (
        "the consumed flag must be written back, or it keeps stale output on "
        "every later executor run of the phase"
    )


def _reset_orch(tmp_path, monkeypatch, phase_state):
    import orchestrator as orc_module

    ps = tmp_path / "phase_state.json"
    ps.write_text(json.dumps(phase_state), encoding="utf-8")
    monkeypatch.setattr(orc_module, "PHASE_STATE_FILE", str(ps))
    monkeypatch.setattr(orc_module, "SYMLINK_TARGET", str(tmp_path))
    monkeypatch.setattr(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orc_module.subprocess, "run", MagicMock())  # git checkout
    orch = orc_module.Orchestrator.__new__(orc_module.Orchestrator)
    orch.lock_fd = None
    orch.openclaw_config = {"hooks": {"token": "tok"}}
    orch.state = {
        "current_phase": 1,
        "current_phase_raw_id": "CORE-E1",
        "current_agent": "escalation",
        "executor_retries": 0,
        "reviewer_retries": 0,
    }
    orch.write_state = MagicMock()
    orch.transition_state = MagicMock()
    return orch


@pytest.mark.parametrize("reset", ["reset_execution", "reset_reviewer"])
def test_operator_resets_drop_a_pending_reinvoke(tmp_path, monkeypatch, reset):
    """An escalation can land between the MISSING_ARTIFACTS handler and the
    executor branch (a rejected per-phase model override). An operator reset
    starts that agent over, so the pending re-invoke no longer applies:
    RESET_EXECUTION deletes the kept JSON, and an executor told not to rewrite it
    would fail its gate (independent review)."""
    orch = _reset_orch(tmp_path, monkeypatch, {
        "executor_keep_output": True,
        "executor_retry_directive": "MISSING COMPLETION ARTIFACTS: ...",
        "last_error_code": "ERR_MODEL_OVERRIDE_REJECTED",
    })
    if reset == "reset_execution":
        assert orch.reset_execution("escalation") is True
    else:
        orch.reset_reviewer()
    ps = orch.read_phase_state()
    assert "executor_keep_output" not in ps
    assert "executor_retry_directive" not in ps


def _bare_orch():
    import orchestrator as orc_module

    orch = orc_module.Orchestrator.__new__(orc_module.Orchestrator)
    orch.lock_fd = None
    orch.openclaw_config = {}
    orch.state = {}
    return orch


def test_a_kept_output_does_not_turn_a_stalled_reinvoke_into_preempted(tmp_path):
    """If the re-invoked run stalls, the kept JSON must not read as a preempted
    finish: the gate would pass it again and the reviewer would hit the same
    missing artifacts and escalate. Only output written during the attempt
    counts, so the stall is a crash and gets the normal retry (independent
    review)."""
    out = tmp_path / "executor_output.json"
    out.write_text("{}", encoding="utf-8")
    old = time.time() - 120
    os.utime(out, (old, old))
    outcome = _bare_orch().classify_executor_outcome(
        False, str(out), attempt_start=time.time() - 60
    )
    assert outcome == "executor_crashed"


def test_output_written_during_the_attempt_is_still_preempted(tmp_path):
    start = time.time() - 60
    out = tmp_path / "executor_output.json"
    out.write_text("{}", encoding="utf-8")
    assert _bare_orch().classify_executor_outcome(
        False, str(out), attempt_start=start
    ) == "executor_preempted"


def test_executor_branch_classifies_against_its_attempt_start():
    text = (Path(_PIPE) / "orchestrator.py").read_text(encoding="utf-8")
    assert (
        "self.classify_executor_outcome(sentinel_found, executor_output_path, "
        "_attempt_start_time)"
    ) in text


def test_cleanup_before_an_executor_run_removes_executor_output_json(tmp_path):
    from sentinel_poller import cleanup_output_files

    (tmp_path / "executor_output.json").write_text("{}", encoding="utf-8")
    cleanup_output_files(str(tmp_path), "executor")
    assert not (tmp_path / "executor_output.json").exists()


def test_cleanup_can_keep_executor_output_json(tmp_path):
    from sentinel_poller import cleanup_output_files

    for name in ("executor_output.json", "executor_output.done", "executor_activity.stamp"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    cleanup_output_files(str(tmp_path), "executor", keep_output_json=True)
    assert (tmp_path / "executor_output.json").exists()
    assert not (tmp_path / "executor_output.done").exists()
    assert not (tmp_path / "executor_activity.stamp").exists()


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
