"""Section 0 — abort_agent_session observability and verification.

These tests cover the prerequisite hardening that the rest of the
stall-detection work depends on:

* The orchestrator must check and log the return value of
  ``abort_agent_session`` at every call site.
* ``load_config()`` must fail loudly if ``gateway_token`` or
  ``gateway_ws_url`` is missing/empty, rather than discovering the gap
  the first time an abort fires.
* A ``verify_session_stopped`` helper must exist in ``webhook_client`` so
  the orchestrator can confirm an acknowledged abort actually stopped
  the agent.
* The orchestrator must escalate to ``HALTED_SILENT`` rather than
  launching the next attempt when verification reveals the prior
  session is still streaming.
* ``logging.*`` output from helper modules must reach the same captured
  stream as ``print()`` output so operators see abort outcomes.

Tests are a mix of source-level wiring checks (for the orchestrator
patterns, mirroring ``test_executor_abort_on_retry.py``) and direct
unit tests for new helpers.
"""

import io
import json
import logging
import os
import re
import sys
import time
import threading

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402
import webhook_client as wc  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ORCH_SRC = open(
    os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8"
).read()


def _make_config(tmp_path_str, gateway=None, hooks=None):
    """Write a minimal openclaw.json and return its path."""
    cfg = {
        "hooks": hooks if hooks is not None else {"token": "hooks-tok"},
        "gateway": gateway
        if gateway is not None
        else {"port": 18789, "auth": {"mode": "token", "token": "gw-tok"}},
    }
    p = os.path.join(tmp_path_str, "openclaw.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return p


# ---------------------------------------------------------------------------
# Tests A, B — orchestrator must check and log abort return value
# ---------------------------------------------------------------------------


class TestAbortReturnValueLogged:
    def test_orchestrator_logs_abort_result_ok(self):
        """The retry-start abort site must capture the return value and emit
        a ``[ABORT] result=ok ...`` log line on success.

        We check the source for the canonical pattern. The matching is
        deliberately loose on whitespace so reformatting does not break
        the test, but the structural elements (capture variable + result
        log line) must be present.
        """
        # Capture variable assigned from abort_agent_session call
        capture_pattern = re.compile(
            r"\b(\w+)\s*=\s*abort_agent_session\(",
        )
        matches = capture_pattern.findall(_ORCH_SRC)
        assert matches, (
            "orchestrator.py must assign abort_agent_session() return value "
            "to a variable (was previously discarded fire-and-forget)"
        )
        # Result-log line referencing the captured variable and the "ok" branch
        # plus a session_key field for diagnostics.
        ok_pattern = re.compile(
            r"\[ABORT\]\s+result=.*ok.*session_key=",
            re.DOTALL,
        )
        assert ok_pattern.search(_ORCH_SRC), (
            "orchestrator.py must emit a '[ABORT] result=ok session_key=...' "
            "log line on successful abort so operators see outcomes"
        )

    def test_orchestrator_logs_abort_result_failed(self):
        """The same site must emit '[ABORT] result=FAILED ...' on failure."""
        failed_pattern = re.compile(
            r"\[ABORT\]\s+result=.*FAILED.*session_key=",
            re.DOTALL,
        )
        assert failed_pattern.search(_ORCH_SRC), (
            "orchestrator.py must emit a '[ABORT] result=FAILED session_key=...' "
            "log line when abort_agent_session returns False"
        )


# ---------------------------------------------------------------------------
# Tests C, D — load_config must fail fast on missing gateway config
# ---------------------------------------------------------------------------


class TestGatewayConfigPreValidation:
    def test_load_config_fails_fast_if_gateway_token_missing(
        self, tmp_path, monkeypatch, capsys
    ):
        """An empty gateway.auth.token must cause load_config() to exit
        with a clear error rather than silently producing
        ``gateway_token == ''``."""
        cfg_path = _make_config(
            str(tmp_path),
            gateway={"port": 18789, "auth": {"mode": "token", "token": ""}},
        )
        monkeypatch.setattr(orch_mod, "CONFIG_FILE", cfg_path)
        orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
        orch.skill_manager = None
        with pytest.raises(SystemExit):
            orch.load_config()
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "gateway" in combined.lower() and "token" in combined.lower(), (
            "load_config() must print a diagnostic mentioning the missing "
            "gateway token before exiting"
        )

    def test_load_config_fails_fast_if_gateway_section_missing(
        self, tmp_path, monkeypatch, capsys
    ):
        """An entirely missing gateway section is equivalent to missing token —
        treat it as fatal so the operator notices before the first abort fires."""
        cfg_path = _make_config(str(tmp_path), gateway={})  # no auth at all
        monkeypatch.setattr(orch_mod, "CONFIG_FILE", cfg_path)
        orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
        orch.skill_manager = None
        with pytest.raises(SystemExit):
            orch.load_config()
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "gateway" in combined.lower(), (
            "load_config() must print a gateway-related diagnostic when the "
            "gateway section is empty"
        )


# ---------------------------------------------------------------------------
# Tests E, F — verify_session_stopped helper
# ---------------------------------------------------------------------------


class TestVerifySessionStopped:
    def test_verify_session_stopped_returns_true_when_stamp_static(self, tmp_path):
        """If the activity stamp's mtime does not advance during settle, the
        session is genuinely stopped — return True."""
        stamp = tmp_path / "executor_activity.stamp"
        stamp.write_text("")
        assert callable(getattr(wc, "verify_session_stopped", None)), (
            "webhook_client must export verify_session_stopped"
        )
        assert wc.verify_session_stopped(str(stamp), settle_seconds=0.3) is True

    def test_verify_session_stopped_returns_false_when_stamp_advances(self, tmp_path):
        """If something keeps touching the stamp during settle, the session
        is still active — return False."""
        stamp = tmp_path / "executor_activity.stamp"
        stamp.write_text("")
        stop_flag = threading.Event()

        def _toucher():
            while not stop_flag.is_set():
                try:
                    os.utime(str(stamp), None)
                except OSError:
                    return
                time.sleep(0.05)

        t = threading.Thread(target=_toucher, daemon=True)
        t.start()
        try:
            result = wc.verify_session_stopped(str(stamp), settle_seconds=0.4)
        finally:
            stop_flag.set()
            t.join(timeout=1.0)
        assert result is False, (
            "verify_session_stopped must return False when the stamp mtime "
            "advances during the settle window"
        )

    def test_verify_session_stopped_returns_true_when_stamp_missing(self, tmp_path):
        """A missing stamp cannot be 'active' — return True (no false stalls)."""
        missing = tmp_path / "nope.stamp"
        assert wc.verify_session_stopped(str(missing), settle_seconds=0.1) is True


# ---------------------------------------------------------------------------
# Test G — verify-failed → HALTED_SILENT wiring
# ---------------------------------------------------------------------------


class TestVerifyFailureSoftContinue:
    """Per operator policy (post-CORE-E6 review): a verify-failed outcome
    must NOT halt the pipeline.  90%+ of long runs eventually resolve on
    retry, whereas a HALTED_SILENT transition always requires human
    intervention.  We keep the ``[ABORT][VERIFY_FAILED]`` print and
    ``abort_verify_failed`` event for activity-feed transparency, but
    the orchestrator continues with the next attempt.
    """

    def test_orchestrator_logs_verify_failed_marker(self):
        """The diagnostic print line must remain so operators can grep logs."""
        assert "verify_session_stopped" in _ORCH_SRC, (
            "orchestrator.py must call verify_session_stopped after abort"
        )
        assert "[ABORT][VERIFY_FAILED]" in _ORCH_SRC, (
            "[ABORT][VERIFY_FAILED] marker must remain for log grep/diagnostics"
        )

    def test_orchestrator_does_not_halt_on_verify_failure(self):
        """No code path may transition to HALTED_SILENT off the back of a
        verify failure — soft-continue is the new contract.  We forbid
        the actual transition_state call (docstring mentions are fine)."""
        for marker in ("[ABORT][VERIFY_FAILED]",):
            idx = 0
            while True:
                idx = _ORCH_SRC.find(marker, idx)
                if idx == -1:
                    break
                window = _ORCH_SRC[max(0, idx - 300) : idx + 1500]
                assert not re.search(
                    r'transition_state\(\s*["\']HALTED_SILENT["\']', window
                ), (
                    f"Found transition_state(\"HALTED_SILENT\", ...) near a "
                    f"{marker} block; verify failure must soft-continue, not halt"
                )
                idx += len(marker)


# ---------------------------------------------------------------------------
# Test H — logging.basicConfig routes logger output to stdout
# ---------------------------------------------------------------------------


class TestLoggingRoutedToStdout:
    def test_orchestrator_module_exposes_stdout_logging_helper(self):
        """orchestrator.py must define a helper that attaches an INFO-level
        StreamHandler routed at the *current* sys.stdout, idempotently.

        We check the helper exists and is callable (it runs at module import
        time but must remain available so it can be invoked again after
        e.g. ``capsys`` swaps stdout, or under test isolation).
        """
        helper = getattr(orch_mod, "_ensure_stdout_logging", None)
        assert callable(helper), (
            "orchestrator.py must define _ensure_stdout_logging() so "
            "logging.* calls from webhook_client (e.g. abort_agent_session's "
            "success/failure lines) reach the operator-facing stdout stream"
        )

    def test_logging_info_from_helper_module_reaches_stdout(self, capsys):
        """A ``logging.info()`` call from a helper module must land in
        captured stdout when ``_ensure_stdout_logging()`` is active under
        the current sys.stdout.

        This is the behavioural counterpart to the structural test above:
        even if pytest's ``capsys`` swaps ``sys.stdout`` per-test, calling
        the helper re-binds the handler to the live stream so log output
        is visible to the test runner (and, in production, to the
        operator's tail of /tmp/orchestrator.log).
        """
        # Re-bind the handler to the test's current sys.stdout (per-test
        # capsys swap means the import-time handler points at a different
        # object).  In production this is a no-op idempotent re-attach.
        orch_mod._ensure_stdout_logging()
        logging.info("autodev-stall-test-canary-7e3f")
        captured = capsys.readouterr()
        assert "autodev-stall-test-canary-7e3f" in captured.out, (
            "logging.info() output from helper modules must appear on stdout "
            "so abort outcomes (which use logging.*) are visible in the "
            "captured orchestrator log; instead it went to: "
            f"out={captured.out!r} err={captured.err!r}"
        )
