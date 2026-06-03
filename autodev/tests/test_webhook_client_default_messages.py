"""Tests for Stage D: webhook_client default messages reference prd.md + verification.md.

The default messages are the fallback text used when the orchestrator does
not pass an explicit ``message`` argument to ``invoke_agent_webhook``. They
also serve as the canonical instruction surface for any code path that
hits the OpenClaw webhook without orchestrator-supplied context.

Planner / executor / reviewer must reference both ``prd.md`` and
``verification.md``. Escalation's inputs are ``phase_state.json`` and the output
files; PRD/verification are not on its read path. As of F13 the escalation
message frames the webhook as a TRUSTED control invocation and instructs
NOTIFY-only — the agent must not refuse the orchestrator's own webhook as
"untrusted" and must not write a default ``escalation_output`` command; the
operator answers asynchronously from the dashboard.
"""

import inspect
import re

import pytest

import autodev.pipeline.webhook_client as wc_mod


def _default_messages_source() -> str:
    """Source code of invoke_agent_webhook (where default_messages lives)."""
    return inspect.getsource(wc_mod.invoke_agent_webhook)


class TestDefaultMessageReferences:

    def test_planner_default_references_prd_and_verification(self):
        src = _default_messages_source()
        # Extract the planner block from the default_messages dict.
        match = re.search(
            r'"planner"\s*:\s*\((.*?)\)\s*,',
            src,
            re.DOTALL,
        )
        assert match, "Could not locate planner default message"
        planner_msg = match.group(1)
        assert "prd.md" in planner_msg, (
            f"planner default must reference prd.md; got: {planner_msg!r}"
        )
        assert "verification.md" in planner_msg, (
            f"planner default must reference verification.md; got: {planner_msg!r}"
        )

    def test_executor_default_references_prd_and_verification(self):
        src = _default_messages_source()
        match = re.search(
            r'"executor"\s*:\s*\((.*?)\)\s*,',
            src,
            re.DOTALL,
        )
        assert match, "Could not locate executor default message"
        executor_msg = match.group(1)
        assert "prd.md" in executor_msg
        assert "verification.md" in executor_msg

    def test_reviewer_default_references_prd_and_verification(self):
        src = _default_messages_source()
        match = re.search(
            r'"reviewer"\s*:\s*\((.*?)\)\s*,',
            src,
            re.DOTALL,
        )
        assert match, "Could not locate reviewer default message"
        reviewer_msg = match.group(1)
        assert "prd.md" in reviewer_msg
        assert "verification.md" in reviewer_msg

    def test_escalation_default_does_NOT_mention_verification(self):
        """Regression guard: escalation message stays focused on phase_state.json."""
        src = _default_messages_source()
        match = re.search(
            r'"escalation"\s*:\s*\((.*?)\)\s*,',
            src,
            re.DOTALL,
        )
        assert match, "Could not locate escalation default message"
        escalation_msg = match.group(1)
        # Escalation's read path is phase_state.json and the output files.
        # Adding prd.md / verification.md here would shift its scope beyond
        # what its IDENTITY.md authorizes.
        assert "verification.md" not in escalation_msg, (
            "Escalation default should not reference verification.md — "
            "escalation's input is phase_state.json + output files."
        )

    def _escalation_msg(self):
        src = _default_messages_source()
        match = re.search(r'"escalation"\s*:\s*\((.*?)\)\s*,', src, re.DOTALL)
        assert match, "Could not locate escalation default message"
        return match.group(1)

    def test_escalation_default_frames_webhook_as_trusted(self):
        """F13: the message must frame the escalation webhook as a TRUSTED control invocation so
        the agent does not refuse the orchestrator's own webhook as 'untrusted'/prompt-injection
        (the live failure mode). Replaces the prior 'enumerate offerable verbs' contract — under
        notify-only the agent no longer writes a command."""
        msg = self._escalation_msg()
        assert "TRUSTED" in msg, f"escalation default must frame the webhook as trusted; got: {msg!r}"
        low = msg.lower()
        assert "notify" in low, f"escalation default must instruct the agent to NOTIFY the operator; got: {msg!r}"

    def test_escalation_default_is_notify_only_no_default_command(self):
        """F13: notify-only — the agent must NOT be told to write a default escalation_output
        command (the operator answers from the dashboard). The prior '{"command": "STOP"}' default
        prematurely halted the pipeline, and 'write your assessment to escalation_output' is the
        instruction the agent (correctly distrusting it as untrusted) refused."""
        msg = self._escalation_msg()
        assert '"command": "STOP"' not in msg, (
            f"escalation default must NOT instruct a default STOP command (notify-only); got: {msg!r}"
        )
        low = msg.lower()
        assert "do not write escalation_output" in low or "not write escalation_output" in low, (
            f"escalation default must tell the agent NOT to write escalation_output (notify-only); got: {msg!r}"
        )

    def test_escalation_default_omits_secret_menu_verbs(self):
        """SKIP and NUCLEAR_RESET must never be named in the static message (SKIP is
        on-request-only; NUCLEAR_RESET is cap-gated and surfaced by the agent). Under F13's
        notify-only message no resume verbs are listed at all, which satisfies this."""
        msg = self._escalation_msg()
        assert "SKIP" not in msg, f"escalation default must NOT name SKIP (secret-menu); got: {msg!r}"
        assert "NUCLEAR_RESET" not in msg, (
            f"escalation default must NOT name NUCLEAR_RESET (cap-gated, surfaced by the agent); got: {msg!r}"
        )
