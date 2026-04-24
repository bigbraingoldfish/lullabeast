"""Browser verification of ElapsedTimer (cursor browser MCP).

Run manually when the UI server is up: start ``uvicorn ui.server:app --host 127.0.0.1 --port 18790``,
then use browser MCP: navigate, ``browser_wait_for`` 3–5s, ``browser_snapshot``, assert
``data-testid=elapsed-timer`` text includes ``orchestrator offline`` when /api/state reports
dead orchestrator mid-flight.
"""

import pytest


@pytest.mark.skip(reason="Interactive browser MCP check — see module docstring")
def test_elapsed_timer_orchestrator_offline_visible_in_browser():
    pytest.fail("skipped by default — run browser MCP steps from module docstring when needed")
