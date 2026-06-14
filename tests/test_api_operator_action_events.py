"""P1-C — server-side ``operator_action`` events.

Human interventions (escalation commands, stop, resume, git-recover, queue edits,
launch, switch-project) were durable only if the orchestrator happened to consume
them (`escalation_resolve`) — a banked-then-superseded command, or any queue edit,
left no trace. P1-C makes every operator mutation a first-class `operator_action`
event so "interventions by type per week" is a `jq` away.

Operators act while the orchestrator may be stopped, so the SERVER writes these —
via the SAME `event_log.append_pipeline_event` the orchestrator uses (one writer
function, one schema, one rotation policy). These pin the writer's output shape, its
non-raising contract, that the live `/api/stop` endpoint actually emits, and — via a
source guard — that every operator endpoint has an emit site (so none is silently
missed and the queue emits stay OUTSIDE the side-effect-free CAS closures).

Fixture pattern mirrors ``test_api_state_run_id.py``.
"""
import json
import os
import re
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import ui.server as srv
from ui.server import app, _write_operator_event

client = TestClient(app)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def _config(temp_dir):
    project_root = os.path.join(temp_dir, "pipeline_project")
    os.makedirs(project_root, exist_ok=True)
    return {
        "pipeline_state_path": os.path.join(temp_dir, "pipeline_state.json"),
        "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
        "lock_path": os.path.join(temp_dir, "pipeline.lock"),
        "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
        "project_dir_path": project_root,
    }


def _write_state(cfg, state):
    with open(cfg["pipeline_state_path"], "w") as f:
        json.dump(state, f)


def _read_events(cfg):
    p = cfg["events_path"]
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p).read().splitlines() if l.strip()]


# --- writer unit tests ------------------------------------------------------

def test_writer_emits_operator_action_line(temp_dir):
    """The writer produces a canonical operator_action line carrying run_id / phase /
    project (from pipeline_state) and the action/target/source detail."""
    cfg = _config(temp_dir)
    _write_state(cfg, {
        "run_id": "RID-op", "current_phase_raw_id": "CORE-2", "project_path": "/x/myproj",
    })
    _write_operator_event(cfg, "stop", target="myproj")
    row = _read_events(cfg)[-1]
    assert row["event"] == "operator_action"
    assert row["agent"] == "operator"
    assert row["run_id"] == "RID-op"
    assert row["phase"] == "CORE-2"
    assert row["project"] == "myproj"
    assert row["detail"] == {"action": "stop", "target": "myproj", "source": "ui"}


def test_writer_includes_command_only_when_passed(temp_dir):
    """A `command` rides the detail only when supplied (escalation commands), never
    as a spurious empty key."""
    cfg = _config(temp_dir)
    _write_state(cfg, {"run_id": "R"})
    _write_operator_event(cfg, "command", target="proj", command="RESET_PHASE")
    row = _read_events(cfg)[-1]
    assert row["detail"]["command"] == "RESET_PHASE"


def test_writer_run_id_null_when_state_absent(temp_dir):
    """No pipeline_state file → run_id null, still a well-formed event (operator
    actions happen while the orchestrator/state may not exist yet)."""
    cfg = _config(temp_dir)
    _write_operator_event(cfg, "queue_add", target="x")
    row = _read_events(cfg)[-1]
    assert row["run_id"] is None
    assert row["detail"]["action"] == "queue_add"


def test_writer_nonraising_without_events_path():
    """Telemetry must never break an API request: a config without events_path is a
    silent no-op, not an exception."""
    _write_operator_event({"pipeline_state_path": "/nonexistent"}, "stop")  # must not raise


# --- live endpoint wiring ---------------------------------------------------

def test_stop_endpoint_emits_operator_action(temp_dir):
    """The live `/api/stop` endpoint emits a `stop` operator_action on success —
    proves the wiring fires end-to-end, not just the writer in isolation."""
    cfg = _config(temp_dir)
    _write_state(cfg, {"pipeline_status": "RUNNING", "current_phase": 1, "run_id": "RID-s"})
    with patch("ui.server.load_config", return_value=cfg):
        resp = client.post("/api/stop")
    assert resp.status_code == 200, resp.text
    actions = [e["detail"]["action"] for e in _read_events(cfg) if e.get("event") == "operator_action"]
    assert "stop" in actions


# --- source guard: every operator endpoint has an emit site -----------------

EXPECTED_ACTIONS = {
    "command", "git_recover", "resume_ready", "resume_orchestrator", "stop",
    "queue_mode", "queue_reorder", "queue_add", "queue_delete", "queue_clear",
    "queue_position", "queue_parent", "queue_relaunch", "queue_revalidate",
    "switch_project", "launch",
}


def _emit_action_literals():
    src = open(srv.__file__, "r", encoding="utf-8").read()
    # calls: _write_operator_event(<config-var>, "<action>" ...) — the def line
    # (`def _write_operator_event(config, action`) has no quoted action literal, so
    # it is not matched.
    return re.findall(r'_write_operator_event\(\s*[A-Za-z_][A-Za-z0-9_()]*\s*,\s*"([a-z_]+)"', src)


def test_every_operator_action_is_emitted():
    """Source guard: each of the 16 operator endpoints' action labels appears at an
    emit site. Catches a forgotten endpoint (the taxonomy of interventions must be
    complete) without 16 heavy integration tests."""
    literals = set(_emit_action_literals())
    missing = EXPECTED_ACTIONS - literals
    assert not missing, f"operator endpoints with no operator_action emit: {sorted(missing)}"


def test_emit_site_count_floor():
    """`command` (3 success paths) and `stop` (2) emit at multiple sites, so the total
    emit-call count exceeds the 16 endpoints; a floor guards against bulk removal."""
    assert len(_emit_action_literals()) >= 16
