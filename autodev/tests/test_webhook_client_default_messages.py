"""Tests for Stage D: webhook_client default messages reference prd.md + verification.md.

The default messages are the fallback text used when the orchestrator does
not pass an explicit ``message`` argument to ``invoke_agent_webhook``. They
also serve as the canonical instruction surface for any code path that
hits the OpenClaw webhook without orchestrator-supplied context.

Planner / executor / reviewer must reference both ``prd.md`` and
``verification.md``. Escalation is intentionally unchanged — escalation's
inputs are ``phase_state.json`` and the output files; PRD/verification are
not on its read path.
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

    def test_escalation_default_requires_command_field(self):
        """The strengthened message must name the `command` field and the no-instruction STOP default,
        so the agent never writes a command-less escalation_output.done."""
        msg = self._escalation_msg()
        assert "command" in msg, f"escalation default must require a command field; got: {msg!r}"
        assert '"command": "STOP"' in msg, (
            f"escalation default must instruct {{'command': 'STOP'}} when no clear instruction; got: {msg!r}"
        )

    def test_escalation_default_enumerates_offerable_verbs(self):
        """The message must enumerate the six offerable verbs the agent may write."""
        msg = self._escalation_msg()
        for verb in ("RETRY", "RESET_PHASE", "RESET_EXECUTION", "RESET_REVIEWER", "PROCEED", "STOP"):
            assert verb in msg, f"escalation default must name offerable verb {verb}; got: {msg!r}"

    def test_escalation_default_omits_secret_menu_verbs(self):
        """SKIP and NUCLEAR_RESET are not offerable at invocation: SKIP is on-request-only, and
        NUCLEAR_RESET is surfaced conditionally (escalation_resets >= 3) per AGENTS.md, not statically."""
        msg = self._escalation_msg()
        assert "SKIP" not in msg, f"escalation default must NOT name SKIP (secret-menu); got: {msg!r}"
        assert "NUCLEAR_RESET" not in msg, (
            f"escalation default must NOT name NUCLEAR_RESET (cap-gated, surfaced by the agent); got: {msg!r}"
        )
