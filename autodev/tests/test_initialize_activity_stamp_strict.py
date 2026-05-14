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
      * A call through ``_init_activity_stamp_or_halt("agent")`` which
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
        r"^[ \t]*[\w_]+\s*=\s*self\._init_activity_stamp_or_halt\(\s*['\"]"
        + agent + r"['\"]\s*\)",
        re.MULTILINE,
    )
    assert direct_capture.search(_ORCH_SRC) or helper_capture.search(_ORCH_SRC), (
        f"orchestrator must capture the return value of "
        f"initialize_activity_stamp(..., {agent!r}) — either directly or "
        f"via _init_activity_stamp_or_halt({agent!r}); a bare call "
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


def test_orchestrator_logs_fatal_on_stamp_init_failure():
    """A ``[FATAL]`` log line referencing activity-stamp init must exist so
    operators see the failure mode in /tmp/orchestrator.log."""
    # The literal is locked so future grep-based monitoring can rely on it.
    assert "[FATAL]" in _ORCH_SRC and "activity stamp" in _ORCH_SRC, (
        "orchestrator must emit a '[FATAL] activity stamp init failed ...' "
        "log line when initialize_activity_stamp returns False"
    )


def test_orchestrator_escalates_to_halted_silent_on_stamp_init_failure():
    """The fatal stamp-init failure must transition to ``HALTED_SILENT``
    (mirrors the verify-failed escalation in Section 0/2) rather than
    proceeding into ``poll_for_sentinel`` with broken stall detection."""
    # Find the [FATAL] activity-stamp message and check a HALTED_SILENT
    # transition appears within ~400 chars of it.
    idx = _ORCH_SRC.find("activity stamp init")
    assert idx != -1, (
        "Could not find '[FATAL] activity stamp init failed ...' message"
    )
    window = _ORCH_SRC[idx : idx + 600]
    assert "HALTED_SILENT" in window, (
        "Stamp-init failure must escalate to HALTED_SILENT rather than "
        "silently proceed with stall detection disabled"
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
