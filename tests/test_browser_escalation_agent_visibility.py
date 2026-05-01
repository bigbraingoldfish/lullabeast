"""Browser verification: escalation panel when current_agent is escalation (cursor browser MCP).

Prerequisites: UI server on ``127.0.0.1:18790`` (see ``ui/com.autodev.ui.plist`` or your config).

Steps for an agent using browser MCP (use **≥6s** waits so ``/api/state`` polling ~3s settles):

1. Navigate to the UI; ``browser_wait_for`` **6** s; ``browser_snapshot``.
2. On the server, set ``pipeline_state.json`` with ``pipeline_status``: ``QUEUE_HALTED``,
   ``current_agent``: ``escalation``, and ``phase_state.json`` with only ``escalation_resets``
   (no ``escalation_trigger_reason`` / ``escalation_message`` — the previously blind case).
3. ``browser_wait_for`` **6** s; ``browser_snapshot`` — confirm **Reset Phase** and **Stop Pipeline**
   appear in the escalation panel.
4. Click **Stop Pipeline**, confirm, ``browser_wait_for`` **6** s — no 409 toast; optional check for
   ``escalation_output.json`` under ``<project>/.autodev/pipeline/``.
5. Restore prior state files.

Skipped in CI; run manually when validating UI + API together.
"""

import pytest


@pytest.mark.skip(reason="Interactive browser MCP check — see module docstring")
def test_escalation_panel_visible_when_current_agent_escalation_in_browser():
    pytest.fail("skipped by default — run browser MCP steps from module docstring when needed")
