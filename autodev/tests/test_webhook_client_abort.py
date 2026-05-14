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
        ws = _make_ws_mock([HELLO_OK, ABORT_ABORTED])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "test-gateway-token",
            )
        assert result is True

    def test_connect_passes_authorization_header(self):
        """ws.connect must include Authorization: Bearer in HTTP upgrade headers."""
        ws = _make_ws_mock([HELLO_OK, ABORT_ABORTED])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            wc.abort_agent_session(
                "agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "my-gateway-token",
            )
        ws.connect.assert_called_once_with(
            "ws://127.0.0.1:18789/__openclaw__/ws",
            header={"Authorization": "Bearer my-gateway-token"},
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
        ws = _make_ws_mock([HELLO_OK, ABORT_NO_RUN])
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
        ws.recv.side_effect = [json.dumps(HELLO_OK), socket.timeout("timed out")]
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "any:key",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "test-gateway-token",
            )
        assert result is False

    def test_returns_false_on_auth_rejection(self):
        """Best-effort: gateway scope error must not raise, must return False."""
        ws = _make_ws_mock([HELLO_OK, ABORT_FAIL])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "any:key",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "bad-token",
            )
        assert result is False
