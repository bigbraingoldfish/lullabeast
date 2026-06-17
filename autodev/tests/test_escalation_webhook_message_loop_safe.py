"""Escalation webhook-message tests — absolute reads + inline loop guard.

``Orchestrator._build_escalation_webhook_message`` is the per-invocation framing
the escalation agent receives. It must (1) point diagnostic READS at the resolved
ABSOLUTE artifacts path (symlink-move-immune — a queue advance can repoint the
``pipeline-project`` symlink mid-turn), and (2) carry the read-once /
proceed-on-missing / no-loop guard inline, so the guard reaches the agent even if
it ignores its standing AGENTS.md. The live failure was a 222-read ENOENT loop;
this message is one of the two surfaces (with AGENTS.md) that must agree.

Built via ``Orchestrator.__new__`` (the method only needs ``PROJECT_ARTIFACTS_DIR``
on the no-reply-token path), monkeypatching the module global like the other
orchestrator unit tests.
"""

import os

import orchestrator as orch_mod  # sys.path wired by conftest


def _msg(monkeypatch, tmp_path):
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    return orch._build_escalation_webhook_message()


def test_message_points_reads_at_absolute_path(monkeypatch, tmp_path):
    """Regression guard: reads are pointed at the resolved ABSOLUTE artifacts
    path, never a workspace-relative one. Already true; locked so a future edit
    can't silently revert to a volatile symlink-relative read path."""
    msg = _msg(monkeypatch, tmp_path)
    assert os.path.realpath(str(tmp_path)) in msg, (
        "escalation message must reference the absolute realpath of "
        "PROJECT_ARTIFACTS_DIR for diagnostic reads"
    )


def test_message_marks_required_vs_optional(monkeypatch, tmp_path):
    """phase_state.json = required/always-present; *_output.json = optional.
    Keeps the per-invocation framing aligned with the AGENTS.md contract so the
    agent gets one consistent story about which files it must vs may read."""
    msg = _msg(monkeypatch, tmp_path).lower()
    assert "phase_state.json" in msg
    assert "required" in msg or "always present" in msg
    assert "optional" in msg


def test_message_has_anti_loop_guidance(monkeypatch, tmp_path):
    """The read-once / do-not-retry-missing / proceed guard must be inline in the
    message. Absent today → this fails until the fix lands; it is the inline
    backstop for an agent that ignores its standing doc."""
    msg = _msg(monkeypatch, tmp_path).lower()
    assert "once" in msg, "message must say read each diagnostic at most once"
    assert "do not retry" in msg or "do not re-read" in msg, (
        "message must tell the agent not to retry/re-read a missing file"
    )
    assert "proceed" in msg, (
        "message must tell the agent to proceed and write the summary from what it read"
    )
