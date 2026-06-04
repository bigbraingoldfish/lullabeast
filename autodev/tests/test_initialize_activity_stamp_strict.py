"""Section 5a — ``initialize_activity_stamp()`` return value must be honoured.

``initialize_activity_stamp(workspace_dir, agent)`` returns ``False`` when
the workspace directory does not exist or the stamp file cannot be
written.  Previously the three orchestrator call sites discarded that
return value, meaning a silent failure to seed the stamp also silently
disabled the stall-detection path (``poll_for_sentinel`` would call
``os.path.exists(stall_detection_path)`` which would always be False,
skipping the stall branch entirely).

These tests pin that every call site captures the return value and
escalates loudly rather than entering ``poll_for_sentinel`` with stall
detection silently broken.
"""

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402
import sentinel_poller as sp  # noqa: E402
import pytest

_ORCH_SRC = open(
    os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8"
).read()


@pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
def test_initialize_activity_stamp_return_value_is_captured(agent):
    """Each call site must honour the return value of stamp init.

    Accepts either:
      * A direct ``var = initialize_activity_stamp(PROJECT_ARTIFACTS_DIR, "agent")``
        assignment, or
      * A call through ``_init_activity_stamp_or_escalate("agent")`` which
        wraps the initializer and centralises the loud-failure path.

    A bare ``initialize_activity_stamp(...)`` call with no assignment is
    the original silent-failure bug — that pattern must NOT exist.
    """
    direct_capture = re.compile(
        r"^[ \t]*[\w_]+\s*=\s*initialize_activity_stamp\(\s*"
        r"PROJECT_ARTIFACTS_DIR\s*,\s*['\"]" + agent + r"['\"]",
        re.MULTILINE,
    )
    helper_capture = re.compile(
        r"^[ \t]*[\w_]+\s*=\s*self\._init_activity_stamp_or_escalate\(\s*['\"]"
        + agent + r"['\"]\s*\)",
        re.MULTILINE,
    )
    assert direct_capture.search(_ORCH_SRC) or helper_capture.search(_ORCH_SRC), (
        f"orchestrator must capture the return value of "
        f"initialize_activity_stamp(..., {agent!r}) — either directly or "
        f"via _init_activity_stamp_or_escalate({agent!r}); a bare call "
        f"discards the False signal and silently disables stall detection"
    )

    # Guard against regression: the bare-call pattern must NOT exist.
    bare_call = re.compile(
        r"^[ \t]*initialize_activity_stamp\(\s*PROJECT_ARTIFACTS_DIR\s*,\s*"
        r"['\"]" + agent + r"['\"]\s*\)\s*$",
        re.MULTILINE,
    )
    assert not bare_call.search(_ORCH_SRC), (
        f"Found bare initialize_activity_stamp(..., {agent!r}) call with no "
        f"assignment — return value is discarded, silent stall-detection bug"
    )


def test_orchestrator_logs_on_stamp_init_failure():
    """A log line referencing activity-stamp init failure must exist so
    operators see the failure mode in /tmp/orchestrator.log.

    F10(a): the severity moved from ``[FATAL]`` to ``[WARN]`` because the
    failure now routes to escalation (recoverable) rather than dead-ending at a
    silent halt; the message text is the locked part."""
    assert "[WARN] activity stamp init failed for" in _ORCH_SRC, (
        "orchestrator must emit a '[WARN] activity stamp init failed for ...' "
        "log line when initialize_activity_stamp returns False"
    )


def test_orchestrator_routes_stamp_init_failure_to_escalation():
    """F10(a): a stamp-init failure must route to the escalation agent
    (``current_agent = "escalation"`` + ``transition_state("RUNNING", …)``) so
    the operator is notified (advisory + Signal via the escalation agent) and
    can recover from the dashboard — NOT dead-end at the old silent
    ``HALTED_SILENT``. Replaces the prior HALTED_SILENT assertion (a passing
    test for removed behavior)."""
    idx = _ORCH_SRC.find("activity stamp init failed for")
    assert idx != -1, (
        "Could not find the 'activity stamp init failed for ...' message"
    )
    end = _ORCH_SRC.find("\n    def ", idx)
    window = _ORCH_SRC[idx : end if end != -1 else idx + 1500]
    assert re.search(r'current_agent"\]\s*=\s*"escalation"', window), (
        "stamp-init failure must set current_agent = 'escalation'"
    )
    assert re.search(r'transition_state\(\s*"RUNNING"', window), (
        "stamp-init failure must transition_state('RUNNING', …) so the "
        "escalation branch fires on the next loop iteration"
    )
    assert not re.search(r'transition_state\(\s*"HALTED_SILENT"', window), (
        "stamp-init failure must no longer transition to HALTED_SILENT — "
        "F10(a) routes it to escalation instead"
    )


def test_initialize_activity_stamp_still_returns_false_on_missing_workspace(
    tmp_path,
):
    """Pre-condition: the underlying helper still returns False (not raises)
    when the workspace directory does not exist.  The orchestrator's
    new strict handling depends on this contract."""
    missing = tmp_path / "no-such-dir"
    assert (
        sp.initialize_activity_stamp(str(missing), "executor") is False
    )


def test_initialize_activity_stamp_returns_true_on_writable_workspace(
    tmp_path,
):
    """Pre-condition: the helper still returns True on a writable
    workspace and actually creates the stamp file."""
    assert sp.initialize_activity_stamp(str(tmp_path), "executor") is True
    assert (tmp_path / "executor_activity.stamp").exists()
