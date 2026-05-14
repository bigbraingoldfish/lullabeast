"""Executor abort on retry — source-level wiring tests."""

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for p in (PIPELINE_DIR, REPO_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import orchestrator as orch_mod  # noqa: E402
import webhook_client as wc  # noqa: E402


class TestExecutorAbortOnRetry:
    def test_abort_agent_session_imported_in_orchestrator(self):
        """orchestrator.py must import abort_agent_session from webhook_client."""
        src = open(
            os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8"
        ).read()
        assert (
            "abort_agent_session" in src
        ), "orchestrator.py must import abort_agent_session from webhook_client"

    def test_abort_called_before_invoke_when_retries_gt_zero(self):
        """abort_agent_session must appear before executor invoke_agent_webhook and be guarded."""
        src = open(
            os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8"
        ).read()
        pattern = (
            r"if retries > 0:.*?abort_agent_session\(.*?"
            r"webhook_status = invoke_agent_webhook\(\s*\"executor\""
        )
        assert re.search(
            pattern, src, re.DOTALL
        ), "abort_agent_session must be guarded and run before executor invoke"

    def test_abort_agent_session_exists_in_webhook_client(self):
        """abort_agent_session must be a callable exported from webhook_client."""
        assert callable(
            getattr(wc, "abort_agent_session", None)
        ), "webhook_client must export abort_agent_session as a callable"
