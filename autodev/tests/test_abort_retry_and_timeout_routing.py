"""Tests for abort retry-loop, timeout routing, and soft verify-failed.

Background — the CORE-E6 cascade in the wild
============================================

When ``poll_for_sentinel`` returned ``PollResult(reason="timeout")`` (the
45-min infrastructure backstop), the orchestrator's stall-outcome guard
only matched ``stalled``/``no_first_activity`` and skipped the abort
helper entirely.  The inline retry-start abort block was the only thing
that ran, and on a single WS handshake failure it returned ``False``
and the orchestrator launched attempt N+1 on top of the still-streaming
attempt N — collisions in stamp writes hid stall detection, the new
attempt timed out at 45 min, repeat, repeat, until retries exhausted.

The three changes pinned here
-----------------------------

1. **Abort retry loop (3 attempts) in ``abort_agent_session``.**
   A single WS handshake hiccup on a busy gateway should not be the
   reason a still-streaming session is allowed to keep running.  The
   helper now tries up to 3 times with a short backoff before
   declaring failure.

2. **Timeout routes through ``_handle_stall_outcome``.**
   When ``poll_for_sentinel`` returns ``reason="timeout"`` we still want
   the abort+verify path to run before launching the next attempt.

3. **Verify-failed soft-continues (no more HALTED_SILENT).**
   The user's call: a halted pipeline needs human intervention,
   whereas 90%+ of long runs eventually resolve, so letting the next
   attempt start is the better expected-value choice.  We still emit
   ``abort_verify_failed`` so the activity feed shows the situation
   in red, but the orchestrator does not transition the state.
"""

import json
import os
import re
import sys
import socket

import pytest
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402
import webhook_client as wc  # noqa: E402


_ORCH_SRC = open(
    os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8"
).read()


# ---------------------------------------------------------------------------
# Helpers — minimal WS hello/abort frames mirroring webhook_client tests
# ---------------------------------------------------------------------------

HELLO_OK = {
    "type": "res",
    "id": "1",
    "ok": True,
    "payload": {"type": "hello-ok", "protocol": 4},
}
ABORT_ABORTED = {
    "type": "res",
    "id": "2",
    "ok": True,
    "payload": {"ok": True, "status": "aborted"},
}


CONNECT_CHALLENGE = {
    "type": "event",
    "event": "connect.challenge",
    "payload": {"nonce": "test-nonce", "ts": 1779255357500},
}


def _ws_mock(responses):
    """Build a mock WS that auto-prepends a connect.challenge frame.

    The OpenClaw gateway always emits ``connect.challenge`` as the first
    frame; ``abort_agent_session`` reads it before sending ``connect``.
    Tests above this helper only care about the ``hello-ok`` / abort
    response sequence, so we splice the challenge in here.
    """
    ws = MagicMock()
    ws.recv.side_effect = [json.dumps(CONNECT_CHALLENGE)] + [
        json.dumps(r) for r in responses
    ]
    return ws


# ---------------------------------------------------------------------------
# (1) abort_agent_session retry-loop behaviour
# ---------------------------------------------------------------------------


class TestAbortRetryLoop:
    def test_succeeds_first_try_no_retry(self):
        """Happy path: a single successful attempt does not invoke a retry."""
        ws = _ws_mock([HELLO_OK, ABORT_ABORTED])
        with patch.object(wc.websocket, "WebSocket", return_value=ws) as ctor:
            result = wc.abort_agent_session(
                "agent:executor:any:key",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "tok",
            )
        assert result is True
        assert ctor.call_count == 1, (
            "First-try success must not allocate additional WebSocket objects"
        )

    def test_retries_up_to_three_times_on_handshake_failure(self):
        """Two transient ConnectionErrors followed by a success must return True."""
        good_ws = _ws_mock([HELLO_OK, ABORT_ABORTED])
        bad_ws_1 = MagicMock()
        bad_ws_1.connect.side_effect = ConnectionError("refused")
        bad_ws_2 = MagicMock()
        bad_ws_2.connect.side_effect = ConnectionError("refused")
        # Patch a no-op sleep so the test is fast.
        with patch.object(wc.websocket, "WebSocket",
                          side_effect=[bad_ws_1, bad_ws_2, good_ws]) as ctor, \
             patch.object(wc.time, "sleep") as sleeper:
            result = wc.abort_agent_session(
                "agent:executor:any:key",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "tok",
            )
        assert result is True, (
            "abort_agent_session must retry transient handshake failures up to 3 times"
        )
        assert ctor.call_count == 3
        assert sleeper.call_count >= 2, (
            "There must be a backoff sleep between retry attempts"
        )

    def test_returns_false_after_three_failed_attempts(self):
        """All 3 attempts fail → return False (caller may continue best-effort)."""
        bad1 = MagicMock(); bad1.connect.side_effect = ConnectionError("x")
        bad2 = MagicMock(); bad2.connect.side_effect = ConnectionError("x")
        bad3 = MagicMock(); bad3.connect.side_effect = ConnectionError("x")
        with patch.object(wc.websocket, "WebSocket",
                          side_effect=[bad1, bad2, bad3]) as ctor, \
             patch.object(wc.time, "sleep"):
            result = wc.abort_agent_session(
                "agent:executor:any:key",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "tok",
            )
        assert result is False
        assert ctor.call_count == 3, (
            "abort_agent_session must attempt exactly 3 times before giving up"
        )

    def test_does_not_retry_on_explicit_unexpected_response(self):
        """If the gateway returns a coherent error response (not a transport
        failure) we still surface False but should not infinitely loop —
        bound by the same 3-attempt ceiling."""
        ws1 = _ws_mock([HELLO_OK, {"type": "res", "id": "2", "ok": False,
                                    "error": {"type": "forbidden"}}])
        ws2 = _ws_mock([HELLO_OK, {"type": "res", "id": "2", "ok": False,
                                    "error": {"type": "forbidden"}}])
        ws3 = _ws_mock([HELLO_OK, {"type": "res", "id": "2", "ok": False,
                                    "error": {"type": "forbidden"}}])
        with patch.object(wc.websocket, "WebSocket",
                          side_effect=[ws1, ws2, ws3]) as ctor, \
             patch.object(wc.time, "sleep"):
            result = wc.abort_agent_session(
                "agent:executor:any:key",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "tok",
            )
        assert result is False
        assert ctor.call_count <= 3


# ---------------------------------------------------------------------------
# (2) timeout reason routes through _handle_stall_outcome at all 3 poll sites
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
def test_timeout_reason_routes_through_stall_outcome(agent):
    """All three poll sites must include ``"timeout"`` in the set of
    reasons that trigger ``_handle_stall_outcome``.  Before this fix the
    set was only ``stalled``/``no_first_activity``, so a 45-min
    infrastructure backstop silently skipped abort+verify."""
    marker = f"stall_detection_path=_{agent}_stamp"
    idx = _ORCH_SRC.find(marker)
    assert idx != -1, f"Could not locate {agent} poll site"
    # Look at the post-poll window where the stall-outcome guard lives.
    window = _ORCH_SRC[idx : idx + 3500]
    # Find the stall_outcome call and check the reason tuple just above it
    # includes "timeout".
    call_idx = window.find("_handle_stall_outcome")
    assert call_idx != -1, f"{agent} site missing _handle_stall_outcome call"
    pre = window[max(0, call_idx - 400) : call_idx]
    assert '"timeout"' in pre or "'timeout'" in pre, (
        f"{agent} poll site must route reason='timeout' through "
        "_handle_stall_outcome so the 45-min infra backstop also gets "
        "abort+verify before the next attempt is launched"
    )


# ---------------------------------------------------------------------------
# (3) Verify-failed soft-continues (no HALTED_SILENT in stall outcome or retry-start)
# ---------------------------------------------------------------------------


def test_handle_stall_outcome_does_not_halt_on_verify_failure():
    """Per the user's call: a verify failure should emit the
    ``abort_verify_failed`` event for transparency in the activity feed
    but must NOT transition to ``HALTED_SILENT`` — letting the next
    attempt run is the better expected-value choice than a hung state
    that requires human intervention."""
    # _handle_stall_outcome now delegates to the consolidated _interrupt_agent_session
    # helper, which owns the abort_verify_failed emission. Assert the delegation, that the
    # event lives in the helper, and that neither path halts on verify failure.
    stall_idx = _ORCH_SRC.find("def _handle_stall_outcome")
    assert stall_idx != -1
    stall_next = _ORCH_SRC.find("\n    def ", stall_idx + 1)
    stall_body = _ORCH_SRC[stall_idx : stall_next if stall_next != -1 else stall_idx + 5000]
    assert "_interrupt_agent_session(" in stall_body, (
        "_handle_stall_outcome must delegate to _interrupt_agent_session"
    )

    helper_idx = _ORCH_SRC.find("def _interrupt_agent_session")
    assert helper_idx != -1
    helper_next = _ORCH_SRC.find("\n    def ", helper_idx + 1)
    helper_body = _ORCH_SRC[helper_idx : helper_next if helper_next != -1 else helper_idx + 6000]
    # The event must still be emitted (now from the consolidated helper).
    assert "abort_verify_failed" in helper_body, (
        "abort_verify_failed event must remain — it powers the activity feed"
    )
    # No transition_state("HALTED_SILENT", ...) tied to verify failure in either method.
    for body in (stall_body, helper_body):
        assert not re.search(
            r'transition_state\(\s*["\']HALTED_SILENT["\']', body
        ), (
            "neither _handle_stall_outcome nor _interrupt_agent_session may call "
            "transition_state(\"HALTED_SILENT\", ...) on verify failure — "
            "soft-continue and emit the event instead"
        )


def test_retry_start_abort_does_not_halt_on_verify_failure():
    """Same contract at the executor retry-start abort block."""
    # Anchor on the unique [ABORT] print at the retry-start site.
    idx = _ORCH_SRC.find("prior_attempt=")
    assert idx != -1
    window = _ORCH_SRC[max(0, idx - 800) : idx + 2500]
    assert "abort_verify_failed" in window, (
        "abort_verify_failed event must remain at the retry-start site"
    )
    # No transition_state("HALTED_SILENT", ...) call in retry-start abort window.
    assert not re.search(
        r'transition_state\(\s*["\']HALTED_SILENT["\']', window
    ), (
        "retry-start abort block must no longer call "
        "transition_state(\"HALTED_SILENT\", ...) on verify failure"
    )


def test_handle_stall_outcome_always_returns_true():
    """After the soft-continue change, ``_handle_stall_outcome`` should
    have no codepath that returns False — every outcome (abort ok+verify
    ok, abort ok+verify failed, abort failed) lets the caller proceed."""
    method_idx = _ORCH_SRC.find("def _handle_stall_outcome")
    assert method_idx != -1
    next_def = _ORCH_SRC.find("\n    def ", method_idx + 1)
    method_body = _ORCH_SRC[method_idx : next_def if next_def != -1 else method_idx + 5000]
    # The only returns inside the method should be `return True`.
    returns = re.findall(r"^\s+return\s+(\S+)", method_body, re.MULTILINE)
    assert returns, "Method must have at least one return"
    for r in returns:
        assert r.startswith("True"), (
            f"_handle_stall_outcome must only ever return True after the "
            f"soft-continue change; found `return {r}`"
        )
