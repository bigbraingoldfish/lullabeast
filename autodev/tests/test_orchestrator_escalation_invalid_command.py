"""TDD — heal the escalation invalid-command dead-end.

The orchestrator's main-loop escalation-command consumer (the ``elif current_agent ==
"escalation"`` block in ``run()``) reads ``escalation_output.json`` and dispatches the ``command``.
An empty / missing / unrecognised command must default to a **recoverable STOP** and emit an
``escalation_command_invalid`` event — NOT dead-end to ``HALTED_SILENT`` + queue ``FAILED`` (the
documented PIPELINE-CONSTRAINTS.md §5.2 incident, where the agent wrote
``{"command": "WAITING_FOR_HUMAN"}``).

These behavioural tests drive ``Orchestrator.run()`` for a single loop iteration via the established
monkeypatch pattern from ``test_orchestrator_queue.py::TestMainLoopStaleCompleteSyncsQueue``. The
source-text guards lock in the removal of the HALTED_SILENT / FAILED branch.
"""
import importlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _setup(tmp_path, monkeypatch, command_payload, *, seed_active_queue=False):
    """Prepare an Orchestrator poised to consume escalation_output.json on the first loop iteration.

    Returns ``(inst, orch_mod, captured_events, state_file)``. The caller invokes ``inst.run()``.
    State is ``current_agent="escalation"`` + ``pipeline_status="WAITING_FOR_HUMAN"`` so
    ``_should_invoke_escalation_agent()`` is False and the loop takes the poll/consume ``else``.
    """
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))

    import orchestrator as orch_mod
    importlib.reload(orch_mod)

    (tmp_path / "openclaw.json").write_text(
        '{"hooks_url": "http://localhost:18789/hooks/agent", "hooks_token": "test-token", '
        '"gateway": {"port": 18789, "auth": {"token": "gw-test-token"}}}'
    )
    for _role in ("planner", "executor", "reviewer", "escalation"):
        (tmp_path / f"workspace-{_role}").mkdir(exist_ok=True)

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()
    (proj / "roadmap.md").write_text("# r\n- [ ] `CORE-4` | LOW | t\n")

    art = proj / ".autodev" / "pipeline"
    art.mkdir(parents=True)
    # The consumer reads phase_state.json before dispatch — seed a minimal one.
    (art / "phase_state.json").write_text(json.dumps({"escalation_resets": 0, "nuclear_resets": 0}))
    # Output payload first, then the .done sentinel (write-then-done ordering).
    (art / "escalation_output.json").write_text(json.dumps(command_payload))
    (art / "escalation_output.done").write_text("")

    # Symlink pipeline-project -> proj so _escalation_poll_roots() finds the output.
    link = Path(orch_mod.SYMLINK_TARGET)
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(proj, target_is_directory=True)
    # Pin PROJECT_ARTIFACTS_DIR to the resolved dir (the constant was captured at import time).
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(art))

    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(json.dumps({
        "current_phase": 0,
        "current_phase_raw_id": "CORE-4",
        "current_agent": "escalation",
        "pipeline_status": "WAITING_FOR_HUMAN",
        "project_path": str(proj),
        "last_action": "escalation triggered",
    }))

    if seed_active_queue:
        (tmp_path / "pipeline_queue.json").write_text(json.dumps({
            "queue": [{
                "id": str(uuid.uuid4()),
                "project_path": str(proj),
                "name": "proj",
                "state": "ACTIVE",
                "position": 1,
                "parent_id": None,
            }],
            "queue_mode": "auto",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }))

    monkeypatch.setattr(orch_mod, "SkillManager", lambda _ad: MagicMock())

    captured = []
    monkeypatch.setattr(orch_mod, "_write_pipeline_event", lambda *a, **k: captured.append(a))
    # Safety net: the consume path makes no webhook call, but if an unexpected exception routes
    # to run()'s crash-handler we must not make a real HTTP request.
    monkeypatch.setattr(orch_mod, "invoke_agent_webhook", MagicMock(return_value="SUCCESS"))

    inst = orch_mod.Orchestrator()
    monkeypatch.setattr(inst, "acquire_lock", lambda: setattr(inst, "lock_fd", None))
    monkeypatch.setattr(inst, "release_lock", lambda: None)
    monkeypatch.setattr(orch_mod, "cleanup_stranded_temp_files", lambda _root: None)
    monkeypatch.setattr(inst, "run_repo_init_check", lambda: (True, ""))
    monkeypatch.setattr(inst, "_run_startup_planner_phase_zero_and_branch", lambda: "enter_main_loop")
    monkeypatch.setattr(inst, "_maybe_revive_on_queue_halted", lambda: True)

    return inst, orch_mod, captured, state_file


def _invalid_events(captured):
    return [a for a in captured if a and a[0] == "escalation_command_invalid"]


# ---------------------------------------------------------------------------
# Behavioural tests (drive run() one iteration)
# ---------------------------------------------------------------------------

def test_unknown_command_value_transitions_to_stopped(tmp_path, monkeypatch):
    """The real §5.2 incident value (a state, not a command) must default to STOP, not HALTED_SILENT."""
    inst, _mod, _cap, state_file = _setup(tmp_path, monkeypatch, {"command": "WAITING_FOR_HUMAN"})
    inst.run()
    assert inst.state["pipeline_status"] == "STOPPED"
    assert inst.state["pipeline_status"] != "HALTED_SILENT"
    assert json.loads(state_file.read_text())["pipeline_status"] == "STOPPED"


def test_unknown_command_does_not_mark_queue_failed(tmp_path, monkeypatch):
    """The unrecoverable queue FAILED-marking must be gone."""
    inst, _mod, _cap, _sf = _setup(
        tmp_path, monkeypatch, {"command": "WAITING_FOR_HUMAN"}, seed_active_queue=True)
    seen = []
    orig = inst._queue_update_active_entry

    def spy(new_state, extra=None):
        seen.append(new_state)
        return orig(new_state, extra)

    monkeypatch.setattr(inst, "_queue_update_active_entry", spy)
    inst.run()
    assert "FAILED" not in seen


def test_unknown_command_emits_invalid_event(tmp_path, monkeypatch):
    inst, _mod, captured, _sf = _setup(tmp_path, monkeypatch, {"command": "WAITING_FOR_HUMAN"})
    inst.run()
    evs = _invalid_events(captured)
    assert len(evs) == 1
    _et, phase, agent, _detail = evs[0]
    assert phase == "CORE-4"
    assert agent == "escalation"


def test_invalid_event_payload_shape(tmp_path, monkeypatch):
    inst, _mod, captured, _sf = _setup(tmp_path, monkeypatch, {"command": "WAITING_FOR_HUMAN"})
    inst.run()
    detail = _invalid_events(captured)[0][3]
    assert detail == {"received_command": "WAITING_FOR_HUMAN", "defaulted_to": "STOP"}


def test_empty_command_string_transitions_to_stopped_with_event(tmp_path, monkeypatch):
    inst, _mod, captured, _sf = _setup(tmp_path, monkeypatch, {"command": ""})
    inst.run()
    assert inst.state["pipeline_status"] == "STOPPED"
    evs = _invalid_events(captured)
    assert len(evs) == 1
    assert evs[0][3]["received_command"] == ""


def test_missing_command_field_transitions_to_stopped_with_event(tmp_path, monkeypatch):
    inst, _mod, captured, _sf = _setup(tmp_path, monkeypatch, {})
    inst.run()
    assert inst.state["pipeline_status"] == "STOPPED"
    evs = _invalid_events(captured)
    assert len(evs) == 1
    assert evs[0][3]["received_command"] == ""


def test_valid_stop_command_takes_elif_not_else(tmp_path, monkeypatch):
    """Regression guard: a valid command must take its elif branch, NOT the invalid-command else.
    Distinguished by the absence of the escalation_command_invalid event (STOP and the invalid
    path share the STOPPED end-state)."""
    inst, _mod, captured, _sf = _setup(tmp_path, monkeypatch, {"command": "STOP"})
    inst.run()
    assert inst.state["pipeline_status"] == "STOPPED"
    assert _invalid_events(captured) == []


# ---------------------------------------------------------------------------
# Source-text guards (lock in the removal of HALTED_SILENT + FAILED)
# ---------------------------------------------------------------------------

def _orch_source():
    return Path(PIPELINE_DIR, "orchestrator.py").read_text()


def _consumer_slice(src):
    """The escalation poll/consume/dispatch block: from the poll call to the agent-level else.
    Excludes the escalation *delivery-failure* HALTED_SILENT/FAILED (which sits in the invoke
    branch, above the poll call)."""
    start = src.index("out_path = self._poll_escalation_output_json_path")
    end = src.index('print(f"[INFO] Agent {current_agent} logic not reached', start)
    return src[start:end]


def test_consumer_else_no_longer_halts_silent():
    # Assert the behavioural markers are gone, not the bare word: a historical reference to
    # HALTED_SILENT in an explanatory comment is legitimate and must not trip this guard.
    sl = _consumer_slice(_orch_source())
    assert 'transition_state("HALTED_SILENT"' not in sl
    assert '_write_run_summary("HALTED_SILENT"' not in sl


def test_consumer_poll_branch_no_longer_marks_failed():
    assert '"FAILED"' not in _consumer_slice(_orch_source())


def test_invalid_command_event_name_present():
    assert '"escalation_command_invalid"' in _orch_source()
