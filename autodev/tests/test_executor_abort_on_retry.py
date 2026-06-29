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
        """The retry-start interrupt must run before the executor invoke and be guarded.

        The executor is invoked via ``self._invoke_executor(...)`` (which delivers
        any one-shot ``executor_retry_directive`` as the webhook message); the
        retry-start interrupt must still run before it when ``retries > 0``. The
        interrupt now routes through the consolidated ``_interrupt_agent_session``
        helper (liveness-gated steer + settle-wait) rather than a direct
        ``abort_agent_session`` call.
        """
        src = open(
            os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8"
        ).read()
        pattern = (
            r"if retries > 0:.*?_interrupt_agent_session\(.*?"
            r"webhook_status = self\._invoke_executor\("
        )
        assert re.search(
            pattern, src, re.DOTALL
        ), "the retry-start _interrupt_agent_session call must be guarded and run before executor invoke"

    def test_retry_start_interrupt_passes_correct_session_and_stamp(self):
        """The retry-start interrupt must target the PRIOR attempt's session key and the
        executor's activity stamp, with the skip-if-idle liveness gate and retry_start source.

        Guards the gap a bare 'the call exists' scrape leaves open (review finding): a typo in
        the stamp filename or the attempt-N session key would make the liveness pre-check read an
        unresolvable transcript and steer the wrong/no session, yet still pass a presence-only
        check. We pin the literal arguments in the retry-start window."""
        src = open(
            os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8"
        ).read()
        rs = src.find("if retries > 0:")
        assert rs != -1, "executor retry-start guard not found"
        window = src[rs : rs + 1200]
        assert 'role="executor"' in window
        assert "executor-attempt-{retries}" in window, (
            "retry-start must interrupt the PRIOR attempt (executor-attempt-{retries})"
        )
        assert '"executor_activity.stamp"' in window, (
            "retry-start must pass the executor activity stamp as stamp_path"
        )
        assert 'source="retry_start"' in window
        assert "skip_if_idle=True" in window, (
            "retry-start must liveness-gate the steer (skip a prior attempt that already ended)"
        )

    def test_abort_agent_session_exists_in_webhook_client(self):
        """abort_agent_session must be a callable exported from webhook_client."""
        assert callable(
            getattr(wc, "abort_agent_session", None)
        ), "webhook_client must export abort_agent_session as a callable"
