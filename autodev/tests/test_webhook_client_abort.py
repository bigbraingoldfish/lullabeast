"""abort_agent_session() — unit tests for the best-effort OpenClaw session abort."""

import json
import websocket as _ws_lib
from unittest.mock import MagicMock, call, patch

import webhook_client as wc


def _make_ws_mock(responses):
    """Return a mock WebSocket that yields JSON responses in sequence from recv()."""
    ws = MagicMock()
    ws.recv.side_effect = [json.dumps(r) for r in responses]
    return ws


# The OpenClaw gateway opens every WebSocket with an unsolicited
# ``connect.challenge`` event carrying a nonce.  The client must wait for
# this frame before sending the ``connect`` request — sending early
# causes the gateway to drop the frame and the handshake to time out.
# This was the live-gateway bug that made every abort_agent_session call
# return False under the original (single-roundtrip) implementation.
CONNECT_CHALLENGE = {
    "type": "event",
    "event": "connect.challenge",
    "payload": {
        "nonce": "abc-test-nonce-1234",
        "ts": 1779255357500,
    },
}

HELLO_OK = {
    "type": "res",
    "id": "1",
    "ok": True,
    "payload": {
        "type": "hello-ok",
        "protocol": 4,
        "server": {},
        "features": {},
        "snapshot": {},
        "auth": {"role": "operator", "scopes": []},
        "policy": {
            "maxPayload": 26214400,
            "maxBufferedBytes": 52428800,
            "tickIntervalMs": 15000,
        },
    },
}
ABORT_ABORTED = {
    "type": "res",
    "id": "2",
    "ok": True,
    "payload": {"ok": True, "abortedRunId": "r1", "status": "aborted"},
}
ABORT_NO_RUN = {
    "type": "res",
    "id": "2",
    "ok": True,
    "payload": {"ok": True, "abortedRunId": None, "status": "no-active-run"},
}
ABORT_FAIL = {
    "type": "res",
    "id": "2",
    "ok": False,
    "error": {"type": "forbidden", "message": "missing scope"},
}


class TestAbortAgentSession:
    def test_returns_true_on_aborted(self):
        """Happy path: session was running and got aborted."""
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, ABORT_ABORTED])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "test-gateway-token",
            )
        assert result is True

    def test_connect_passes_authorization_header(self):
        """ws.connect must include Authorization: Bearer in HTTP upgrade headers
        AND pass ``suppress_origin=True`` so Python's websocket-client lib does
        not auto-add an ``Origin`` header.

        Suppressing ``Origin`` is load-bearing: with it present, the OpenClaw
        gateway treats the connection as a browser request and rejects the
        trusted-loopback-backend path that grants ``operator.write`` to local
        clients.  Tracked in the live-fire diagnosis on the
        ``can-you-look-into-jiggly-hejlsberg`` plan.
        """
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, ABORT_ABORTED])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            wc.abort_agent_session(
                "agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "my-gateway-token",
            )
        ws.connect.assert_called_once_with(
            "ws://127.0.0.1:18789/__openclaw__/ws",
            header={"Authorization": "Bearer my-gateway-token"},
            suppress_origin=True,
        )

    def test_returns_false_on_401_unauthorized(self):
        """Best-effort: 401 on WebSocket upgrade must not raise, must return False."""
        ws = MagicMock()
        # The message must contain %s placeholders for status_code and status_message.
        ws.connect.side_effect = _ws_lib.WebSocketBadStatusException(
            "Handshake status %s %s", 401, "Unauthorized", {}
        )
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "wrong-token",
            )
        assert result is False

    def test_returns_true_on_no_active_run(self):
        """Session already finished — gateway returns no-active-run, still success."""
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, ABORT_NO_RUN])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "test-gateway-token",
            )
        assert result is True

    def test_returns_false_on_connection_error(self):
        """Best-effort: connection failure must not raise, must return False."""
        ws = MagicMock()
        ws.connect.side_effect = ConnectionError("refused")
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "test-gateway-token",
            )
        assert result is False

    def test_returns_false_on_timeout(self):
        """Best-effort: socket timeout on second recv must not raise, must return False."""
        import socket

        ws = MagicMock()
        ws.recv.side_effect = [
            json.dumps(CONNECT_CHALLENGE),
            json.dumps(HELLO_OK),
            socket.timeout("timed out"),
        ]
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "any:key",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "test-gateway-token",
            )
        assert result is False

    def test_returns_false_on_auth_rejection(self):
        """Best-effort: gateway scope error must not raise, must return False."""
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, ABORT_FAIL])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "any:key",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "bad-token",
            )
        assert result is False

    def test_waits_for_connect_challenge_before_sending_connect(self):
        """The OpenClaw gateway opens every WebSocket with an unsolicited
        ``connect.challenge`` event.  The client must receive that frame
        **before** sending ``connect`` — otherwise the gateway treats the
        connect frame as arriving in the wrong state and the handshake
        never completes (every abort returns False).

        Regression test for the bug that made every live abort fail:
        attempt-1 sent ``connect`` immediately, then read the first
        frame and saw the challenge event (which has ``ok`` undefined),
        and treated that as a handshake rejection.

        We assert ordering by checking ``ws.send`` was first called only
        after ``ws.recv`` returned the challenge frame.
        """
        ws = MagicMock()
        ws.recv.side_effect = [
            json.dumps(CONNECT_CHALLENGE),
            json.dumps(HELLO_OK),
            json.dumps(ABORT_ABORTED),
        ]
        call_log = []
        ws.send.side_effect = lambda frame: call_log.append(("send", frame))
        ws.recv.side_effect = (
            lambda: call_log.append(("recv",))
            or [
                json.dumps(CONNECT_CHALLENGE),
                json.dumps(HELLO_OK),
                json.dumps(ABORT_ABORTED),
            ][len([c for c in call_log if c[0] == "recv"]) - 1]
        )
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "tok",
            )
        assert result is True
        # First action against the socket must be a recv (to read the
        # challenge), not a send.  This is the protocol invariant the
        # OpenClaw gateway enforces.
        kinds = [c[0] for c in call_log]
        assert kinds[0] == "recv", (
            f"abort_agent_session must read the connect.challenge frame "
            f"before sending the connect request; actual order: {kinds}"
        )

    def test_treats_challenge_event_as_event_not_rejection(self):
        """A bare ``connect.challenge`` event (no ``ok`` key) must not be
        treated as a failed ``hello-ok`` response.  The original bug
        logged ``[ABORT] Gateway handshake rejected`` and returned False
        when the gateway was actually sending a normal challenge frame.
        """
        ws = _make_ws_mock([
            CONNECT_CHALLENGE,
            HELLO_OK,
            ABORT_ABORTED,
        ])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "tok",
            )
        assert result is True, (
            "connect.challenge is the normal first frame from the gateway; "
            "abort_agent_session must consume it and proceed, not treat it "
            "as a handshake rejection"
        )

    def test_returns_false_when_first_frame_is_not_challenge(self):
        """If the very first frame is somehow not a ``connect.challenge``
        event, that is a real protocol violation — return False (logged
        as handshake rejection) rather than blindly proceeding."""
        weird_first_frame = {
            "type": "event",
            "event": "something.unexpected",
            "payload": {},
        }
        ws = _make_ws_mock([weird_first_frame])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "any:key",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "tok",
            )
        assert result is False
