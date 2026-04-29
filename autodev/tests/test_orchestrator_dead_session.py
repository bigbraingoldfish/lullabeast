"""Dead-session detection: if the OpenClaw gateway registers a session that
terminated immediately (runtimeMs == 0, stopReason == "error"), the orchestrator
must detect this after webhook fire and route to escalation immediately —
without burning the full sentinel idle threshold and without consuming
executor_retries / reviewer_retries.

Symptom this guards against: a 402-style provider error returns a response
that OpenClaw records as a session with zero runtime and an errorMessage.
poll_for_sentinel_with_idle_detect would otherwise wait the full 120s/300s
idle window before declaring timeout, and the timeout path increments retry
counters as if this were a code-quality failure.

Fix-ID: dead-session-on-arrival
"""

import ast
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
ORCHESTRATOR_PATH = os.path.join(PIPELINE_DIR, "orchestrator.py")

for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Helper unit tests — _check_session_dead_on_arrival
# ---------------------------------------------------------------------------


def _import_helper():
    import orchestrator  # noqa: F401
    return orchestrator._check_session_dead_on_arrival


def test_helper_returns_false_when_sessions_file_missing(tmp_path):
    fn = _import_helper()
    is_dead, msg = fn(str(tmp_path / "does-not-exist.json"), "agent:executor:foo")
    assert is_dead is False
    assert msg == ""


def test_helper_returns_false_when_key_absent(tmp_path):
    fn = _import_helper()
    sessions_path = tmp_path / "sessions.json"
    sessions_path.write_text(json.dumps({"agent:executor:other": {"runtimeMs": 5000}}))
    is_dead, msg = fn(str(sessions_path), "agent:executor:foo")
    assert is_dead is False
    assert msg == ""


def test_helper_returns_false_for_healthy_running_session(tmp_path):
    fn = _import_helper()
    sessions_path = tmp_path / "sessions.json"
    sessions_path.write_text(json.dumps({
        "agent:executor:foo": {
            "runtimeMs": 0,
            "startedAt": "2026-04-28T00:00:00Z",
            # No stopReason / endedAt → still active, not dead
        }
    }))
    is_dead, _ = fn(str(sessions_path), "agent:executor:foo")
    assert is_dead is False


def test_helper_detects_dead_session_with_runtime_zero_and_error(tmp_path):
    fn = _import_helper()
    sessions_path = tmp_path / "sessions.json"
    sessions_path.write_text(json.dumps({
        "agent:executor:foo": {
            "runtimeMs": 0,
            "stopReason": "error",
            "errorMessage": "402 Payment Required: insufficient credits",
            "startedAt": "2026-04-28T00:00:00Z",
            "endedAt": "2026-04-28T00:00:00Z",
        }
    }))
    is_dead, msg = fn(str(sessions_path), "agent:executor:foo")
    assert is_dead is True
    assert "402" in msg


def test_helper_handles_corrupt_json(tmp_path):
    fn = _import_helper()
    sessions_path = tmp_path / "sessions.json"
    sessions_path.write_text("{not valid json")
    is_dead, msg = fn(str(sessions_path), "agent:executor:foo")
    assert is_dead is False
    assert msg == ""


def test_helper_returns_false_when_runtime_nonzero_even_with_error(tmp_path):
    """A session that ran for some time and then errored is NOT dead-on-arrival.
    Real failures with non-zero runtime should go through the normal sentinel path."""
    fn = _import_helper()
    sessions_path = tmp_path / "sessions.json"
    sessions_path.write_text(json.dumps({
        "agent:executor:foo": {
            "runtimeMs": 12345,
            "stopReason": "error",
            "errorMessage": "tool call failed",
        }
    }))
    is_dead, _ = fn(str(sessions_path), "agent:executor:foo")
    assert is_dead is False


# ---------------------------------------------------------------------------
# Static AST checks — guard the integration in the executor and reviewer branches
# ---------------------------------------------------------------------------


def _branch_calls(branch_label: str):
    """Return list of Call.func names (id or attr) inside the specified branch
    of the main agent dispatch (planner / executor / reviewer / escalation)."""
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)

    found = []

    class V(ast.NodeVisitor):
        def __init__(self):
            self._in_branch = False

        def visit_If(self, node):
            test = node.test
            is_branch = False
            if isinstance(test, ast.Compare):
                left = test.left
                comps = test.comparators
                ops = test.ops
                if isinstance(ops[0], ast.Eq) and (
                    (isinstance(left, ast.Constant) and left.value == branch_label)
                    or (len(comps) == 1 and isinstance(comps[0], ast.Constant) and comps[0].value == branch_label)
                ):
                    is_branch = True

            if is_branch:
                prev = self._in_branch
                self._in_branch = True
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        f = child.func
                        if isinstance(f, ast.Name):
                            found.append(f.id)
                        elif isinstance(f, ast.Attribute):
                            found.append(f.attr)
                self._in_branch = prev
            else:
                self.generic_visit(node)

    V().visit(tree)
    return found


def test_executor_branch_calls_dead_session_helper():
    calls = _branch_calls("executor")
    assert "_check_session_dead_on_arrival" in calls, (
        "_check_session_dead_on_arrival must be called in the executor branch "
        "after sessions.json lookup, before poll_for_sentinel_with_idle_detect."
    )


def test_reviewer_branch_calls_dead_session_helper():
    calls = _branch_calls("reviewer")
    assert "_check_session_dead_on_arrival" in calls, (
        "_check_session_dead_on_arrival must be called in the reviewer branch "
        "after sessions.json lookup, before poll_for_sentinel_with_idle_detect."
    )


def test_dead_on_arrival_error_code_constant_present():
    """The error code ERR_SESSION_DEAD_ON_ARRIVAL must be referenced in
    orchestrator.py so it appears in phase_state.last_error_code on escalation."""
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    assert "ERR_SESSION_DEAD_ON_ARRIVAL" in source, (
        "The orchestrator must write last_error_code=ERR_SESSION_DEAD_ON_ARRIVAL "
        "when a dead-on-arrival session is detected, so the UI escalation panel "
        "and Signal notification show a meaningful reason."
    )
