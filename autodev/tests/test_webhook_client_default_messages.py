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
